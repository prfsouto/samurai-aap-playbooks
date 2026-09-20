from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def recipe_fingerprint(
    packer_template: str,
    playbook: str,
    variables: Mapping[str, Any],
) -> str:
    """Return the stable fingerprint shared by proposal and execution."""
    payload = "\x00".join(
        [
            packer_template,
            playbook,
            json.dumps(dict(variables), sort_keys=True, default=str),
        ]
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _main(argv: list[str]) -> int:
    if len(argv) != 4:
        return 2
    template = Path(argv[1]).read_text(encoding="utf-8")
    playbook = Path(argv[2]).read_text(encoding="utf-8")
    variables = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
    if not isinstance(variables, dict):
        return 2
    print(recipe_fingerprint(template, playbook, variables))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
