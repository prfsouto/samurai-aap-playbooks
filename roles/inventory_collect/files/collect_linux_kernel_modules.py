"""Observações de módulos do kernel iniciado. Compatível com o Python 3.6 do guest."""
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

MODULE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,127}")
RELEASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
LIMIT = 512 * 1024


def module_names(names):
    values = set()
    for name in names:
        if not MODULE.fullmatch(name):
            raise ValueError("invalid module name")
        values.add(name.replace("-", "_"))
    return sorted(values)


def loaded_modules(data):
    names = []
    for line in data.splitlines():
        fields = line.split()
        if len(fields) < 6 or not fields[1].isdigit() or not fields[2].isdigit():
            raise ValueError("incomplete module record")
        names.append(fields[0])
    return module_names(names)


def builtin_modules(data):
    names = []
    for path in data.splitlines():
        name = path.rsplit("/", 1)[-1]
        if not name.endswith(".ko"):
            raise ValueError("invalid builtin record")
        names.append(name[:-3])
    return module_names(names)


def boot_policy(data):
    keys = ("module_blacklist", "modprobe.blacklist")
    selected = {}
    if any(char in data for char in ("'", '"', "\\")):
        raise ValueError("unsupported command line quoting")
    for arg in data.split():
        key, sep, value = arg.partition("=")
        if key == "--":
            break
        if key in keys:
            if not sep or key in selected:
                raise ValueError("ambiguous module configuration")
            selected[key] = module_names(value.split(","))
    return selected


def measure(path, parse):
    try:
        with path.open("rb") as stream:
            data = stream.read(LIMIT + 1)
        if len(data) > LIMIT or (data and not data.endswith(b"\n")):
            raise ValueError("incomplete read")
        return {"state": "measured", "value": parse(data.decode("ascii"))}
    except (OSError, ValueError):
        return {"state": "not_measured"}


def kmod_directives(lines):
    blacklisted, noop, other = set(), set(), set()
    total = 0
    for line in lines:
        total += len(line)
        if total > 16 * 1024 * 1024 or not line.endswith(b"\n"):
            raise ValueError("incomplete kmod configuration")
        fields = line.split()
        if not fields or fields[0] not in (b"blacklist", b"install"):
            continue
        if len(fields) < 2:
            raise ValueError("invalid directive")
        name = fields[1].decode("ascii")
        if not MODULE.fullmatch(name):
            raise ValueError("unsupported module selector")
        name = name.replace("-", "_")
        if fields[0] == b"blacklist":
            if len(fields) != 2:
                raise ValueError("ambiguous blacklist")
            blacklisted.add(name)
        elif fields[2:] in ([b"/bin/false"], [b"/usr/bin/false"], [b"/bin/true"], [b"/usr/bin/true"]):
            noop.add(name)
        else:
            other.add(name)
    return {"blacklist": sorted(blacklisted), "install_noop": sorted(noop - other)}


def measure_kmod():
    args = ["/usr/bin/timeout", "-k", "1", "4", "/sbin/modprobe", "-c"]
    try:
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env={"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}) as process:
            try:
                lines = iter(lambda: process.stdout.readline(LIMIT + 1), b"")
                value = kmod_directives(lines)
            finally:
                process.stdout.close()
            if not process.stderr.read(1) and process.wait() == 0:
                return {"state": "measured", "value": value}
    except (OSError, ValueError):
        pass
    return {"state": "not_measured"}


def collect(release, root=Path("/"), read_kmod=measure_kmod):
    if not RELEASE.fullmatch(release):
        raise ValueError("invalid kernel release")
    return {
        "schema": "samurai.linux_kernel_module_observation/v1",
        "running_kernel": release,
        "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "loaded": measure(root / "proc/modules", loaded_modules),
        "builtin": measure(root / "lib/modules" / release / "modules.builtin", builtin_modules),
        "boot_policy": measure(root / "proc/cmdline", boot_policy),
        "kmod_policy": read_kmod(),
    }


if __name__ == "__main__":
    print(json.dumps(collect(os.uname().release)))
