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
import time

SCHEMA = "samurai.rhel_package_usage_observation/v1"
LIMIT = 16384
MAX_UNITS = 64
MAX_DEPTH = 2
MAX_NODES = 300
TIME_BUDGET_S = 60
EDGE_CAP = 500
KERNEL_NAMES = ("kernel", "kernel-core", "kernel-modules")
UNIT_KINDS = ("service", "timer", "socket")

UNIT_RE = re.compile(r"^[A-Za-z0-9@_.:-]{1,128}\.(service|timer|socket)$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9@_.:-]{1,128}\.service$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._/+@-]{1,255}$")
EXEC_PATH_RE = re.compile(r"path=([^ ;}]+)")
RELEASE_RE = re.compile(r"^[0-9][A-Za-z0-9_.+~-]{0,127}$")
SKIPPED_REQUIREMENTS = ("rpmlib(", "config(")


def read(argv):
    receipt = {"argv": argv, "rc": None, "stdout": "", "stderr": "", "truncated": False}
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env={**os.environ, "LC_ALL": "C"})
        output = process.stdout.read(LIMIT + 1)
        if len(output) > LIMIT:
            process.kill()
            receipt["truncated"] = True
        process.wait(timeout=2)
        receipt.update(rc=process.returncode, stdout=output[:LIMIT].decode("utf-8", "replace"))
    except (OSError, subprocess.TimeoutExpired) as exc:
        receipt["stderr"] = str(exc)[:1024]
    return receipt


def command(argv):
    return read(["timeout", "--signal=KILL", "8", *argv])


def _unit(name, status, reason, binaries=(), packages=()):
    return {"unit": name, "status": status, "reason": reason,
            "binaries": sorted(binaries), "packages": sorted(packages)}


def _inspect_unit(name, run):
    show = run(["systemctl", "show", "--no-pager", "--property=ExecStart,FragmentPath", name])
    if show["rc"] != 0 or show["truncated"]:
        return _unit(name, "unresolved", "command_failed")
    raw = set()
    for line in show["stdout"].splitlines():
        if line.startswith("ExecStart="):
            raw.update(EXEC_PATH_RE.findall(line))
    paths = {path for path in raw if PATH_RE.match(path)}
    if raw - paths:
        return _unit(name, "unresolved", "unsupported_exec_path")
    if not paths:
        return _unit(name, "unresolved", "no_exec_start")
    packages = set()
    for path in sorted(paths):
        owner = run(["rpm", "-qf", "--qf", "%{NAME}\n", path])
        names = [line.strip() for line in owner["stdout"].splitlines() if line.strip()]
        if owner["rc"] != 0 or owner["truncated"] or not names:
            return _unit(name, "unresolved", "unowned_binary")
        packages.update(names)
    return _unit(name, "resolved", None, paths, packages)


def _requirement_names(stdout):
    names = []
    for line in stdout.splitlines():
        parts = line.split()
        if parts and not parts[0].startswith(SKIPPED_REQUIREMENTS) and parts[0] not in names:
            names.append(parts[0])
    return names


def _failure_reason(result):
    return "timeout" if result["rc"] == 124 else "command_failed"


def _closure(seed, run, started):
    """Dependências a partir dos pacotes donos, em largura, com limite de profundidade e nós."""
    edges, nodes = [], 0
    visited = set(seed)
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
            provided = run(["rpm", "-q", "--whatprovides", requirement, "--qf", "%{NAME}\n"])
            if provided["truncated"] or provided["rc"] not in (0, 1):
                truncated, reason = True, _failure_reason(provided)
                break
            for provider in dict.fromkeys(
                line.strip() for line in provided["stdout"].splitlines() if line.strip()
            ):
                if provider == package:
                    continue
                if len(edges) >= EDGE_CAP:
                    truncated, reason = True, "edge_budget"
                    break
                edges.append({"from": package, "to": provider, "depth": depth + 1})
                if provider not in visited:
                    visited.add(provider)
                    queue.append((provider, depth + 1))
            if truncated:
                break
        if truncated:
            break
    return {"max_depth": MAX_DEPTH, "max_nodes": MAX_NODES, "nodes": nodes,
            "truncated": truncated, "reason": reason, "edges": edges}


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
    """Timer e socket disparam um serviço: o pacote que o serviço executa também os sustenta."""
    kind = "timer" if name.endswith(".timer") else "socket"
    prop = "Unit" if kind == "timer" else "Triggers"
    target = None
    shown = run(["systemctl", "show", "--no-pager", f"--property={prop}", name])
    if shown["rc"] != 0 or shown["truncated"]:
        entry = _unit(name, "unresolved", "command_failed")
    else:
        value = shown["stdout"].strip()
        resolved = value.split("=", 1)[1].strip() if "=" in value else ""
        if not resolved:
            resolved = name.rsplit(".", 1)[0] + ".service"
        if "@" in resolved:
            entry = _unit(name, "unresolved", "template_unit")
        elif len(resolved.split()) != 1 or not UNIT_RE.match(resolved):
            entry = _unit(name, "unresolved", "unsupported_trigger")
        else:
            target = resolved
            inspected = _inspect_unit(target, run)
            entry = _unit(name, inspected["status"], inspected["reason"],
                          inspected["binaries"], inspected["packages"])
    entry["kind"] = kind
    entry["target"] = target
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
            entries.append({**_unit(name, "unresolved", "time_budget"), "kind": "timer" if name.endswith(".timer") else "socket", "target": None})
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
