"""Uso de pacote pelo guest RHEL: leitura somente-leitura, sem root e sem executar binário.

Roda com o usuário de coleta, sem become. Fontes permitidas:
- systemctl list-units/show: metadados de unidade;
- rpm -qf, rpm -q --requires, rpm -q --whatprovides: banco RPM, só leitura;
- rpm -q, rpm -q --provides, rpm -ql, rpm -q --whatrequires: banco RPM, só leitura, só para os
  pacotes que o request da origem A pede em ``reverse_dependency_packages``;
- uname -r: kernel em execução.

Não lê /proc/<pid>/exe nem /proc/<pid>/maps de outro usuário, não usa ldd (ele executa
o loader) e não executa nenhum binário do produto. Cada comando tem tempo e bytes limitados.

Escopo declarado da medição (``coverage.scope``): unidades de serviço em execução, timers e
sockets ativos (``service``, ``timer``, ``socket``) e dependências RPM até a profundidade
``MAX_DEPTH`` a partir dos pacotes donos dessas unidades. A profundidade é o escopo, não um
corte: pacote além dela não é medido, e isso não torna a cobertura ``partial``. Por isso
``complete`` quer dizer "medido dentro deste escopo". Ausência de uso só vale dentro dele;
fora dele a sonda não afirma nada. Não cobre processo sem unidade, script manual nem biblioteca
carregada sem requisito declarado no RPM: é o risco de falso ``absent`` declarado no PR.

Corte que compromete a medição (limite de arestas, de nós, de tempo, de saída; nome ou caminho
que a sonda não representa; kernel sem pacote correspondente) aparece em ``coverage.reasons`` e
torna a cobertura ``partial``. Nenhum corte descarta dado em silêncio.

Dependentes reversos (``reverse_dependencies``) só existem quando o request os pede: para cada
pacote, quem o requer pelo nome, por cada capacidade que ele fornece (soname incluído) e por cada
arquivo dele. Pacote que não coube na medição fica ``unmeasured`` com o motivo, nunca "sem dependente".
"""
import collections
import datetime
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

SCHEMA = "samurai.rhel_package_usage_observation/v1"
LIMIT = 16384
MAX_UNITS = 64
MAX_DEPTH = 2
MAX_NODES = 300
TIME_BUDGET_S = 60
# Acima do timeout de 8 s de command(): o SIGKILL do grupo já cortou o que obedece ao sinal.
JOIN_TIMEOUT_S = 10
# N5 #81: cap 500 cortou em 75 de 125 nós. Depth 2 estimado ~730 pares; 1200 cobre até 13 pares/nó nos restantes.
EDGE_CAP = 1200
KERNEL_NAMES = ("kernel", "kernel-core", "kernel-modules")
UNIT_KINDS = ("service", "timer", "socket")

# Primeiro caractere alfanumérico: nome que começa com "-" nunca vira opção de systemctl.
UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@_.:-]{0,127}\.(service|timer|socket)$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@_.:-]{0,127}\.service$")
MODEL_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.:-]{0,126})@\.service$")
INSTANCE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.:-]{0,126})@[A-Za-z0-9][A-Za-z0-9_.:-]{0,126}\.service$")
MODEL_INSTANCE = "samurai-probe-model"
PATH_RE = re.compile(r"^/[A-Za-z0-9._/+@-]{1,255}$")
EXEC_PATH_RE = re.compile(r"path=([^ ;}]+)")
RELEASE_RE = re.compile(r"^[0-9][A-Za-z0-9_.+~-]{0,127}$")
PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
SKIPPED_REQUIREMENTS = ("rpmlib(", "config(")
# Mensagem do rpm -qf para binário sem dono. O rpmlog emite NOTICE no stdout por padrão; esta forma é defensiva.
UNOWNED_RE = re.compile(r"file .+ is not owned by any package")
MAX_REVERSE_PACKAGES = 32
# Nome, capacidades e arquivos de um pacote numa só consulta; acima disso o pacote fica não medido.
MAX_REVERSE_ARGUMENTS = 512
REVERSE_TIME_BUDGET_S = 30
NOT_REQUIRED_RE = re.compile(r"no package requires .+")
NOT_INSTALLED_RE = re.compile(r"package .+ is not installed")


