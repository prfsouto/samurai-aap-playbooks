"""Bounded read-only probes for an explicitly bound RHEL validation candidate.

Única escrita: uma linha no journal marcada com o execution id, sem alterar serviço
nem configuração. Serve só para provar que o journald grava e devolve o que recebe.
"""
import datetime
import ipaddress
import json
import os
import re
import subprocess
import sys
import time

LIMIT = 16384
SUBJECTS = {"sshd.service", "chronyd.service", "NetworkManager.service", "dnf-automatic.service",
            "systemd-journald.service"}

JOURNAL_READ_ATTEMPTS = 3
JOURNAL_RETRY_DELAY_S = 1
JOURNAL_PAUSE = time.sleep


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


BUS_TEXT = ["busctl", "--system", "--auto-start=no", "--allow-interactive-authorization=no"]
NM = "org.freedesktop.NetworkManager"


def network_text(interface, server):
    """Mesmas leituras do D-Bus sem --json, que o busctl do systemd 239 (RHEL 8) não tem.

    Guarda as saídas como vieram; o controlador interpreta o texto ``TIPO VALOR``.
    """
    def ok(receipt):
        return receipt["rc"] == 0 and not receipt["truncated"]

    lookup = command([*BUS_TEXT, "call", NM, "/org/freedesktop/NetworkManager", NM,
                      "GetDeviceByIpIface", "s", interface])
    found = {"lookup_text": lookup}
    device = re.fullmatch(r'o "(/org/freedesktop/NetworkManager/Devices/[0-9]+)"\n?', lookup["stdout"]) if ok(lookup) else None
    if device is None:
        return found
    config_property = "Ip4Config" if server.version == 4 else "Ip6Config"
    found["device_text"] = command([*BUS_TEXT, "get-property", NM, device.group(1), f"{NM}.Device",
                                    "Interface", "Managed", "State", "ActiveConnection", config_property])
    rows = found["device_text"]["stdout"].splitlines() if ok(found["device_text"]) else []
    config = re.fullmatch(r'o "(/org/freedesktop/NetworkManager/IP[46]Config/[0-9]+)"', rows[-1]) if len(rows) == 5 else None
    if config is not None:
        found["ip_config_text"] = command([*BUS_TEXT, "get-property", NM, config.group(1),
                                           f"{NM}.IP{server.version}Config", "AddressData"])
    return found


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
        if lookup["rc"] != 0 or lookup["truncated"]:
            return {**result, **network_text(interface, server)}
        device_path = json.loads(lookup["stdout"])["data"][0]
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


def journal(execution):
    since = int(time.time())
    write = command(["/usr/bin/systemd-cat", "-t", "samurai-probe", "/usr/bin/printf", "%s\n", execution])
    # O journald pode demorar a expor a linha: no máximo três leituras, parando na marca.
    for attempt in range(JOURNAL_READ_ATTEMPTS):
        if attempt:
            JOURNAL_PAUSE(JOURNAL_RETRY_DELAY_S)
        read = command(["/usr/bin/journalctl", "--no-pager", "-o", "cat", "-t", "samurai-probe",
                        "--since", f"@{since}", "-n", "200"])
        if read["rc"] == 0 and not read["truncated"] and execution in read["stdout"].splitlines():
            break
    return {"since": since, "write": write, "read": read, "read_attempts": attempt + 1}


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
    # Unit inativa não recebe escrita: o probe não pode iniciar serviço por socket de ativação.
    unit = value["services"].get("systemd-journald.service", {}).get("stdout", "")
    if "systemd-journald.service" in subjects and "ActiveState=active" in unit.splitlines():
        value["journal"] = journal(request["samurai_execution_id"])
    return value


if __name__ == "__main__":
    print(json.dumps(collect(json.load(sys.stdin)), sort_keys=True, separators=(",", ":")))
