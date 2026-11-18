"""Observações de módulos do kernel iniciado. Compatível com o Python 3.6 do guest."""
import datetime
import json
import os
import re
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


def collect(release, root=Path("/")):
    if not RELEASE.fullmatch(release):
        raise ValueError("invalid kernel release")
    return {
        "schema": "samurai.linux_kernel_module_observation/v1",
        "running_kernel": release,
        "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "loaded": measure(root / "proc/modules", loaded_modules),
        "builtin": measure(root / "lib/modules" / release / "modules.builtin", builtin_modules),
        "boot_policy": measure(root / "proc/cmdline", boot_policy),
    }


if __name__ == "__main__":
    print(json.dumps(collect(os.uname().release)))
