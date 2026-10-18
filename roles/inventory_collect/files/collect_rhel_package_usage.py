"""Uso de pacote pelo guest RHEL: leitura somente-leitura, sem root e sem executar binário.

Roda com o usuário de coleta, sem become. Fontes permitidas:
- systemctl list-units/show: metadados de unidade;
- rpm -qf, rpm -q --requires, rpm -q --whatprovides: banco RPM, só leitura;
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
"""
import collections
import datetime
import json
import os
import re
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


def read(argv):
    """Executa sem shell. stdout e stderr chegam separados; cada um é cortado em LIMIT bytes."""
    receipt = {"argv": argv, "rc": None, "stdout": "", "stderr": "", "truncated": False}
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, "LC_ALL": "C"})
        captured = {}

        def capture(stream_name):
            data = getattr(process, stream_name).read(LIMIT + 1)
            if len(data) > LIMIT:
                receipt["truncated"] = True
                process.kill()
            captured[stream_name] = data[:LIMIT].decode("utf-8", "replace")

        errors = threading.Thread(target=capture, args=("stderr",), daemon=True)
        errors.start()
        capture("stdout")
        errors.join()
        process.wait(timeout=2)
        receipt.update(rc=process.returncode, stdout=captured["stdout"], stderr=captured["stderr"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        receipt["stderr"] = str(exc)[:1024]
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
    """Pacotes donos dos binários: resolved, ou unresolved com o motivo real."""
    packages = set()
    for path in paths:
        owner = run(["rpm", "-qf", "--qf", "%{NAME}\n", path])
        if owner["truncated"] or owner["rc"] not in (0, 1):
            return _unit(name, "unresolved", "command_failed")
        names = _package_names(owner["stdout"]) if owner["rc"] == 0 else []
        if not names:
            return _unit(name, "unresolved", "unowned_binary")
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

    rc 1 sem stderr é "ninguém provê". rc 1 com stderr é falha: tratar como ausência esconderia aresta.
    """
    result = run(["rpm", "-q", "--whatprovides", requirement, "--qf", "%{NAME}\n"])
    if result["truncated"] or result["rc"] not in (0, 1) or (result["rc"] == 1 and result["stderr"]):
        return None, _failure_reason(result)
    if result["rc"] == 1:
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
        requires = run(["rpm", "-q", "--requires", package])
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
    return {
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


if __name__ == "__main__":
    # Mesmo contrato de ``collect_dependency_linux.py``: o request chega em argv[1] (python -c).
    request = json.loads(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)
    print(json.dumps(collect(request), sort_keys=True, separators=(",", ":")))
