"""Bounded read-only probes for an explicitly bound RHEL validation candidate."""
import datetime
import ipaddress
import json
import os
import re
import subprocess
import sys

LIMIT = 16384
SUBJECTS = {"sshd.service", "chronyd.service", "NetworkManager.service", "dnf-automatic.service"}


def read(argv):
    receipt = {"argv": argv, "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "rc": None, "stdout": "", "stderr": "", "truncated": False}
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env={**os.environ, "LC_ALL": "C"})
        # A separate file/socket reader is unnecessary: timeout(1) also caps
        # descendants and this read stops as soon as the byte limit is crossed.
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


def properties(path, interface):
    return command(["busctl", "--system", "--json=short", "--auto-start=no",
                    "--allow-interactive-authorization=no", "call", "org.freedesktop.NetworkManager",
                    path, "org.freedesktop.DBus.Properties", "GetAll", "s", interface])


def network(connection):
    parts = connection.split()
    try:
        client = str(ipaddress.ip_address(parts[0]))
        server = ipaddress.ip_address(parts[2])
        route = command(["ip", "-j", "route", "get", client])
        rows = json.loads(route["stdout"]) if route["rc"] == 0 and not route["truncated"] else []
        interface = rows[0]["dev"]
        if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,64}", interface):
            return {"route": route}
        lookup = command(["busctl", "--system", "--json=short", "--auto-start=no",
                          "--allow-interactive-authorization=no", "call", "org.freedesktop.NetworkManager",
                          "/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager",
                          "GetDeviceByIpIface", "s", interface])
        result = {"route": route, "lookup": lookup,
                  "addresses": command(["ip", "-j", "address", "show", "dev", interface])}
        device_path = json.loads(lookup["stdout"])["data"][0] if lookup["rc"] == 0 and not lookup["truncated"] else ""
        if not re.fullmatch(r"/org/freedesktop/NetworkManager/Devices/[0-9]+", device_path):
            return result
        result["device"] = properties(device_path, "org.freedesktop.NetworkManager.Device")
        values = json.loads(result["device"]["stdout"])["data"][0]
        property_name = "Ip4Config" if server.version == 4 else "Ip6Config"
        config_path = values[property_name]["data"]
        if re.fullmatch(r"/org/freedesktop/NetworkManager/IP[46]Config/[0-9]+", str(config_path)):
            result["ip_config"] = properties(config_path, "org.freedesktop.NetworkManager.IP4Config" if server.version == 4 else "org.freedesktop.NetworkManager.IP6Config")
        return result
    except (KeyError, IndexError, TypeError, ValueError):
        return {"observation_state": "unavailable"}


def collect(request):
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or not subjects or any(subject not in SUBJECTS for subject in subjects):
        raise ValueError("unregistered service subject")
    connection = os.environ.get("SSH_CONNECTION", "")
    value = {"schema_version": "samurai.rhel_service_function_observation/v1", "binding": request,
             "service_mutations": 0, "configuration_writes": 0,
             "services": {subject: command(["systemctl", "show", "--no-pager",
                 "--property=Id,LoadState,ActiveState,SubState,UnitFileState", subject]) for subject in subjects}}
    if "sshd.service" in subjects or "NetworkManager.service" in subjects:
        value["ssh"] = {"transport": request.get("transport"), "port": request.get("target_port"),
                        "connection": connection, "command": command(["/usr/bin/printf", "%s\n", request["samurai_execution_id"]])}
    if "chronyd.service" in subjects:
        argv = ["chronyc", "-h", "127.0.0.1", "-p", "323", "-c"]
        value["chrony"] = {"transport": "udp_loopback", "target_host": "127.0.0.1", "target_port": 323,
                           "tracking": command([*argv, "tracking"]), "sources": command([*argv, "sources"])}
    if "NetworkManager.service" in subjects:
        value["network_manager"] = network(connection)
    return value


if __name__ == "__main__":
    print(json.dumps(collect(json.load(sys.stdin)), sort_keys=True, separators=(",", ":")))