def _kill_group(process):
    """SIGKILL no grupo do processo. Com ``start_new_session`` o pid é o pgid: alcança o ``timeout`` e os netos."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass  # o grupo já não existe


def _capture(process, name, captured):
    """Lê ``name`` até LIMIT+1 bytes. Acima do limite, mata o grupo para nenhum neto continuar escrevendo."""
    data = getattr(process, name).read(LIMIT + 1)
    cut = len(data) > LIMIT
    if cut:
        _kill_group(process)
    captured[name] = (data[:LIMIT].decode("utf-8", "replace"), cut)


def read(argv):
    """Executa sem shell, em grupo próprio. stdout e stderr chegam separados; cada um é cortado em LIMIT bytes.

    Stream que não fecha em JOIN_TIMEOUT_S (neto que escapou do grupo e segura o pipe) não vira resultado:
    o grupo morre, rc é 124 e ``truncated`` fica True. Stream sem captura nunca é lido como vazio.
    """
    receipt = {"argv": argv, "rc": None, "stdout": "", "stderr": "", "truncated": False}
    process = None
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, "LC_ALL": "C"}, start_new_session=True)
        captured = {}
        readers = [threading.Thread(target=_capture, args=(process, name, captured), daemon=True)
                   for name in ("stdout", "stderr")]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + JOIN_TIMEOUT_S
        for reader in readers:
            reader.join(max(0.0, deadline - time.monotonic()))
        if {"stdout", "stderr"} <= captured.keys():
            process.wait(timeout=2)
            receipt.update(rc=process.returncode, stdout=captured["stdout"][0],
                           stderr=captured["stderr"][0],
                           truncated=captured["stdout"][1] or captured["stderr"][1])
        else:
            _kill_group(process)
            process.wait(timeout=2)
            receipt.update(rc=124, truncated=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if process is not None:
            _kill_group(process)
        receipt.update(stderr=str(exc)[:1024], truncated=True)
    return receipt


def command(argv):
    return read(["timeout", "--signal=KILL", "8", *argv])


def _unit(name, status, reason, binaries=(), packages=()):
    return {"unit": name, "status": status, "reason": reason,
            "binaries": sorted(binaries), "packages": sorted(packages)}


def _exec_paths(name, run):
    """Caminhos de ExecStart lidos com ``systemctl show -- <nome>``: (caminhos, None) ou (None, motivo)."""
    show = run(["systemctl", "show", "--no-pager", "--property=ExecStart,FragmentPath", "--", name])
    if show["rc"] != 0 or show["truncated"]:
        return None, "command_failed"
    raw = set()
    for line in show["stdout"].splitlines():
        if line.startswith("ExecStart="):
            raw.update(EXEC_PATH_RE.findall(line))
    paths = {path for path in raw if PATH_RE.match(path)}
    if raw - paths:
        return None, "unsupported_exec_path"
    if not paths:
        return None, "no_exec_start"
    return sorted(paths), None


def _owners(name, paths, run):
    """Pacotes donos dos binários: resolved, ou unresolved com o motivo real.

    rc 1 sem stderr, ou com a mensagem «is not owned by any package», é sem dono. Outro rc 1 é falha
    (rpmdb); rc 0 sem nome também é falha, nunca binário sem dono.
    """
    packages = set()
    for path in paths:
        owner = run(["rpm", "-qf", "--qf", "%{NAME}\n", path])
        if owner["truncated"] or owner["rc"] not in (0, 1):
            return _unit(name, "unresolved", "command_failed")
        if owner["rc"] == 1:
            if owner["stderr"] and not UNOWNED_RE.fullmatch(owner["stderr"].strip()):
                return _unit(name, "unresolved", "command_failed")
            return _unit(name, "unresolved", "unowned_binary")
        names = _package_names(owner["stdout"])
        if not names:
            return _unit(name, "unresolved", "command_failed")
        packages.update(names)
    return _unit(name, "resolved", None, paths, packages)


def _inspect_unit(name, run):
    paths, reason = _exec_paths(name, run)
    if reason:
        return _unit(name, "unresolved", reason)
    return _owners(name, paths, run)


def _requirement_names(stdout):
    names = []
    for line in stdout.splitlines():
        parts = line.split()
        if parts and not parts[0].startswith(SKIPPED_REQUIREMENTS) and parts[0] not in names:
            names.append(parts[0])
    return names


def _failure_reason(result):
    return "timeout" if result["rc"] == 124 else "command_failed"


def _package_names(stdout):
    """Nomes de pacote da saída do rpm, sem repetição. Linha que não é nome nunca entra."""
    names = []
    for line in stdout.splitlines():
        name = line.strip()
        if PACKAGE_RE.match(name) and name not in names:
            names.append(name)
    return names


def _whatprovides(requirement, run):
    """(provedores, None), ou (None, motivo) quando a leitura falhou.

    rc 1 sem stderr é "ninguém provê", e rc 1 com stderr igual à mensagem do rpm para este requisito também.
    Qualquer outro stderr com rc 1 é falha: tratar como ausência esconderia aresta.
    """
    # "--" antes do nome: requisito que começa com "-" nunca vira opção; --qf precisa vir antes dele.
    result = run(["rpm", "-q", "--whatprovides", "--qf", "%{NAME}\n", "--", requirement])
    if result["truncated"] or result["rc"] not in (0, 1):
        return None, _failure_reason(result)
    if result["rc"] == 1:
        if result["stderr"] and result["stderr"].strip() != f"no package provides {requirement}":
            return None, "command_failed"
        return [], None
    names = _package_names(result["stdout"])
    if not names:
        return None, "command_failed"
    return names, None


def _elapsed_ms(since):
    """Milissegundos inteiros desde ``since`` (relógio monotônico). Só mede; não decide corte."""
    return int((time.monotonic() - since) * 1000)


def _closure(seed, run, started):
    """Dependências a partir dos pacotes donos, em largura, com limite de profundidade e nós.

    Aresta é o par (pacote, provedor): repetir o par só soma em ``duplicate_edges``. Requisito sem
    provedor não gera aresta nem nó (``requirements_without_provider``). Cada requisito é consultado
    uma vez na execução; resposta truncada ou com erro corta a medição e não entra no cache.
    """
    begun = time.monotonic()
    edges, nodes = [], 0
    visited = set(seed)
    recorded = set()
    providers_of = {}
    counts = {"requirement_queries": 0, "requirement_cache_hits": 0, "duplicate_edges": 0,
              "requirements_without_provider": 0}
    queue = collections.deque((package, 0) for package in sorted(seed))
    truncated, reason = False, None
    while queue:
        package, depth = queue.popleft()
        if depth >= MAX_DEPTH:
            continue
        if nodes >= MAX_NODES:
            truncated, reason = True, "node_budget"
            break
        if time.monotonic() - started > TIME_BUDGET_S:
            truncated, reason = True, "time_budget"
            break
        nodes += 1
        requires = run(["rpm", "-q", "--requires", "--", package])
        if requires["rc"] != 0 or requires["truncated"]:
            truncated, reason = True, _failure_reason(requires)
            break
        for requirement in _requirement_names(requires["stdout"]):
            if time.monotonic() - started > TIME_BUDGET_S:
                truncated, reason = True, "time_budget"
                break
            if requirement in providers_of:
                counts["requirement_cache_hits"] += 1
            else:
                providers, failure = _whatprovides(requirement, run)
                if failure:
                    truncated, reason = True, failure
                    break
                if not providers:
                    counts["requirements_without_provider"] += 1
                providers_of[requirement] = providers
                counts["requirement_queries"] += 1
            for provider in providers_of[requirement]:
                if provider == package:
                    continue
                if (package, provider) in recorded:
                    counts["duplicate_edges"] += 1
                    continue
                if len(edges) >= EDGE_CAP:
                    truncated, reason = True, "edge_budget"
                    break
                recorded.add((package, provider))
                edges.append({"from": package, "to": provider, "depth": depth + 1})
                if provider not in visited:
                    visited.add(provider)
                    queue.append((provider, depth + 1))
            if truncated:
                break
        if truncated:
            break
    return {"max_depth": MAX_DEPTH, "max_nodes": MAX_NODES, "nodes": nodes,
            "truncated": truncated, "reason": reason, "edges": edges, "elapsed_ms": _elapsed_ms(begun), **counts}


def _kernel(run):
    release = run(["uname", "-r"])
    running = release["stdout"].strip()
    if release["rc"] != 0 or release["truncated"] or not RELEASE_RE.match(running):
        running = None
    entries = run(["rpm", "-q", "--qf", "%{NAME}|%{VERSION}|%{RELEASE}\n", *KERNEL_NAMES])
    packages = []
    if not (entries["truncated"] or entries["rc"] == 124):
        for line in entries["stdout"].splitlines():
            parts = line.strip().split("|")
            if len(parts) == 3 and parts[0] in KERNEL_NAMES:
                packages.append({"name": parts[0], "version": parts[1], "release": parts[2]})
    return running, packages, (running is None), (entries["truncated"] or entries["rc"] == 124)


def _inspect_trigger(name, run):
    """Leitura pelo modelo ``x@samurai-probe-model.service`` não vê drop-in por instância; a instância vem primeiro."""
    kind = "timer" if name.endswith(".timer") else "socket"
    prop = "Unit" if kind == "timer" else "Triggers"
    target = None
    via_model = False
    shown = run(["systemctl", "show", "--no-pager", f"--property={prop}", "--", name])
    if shown["rc"] != 0 or shown["truncated"]:
        entry = _unit(name, "unresolved", "command_failed")
    else:
        value = shown["stdout"].strip()
        resolved = value.split("=", 1)[1].strip() if "=" in value else ""
        if not resolved:
            resolved = name.rsplit(".", 1)[0] + ".service"
        model = MODEL_RE.match(resolved)
        instance = INSTANCE_RE.match(resolved)
        if len(resolved.split()) != 1 or not UNIT_RE.match(resolved):
            entry = _unit(name, "unresolved", "unsupported_trigger")
        elif "@" in resolved and not (model or instance):
            entry = _unit(name, "unresolved", "template_unit")
        else:
            target = resolved
            if instance:
                paths, reason = _exec_paths(resolved, run)
                if reason is None:
                    inspected = _owners(resolved, paths, run)
                elif reason == "no_exec_start":
                    # Só instância sem ExecStart cai no modelo, marcado; outra falha fica com o motivo dela.
                    via_model = True
                    inspected = _inspect_unit(f"{instance.group(1)}@{MODEL_INSTANCE}.service", run)
                else:
                    inspected = _unit(resolved, "unresolved", reason)
            elif model:
                via_model = True
                inspected = _inspect_unit(f"{model.group(1)}@{MODEL_INSTANCE}.service", run)
            else:
                inspected = _inspect_unit(resolved, run)
            entry = _unit(name, inspected["status"], inspected["reason"],
                          inspected["binaries"], inspected["packages"])
    entry["kind"] = kind
    entry["target"] = target
    entry["via_model"] = via_model
    return entry


def _trigger_units(run, started):
    listed = run(["systemctl", "list-units", "--type=timer,socket", "--state=active",
                  "--no-legend", "--plain", "--no-pager"])
    if listed["rc"] != 0 or listed["truncated"]:
        return [], ["trigger_list_unavailable"]
    names = []
    reasons = []
    for line in listed["stdout"].splitlines():
        token = line.split()[0] if line.split() else ""
        if UNIT_RE.match(token) and token.endswith((".timer", ".socket")):
            names.append(token)
        elif token and not UNIT_RE.match(token):
            reasons.append("unit_name_unsupported")
    if len(names) > MAX_UNITS:
        reasons.append("triggers_over_limit")
        names = names[:MAX_UNITS]
    entries = []
    for name in names:
        if time.monotonic() - started > TIME_BUDGET_S:
            entries.append({**_unit(name, "unresolved", "time_budget"), "kind": "timer" if name.endswith(".timer") else "socket", "target": None, "via_model": False})
        else:
            entries.append(_inspect_trigger(name, run))
    return entries, reasons


def _release_of(running):
    """Versão-release do kernel em execução, sem o sufixo de arquitetura."""
    return running.rsplit(".", 1)[0] if running.endswith((".x86_64", ".aarch64")) else running


def _scope():
    """Escopo declarado da medição: o que ``complete`` quer dizer. O controlador confere este valor."""
    return {"dependency_depth": MAX_DEPTH, "unit_kinds": sorted(UNIT_KINDS)}


def _reverse_request(request):
    """Pacotes pedidos para dependente reverso: ``None`` sem pedido, ``[]`` com pedido inválido."""
    if "reverse_dependency_packages" not in request:
        return None
    names = request["reverse_dependency_packages"]
    if (not isinstance(names, list) or not 0 < len(names) <= MAX_REVERSE_PACKAGES
            or len(set(map(str, names))) != len(names)
            or not all(isinstance(name, str) and PACKAGE_RE.fullmatch(name) for name in names)):
        return []
    return names


def _unmeasured(reason):
    return {"status": "unmeasured", "reason": reason}


def _installed(package, run):
    """``True`` instalado, ``False`` ausente do banco RPM, ``None`` quando a leitura falhou."""
    result = run(["rpm", "-q", "--qf", "%{NAME}\n", "--", package])
    if result["truncated"] or result["rc"] not in (0, 1):
        return None
    if result["rc"] == 1:
        message = (result["stdout"] + result["stderr"]).strip()
        return False if NOT_INSTALLED_RE.fullmatch(message) else None
    return True if _package_names(result["stdout"]) == [package] else None


def _provided_capabilities(package, run):
    """Capacidades que o pacote fornece, soname incluído: (lista, None) ou (None, motivo)."""
    result = run(["rpm", "-q", "--provides", "--", package])
    if result["rc"] != 0 or result["truncated"]:
        return None, _failure_reason(result)
    names = []
    for line in result["stdout"].splitlines():
        parts = line.split()
        if parts and parts[0] not in names:
            names.append(parts[0])
    return names, None


def _owned_files(package, run):
    """Arquivos do pacote: (lista, None) ou (None, motivo). Linha que não é caminho absoluto corta a medição."""
    result = run(["rpm", "-ql", "--", package])
    if result["rc"] != 0 or result["truncated"]:
        return None, _failure_reason(result)
    files = []
    for line in result["stdout"].splitlines():
        path = line.strip()
        if not path or path == "(contains no files)":
            continue
        if not path.startswith("/"):
            return None, "unsupported_file_path"
        files.append(path)
    return files, None


def _requirers(package, arguments, run):
    """Quem requer qualquer argumento (nome, capacidade ou arquivo): (lista, None) ou (None, motivo).

    rc do ``rpm -q`` conta os argumentos sem resposta, então rc diferente de zero não é falha. Falha é
    saída cortada, tempo esgotado, stderr com outra mensagem ou linha que não é nome de pacote.
    """
    result = run(["rpm", "-q", "--whatrequires", "--qf", "%{NAME}\n", "--", *arguments])
    if result["truncated"] or result["rc"] == 124:
        return None, _failure_reason(result)
    if any(line.strip() and not NOT_REQUIRED_RE.fullmatch(line.strip())
           for line in result["stderr"].splitlines()):
        return None, "command_failed"
    names = set()
    for line in result["stdout"].splitlines():
        text = line.strip()
        if not text or NOT_REQUIRED_RE.fullmatch(text):
            continue
        if not PACKAGE_RE.fullmatch(text):
            return None, "command_failed"
        names.add(text)
    names.discard(package)
    return sorted(names), None


def _reverse_of(package, run):
    installed = _installed(package, run)
    if installed is None:
        return _unmeasured("command_failed")
    if installed is False:
        return {"status": "not_installed"}
    capabilities, reason = _provided_capabilities(package, run)
    if reason:
        return _unmeasured(reason)
    files, reason = _owned_files(package, run)
    if reason:
        return _unmeasured(reason)
    arguments = list(dict.fromkeys([package, *capabilities, *files]))
    if len(arguments) > MAX_REVERSE_ARGUMENTS:
        return _unmeasured("arguments_over_limit")
    requirers, reason = _requirers(package, arguments, run)
    if reason:
        return _unmeasured(reason)
    return {"status": "measured", "requirers": requirers,
            "capabilities": len(capabilities), "files": len(files)}


def _reverse_dependencies(packages, run):
    """Dependentes reversos dos pacotes pedidos, com cobertura própria."""
    started = time.monotonic()
    measured = {}
    for package in packages:
        if time.monotonic() - started > REVERSE_TIME_BUDGET_S:
            measured[package] = _unmeasured("time_budget")
        else:
            measured[package] = _reverse_of(package, run)
    reasons = sorted({entry["reason"] for entry in measured.values() if entry["status"] == "unmeasured"})
    if not packages:
        reasons = ["request_invalid"]
    return {"packages": measured, "elapsed_ms": _elapsed_ms(started),
            "coverage": {"status": "partial" if reasons else "complete", "reasons": reasons}}


def collect(request, run=command):
    if not isinstance(request, dict):
        raise ValueError("binding must be an object")
    started = time.monotonic()
    reasons = []
    listed = run(["systemctl", "list-units", "--type=service", "--state=running",
                  "--no-legend", "--plain", "--no-pager"])
    if listed["rc"] != 0 or listed["truncated"]:
        reasons.append("unit_list_unavailable")
        names = []
    else:
        names = []
        for line in listed["stdout"].splitlines():
            token = line.split()[0] if line.split() else ""
            if UNIT_RE.match(token):
                names.append(token)
            elif token:
                reasons.append("unit_name_unsupported")
    if len(names) > MAX_UNITS:
        reasons.append("units_over_limit")
        names = names[:MAX_UNITS]
    units = []
    for name in names:
        if time.monotonic() - started > TIME_BUDGET_S:
            units.append(_unit(name, "unresolved", "time_budget"))
        else:
            units.append(_inspect_unit(name, run))
    trigger_entries, trigger_reasons = _trigger_units(run, started)
    units.extend(trigger_entries)
    reasons.extend(trigger_reasons)
    for entry in units:
        if entry["status"] != "resolved":
            reasons.append(entry["reason"])
    seed = {package for entry in units for package in entry["packages"]}
    closure = _closure(seed, run, started)
    if closure["truncated"]:
        reasons.append("closure_truncated")
    running, kernel_packages, kernel_unknown, kernel_failed = _kernel(run)
    if kernel_unknown:
        reasons.append("kernel_unknown")
    if kernel_failed:
        reasons.append("kernel_packages_unavailable")
    if not (kernel_unknown or kernel_failed) and not any(
            f"{pkg['version']}-{pkg['release']}" == _release_of(running) for pkg in kernel_packages):
        reasons.append("running_kernel_unmatched")
    unresolved = [entry["unit"] for entry in units if entry["status"] != "resolved"]
    reasons = sorted(set(reasons))
    reverse = _reverse_request(request)
    observation = {
        "schema": SCHEMA,
        "binding": request,
        "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "service_mutations": 0,
        "configuration_writes": 0,
        "elapsed_ms": _elapsed_ms(started),
        "running_kernel": running,
        "kernel_packages": kernel_packages,
        "units": units,
        "closure": closure,
        "coverage": {
            "status": "partial" if reasons or unresolved else "complete",
            "reasons": reasons,
            "unresolved_units": unresolved,
            "scope": _scope(),
        },
    }
    if reverse is not None:
        observation["reverse_dependencies"] = _reverse_dependencies(reverse, run)
    return observation


if __name__ == "__main__":
    # Mesmo contrato de ``collect_dependency_linux.py``: o request chega em argv[1] (python -c).
    request = json.loads(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)
    print(json.dumps(collect(request), sort_keys=True, separators=(",", ":")))
