"""Read only approved local projections. No cmdline, environment, DNS or peer IO."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import selectors
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DIMENSIONS = ("processes", "services", "listening", "connections", "configuration")
FIELDS = {
    "processes": {"pid", "name"},
    "services": {"name", "pid", "state"},
    "listening": {"protocol", "local_address", "local_port", "state"},
    "connections": {
        "protocol",
        "local_address",
        "local_port",
        "remote_address",
        "remote_port",
        "state",
    },
    "configuration": {"name", "startup_mode", "service_type"},
}
KINDS = {
    "processes": "linux_proc_comm",
    "services": "linux_systemd",
    "listening": "linux_proc_net",
    "connections": "linux_proc_net",
    "configuration": "linux_systemd_properties",
}


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class LimitReached(Exception):
    pass


class ReadBudget:
    def __init__(self, limits: dict[str, Any]) -> None:
        self.limits = limits
        self.calls = 0
        self.bytes_read = 0
        self.deadline = time.monotonic() + limits["max_duration_seconds"]

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise LimitReached("duration_limit")
        if self.bytes_read >= self.limits["max_bytes"]:
            raise LimitReached("byte_limit")

    def call(self) -> None:
        self.check()
        if self.calls >= self.limits["max_calls"]:
            raise LimitReached("call_limit")
        self.calls += 1

    def consume(self, body: bytes) -> None:
        self.bytes_read += len(body)
        self.check()

    def read(self, path: Path) -> bytes:
        self.call()
        with path.open("rb") as handle:
            body = handle.read(
                min(
                    self.limits["max_field_bytes"] + 1,
                    self.limits["max_bytes"] - self.bytes_read,
                )
            )
        self.consume(body)
        return body


def bounded_command(argv: list[str], budget: ReadBudget) -> bytes:
    budget.call()
    output = bytearray()
    with subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False
    ) as child:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    budget.check()
                    for key, _ in selector.select(
                        timeout=max(0, budget.deadline - time.monotonic())
                    ):
                        size = min(4096, budget.limits["max_bytes"] - budget.bytes_read)
                        chunk = os.read(key.fd, size)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        budget.consume(chunk)
                        output.extend(chunk)
                code = child.wait(timeout=max(0, budget.deadline - time.monotonic()))
                if code != 0:
                    raise OSError("source_command_failed")
        except BaseException:
            child.kill()
            child.wait()
            raise
    return bytes(output)


def processes(budget: ReadBudget, root: Path) -> Iterator[dict[str, Any]]:
    budget.call()
    with os.scandir(root) as entries:
        for entry in entries:
            budget.check()
            if entry.name.isdigit():
                name = (
                    budget.read(Path(entry.path) / "comm")
                    .decode("utf-8", errors="strict")
                    .rstrip("\n")
                )
                yield {"pid": int(entry.name), "name": name}


def endpoint(value: str, ipv6: bool) -> tuple[str, int]:
    address, port = value.split(":")
    encoded = bytes.fromhex(address)
    decoded = (
        b"".join(encoded[i : i + 4][::-1] for i in range(0, 16, 4))
        if ipv6
        else encoded[::-1]
    )
    return str(ipaddress.ip_address(decoded)), int(port, 16)


def tcp_records(budget: ReadBudget, root: Path, dimension: str) -> Iterator[dict[str, Any]]:
    for filename in ("tcp", "tcp6"):
        budget.call()
        with (root / "net" / filename).open("rb") as handle:
            while True:
                budget.check()
                line = handle.readline(budget.limits["max_bytes"] - budget.bytes_read)
                if not line:
                    break
                budget.consume(line)
                columns = line.decode("ascii").split()
                if columns and columns[0] == "sl":
                    continue
                if len(columns) < 4:
                    raise ValueError("socket_record_malformed")
                expected = "0A" if dimension == "listening" else "01"
                if columns[3] != expected:
                    continue
                local, local_port = endpoint(columns[1], filename == "tcp6")
                remote, remote_port = endpoint(columns[2], filename == "tcp6")
                record = {
                    "protocol": "tcp",
                    "local_address": local,
                    "local_port": local_port,
                    "state": "listening" if dimension == "listening" else "established",
                }
                if dimension == "connections":
                    record.update(remote_address=remote, remote_port=remote_port)
                yield record


def service_records(
    spec: dict[str, Any], budget: ReadBudget,
    run_command: Callable[[list[str], ReadBudget], bytes] = bounded_command,
) -> Iterator[dict[str, Any]]:
    for name in spec["service_names"]:
        body = run_command(
            [
                "systemctl",
                "show",
                "--property=Id,MainPID,ActiveState,LoadState,Type,UnitFileState",
                "--",
                name,
            ],
            budget,
        )
        properties = dict(
            line.split("=", 1)
            for line in body.decode("utf-8").splitlines()
            if "=" in line
        )
        if properties.get("LoadState") == "not-found":
            continue
        if properties.get("Id") != name or not properties.get("LoadState"):
            raise ValueError("service_identity_unresolved")
        if spec["dimension"] == "services":
            yield {
                "name": name,
                "pid": int(properties.get("MainPID", "0")),
                "state": properties.get("ActiveState") or "unknown",
            }
        else:
            yield {
                "name": name,
                "startup_mode": properties.get("UnitFileState") or "unknown",
                "service_type": properties.get("Type") or "unknown",
            }


def validate_request(request: dict[str, Any]) -> None:
    import re

    if set(request) != {"collector", "policy", "limits", "sources"}:
        raise ValueError("explicit dependency request is required")
    if request["collector"] != {
        "profile": "linux-local-dependencies",
        "version": "1",
        "platform": "linux",
    }:
        raise ValueError("unsupported Linux dependency profile")
    limits = request["limits"]
    for key in (
        "max_hosts",
        "max_calls",
        "max_bytes",
        "max_raw_bytes",
        "max_duration_seconds",
        "max_records_per_source",
        "max_field_bytes",
        "retention_seconds",
    ):
        if type(limits.get(key)) is not int or limits[key] <= 0:
            raise ValueError("explicit positive collection limits are required")
    if limits["max_hosts"] != 1 or not limits.get("budget_ref"):
        raise ValueError("single-host scope and explicit budget reference are required")
    policy = request["policy"]
    for key in (
        "policy_id",
        "policy_version",
        "catalog_sha256",
        "decision_3_ref",
        "decision_6_ref",
    ):
        if not isinstance(policy.get(key), str) or not policy[key]:
            raise ValueError("approved policy provenance is required")
    instant = datetime.now(timezone.utc)
    start = datetime.fromisoformat(policy["valid_from"])
    end = datetime.fromisoformat(policy["valid_until"])
    if start.tzinfo is None or end.tzinfo is None or not start <= instant < end:
        raise ValueError("policy is not valid at collection time")
    seen = set()
    if not request["sources"]:
        raise ValueError("explicit sources are required")
    for spec in request["sources"]:
        dimension = spec.get("dimension")
        if (
            dimension not in DIMENSIONS
            or dimension in seen
            or spec.get("source_kind") != KINDS[dimension]
        ):
            raise ValueError("source is not implemented or is repeated")
        seen.add(dimension)
        fields = spec.get("fields", [])
        required = FIELDS[dimension] - (
            {"pid"}
            if dimension == "services"
            else {"service_type"}
            if dimension == "configuration"
            else set()
        )
        if (
            len(set(fields)) != len(fields)
            or not required <= set(fields) <= FIELDS[dimension]
        ):
            raise ValueError("unsafe source projection")
        expected_scope = (
            "visible_processes"
            if dimension == "processes"
            else "named_services"
            if dimension in {"services", "configuration"}
            else "current_network_namespace"
        )
        if spec.get("scope") != expected_scope:
            raise ValueError("unsupported source scope")
        names = spec.get("service_names")
        if expected_scope == "named_services":
            if (
                not isinstance(names, list)
                or not names
                or len(set(names)) != len(names)
            ):
                raise ValueError("exact service allowlist is required")
            if any(
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@:-]*\.service", name)
                for name in names
            ):
                raise ValueError("unsafe service selector")
        elif names != []:
            raise ValueError("service selectors are not allowed for this source")
        validity = limits.get("signal_validity_seconds", {}).get(dimension)
        if type(validity) is not int or validity <= 0:
            raise ValueError("explicit signal validity is required")


def collect(
    request: dict[str, Any],
    *,
    proc_root: Path = Path("/proc"),
    run_command: Callable[[list[str], ReadBudget], bytes] = bounded_command,
) -> dict[str, Any]:
    validate_request(request)
    started = timestamp()
    budget = ReadBudget(request["limits"])
    selected = {spec["dimension"]: spec for spec in request["sources"]}
    results = []
    for dimension in DIMENSIONS:
        spec = selected.get(dimension)
        records = []
        errors = []
        truncated = False
        state, reason = "not_collected", "not_requested"
        if spec is not None:
            try:
                reader = (
                    processes(budget, proc_root)
                    if dimension == "processes"
                    else tcp_records(budget, proc_root, dimension)
                    if dimension in {"listening", "connections"}
                    else service_records(spec, budget, run_command)
                )
                for record in reader:
                    if len(records) >= budget.limits["max_records_per_source"]:
                        raise LimitReached("record_limit")
                    projected = {field: record[field] for field in spec["fields"]}
                    if any(
                        len(str(value).encode()) > budget.limits["max_field_bytes"]
                        or (
                            isinstance(value, str)
                            and (not value or any(ord(c) < 32 for c in value))
                        )
                        for value in projected.values()
                    ):
                        raise LimitReached("field_limit")
                    records.append(projected)
                state, reason = (
                    ("complete_for_scope" if records else "empty_confirmed"),
                    "",
                )
            except LimitReached as exc:
                state, reason, truncated = "partial", str(exc), True
                errors.append(reason)
            except PermissionError:
                state, reason = (
                    ("partial" if records else "permission_denied"),
                    "permission_denied",
                )
                errors.append(reason)
            except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
                state, reason = (
                    ("partial" if records else "unavailable"),
                    "source_read_failed",
                )
                errors.append(reason)
        records.sort(key=lambda item: json.dumps(item, sort_keys=True))
        results.append(
            {
                "dimension": dimension,
                "requested": spec,
                "state": state,
                "reason": reason,
                "truncated": truncated,
                "records": records,
                "records_sha256": digest(records),
                "errors": errors,
            }
        )
    return {
        "schema": "samurai.dependency_collection/v1",
        "collector": request["collector"],
        "policy": request["policy"],
        "limits": request["limits"],
        "observed_from": started,
        "observed_to": timestamp(),
        "sources": results,
        "usage": {"calls": budget.calls, "bytes_read": budget.bytes_read, "byte_measurement": "source_reads"},
    }


if __name__ == "__main__":
    request = json.loads(sys.argv[1])
    sys.stdout.write(
        json.dumps(collect(request), sort_keys=True, separators=(",", ":"))
    )
