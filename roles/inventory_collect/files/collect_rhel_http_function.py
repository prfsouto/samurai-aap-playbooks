"""Bounded read-only observation of the local HTTP endpoint and its registered units.

Não inicia, recarrega nem configura serviço. Lê o estado das unidades registradas e,
só com alguma ativa, faz um GET local sem proxy e sem seguir redirecionamento.
Compatível com o Python da plataforma do RHEL 8 (3.6).
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

SCHEMA = "samurai.rhel_http_function_observation/v1"
SCOPE = "Local HTTP endpoint and its registered systemd units"
SUBJECTS = ("httpd.service", "nginx.service")
URI = "http://127.0.0.1/"
#: Caminhos registrados além da raiz, servidos por conteúdo de pacote (``/manual/`` vem do httpd-manual).
EXTRA_URIS = ("http://127.0.0.1/manual/",)
UNIT_FIELDS = ("Id", "LoadState", "ActiveState", "SubState", "UnitFileState")
BODY_LIMIT = 1048576
UNIT_LIMIT = 16384


def unit(subject):
    argv = ["timeout", "--signal=KILL", "8", "systemctl", "show", "--no-pager",
            "--property=" + ",".join(UNIT_FIELDS), subject]
    try:
        process = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 env=dict(os.environ, LC_ALL="C"), timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if process.returncode != 0 or len(process.stdout) > UNIT_LIMIT:
        return None
    fields = {}
    for line in process.stdout.decode("utf-8", "replace").splitlines():
        name, separator, value = line.partition("=")
        if not separator or name in fields or name not in UNIT_FIELDS:
            return None
        fields[name] = value
    return fields if set(fields) == set(UNIT_FIELDS) and fields.get("Id") == subject else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http(uri=URI):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    reading = {"uri": uri, "measured": False, "status": None, "body_sha256": None,
               "body_bytes": None, "truncated": None}
    try:
        response = opener.open(urllib.request.Request(uri, method="GET"), timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reading["error"] = type(exc).__name__
        return reading
    try:
        body = response.read(BODY_LIMIT + 1)
        status = response.code if isinstance(response, urllib.error.HTTPError) else response.status
    except OSError as exc:
        reading["error"] = type(exc).__name__
        return reading
    finally:
        response.close()
    truncated = len(body) > BODY_LIMIT
    reading.update(measured=True, status=status, truncated=truncated,
                   body_bytes=None if truncated else len(body),
                   body_sha256=None if truncated else hashlib.sha256(body).hexdigest())
    return reading


def collect(uri=URI):
    value = {"schema_version": SCHEMA, "scope": SCOPE, "configuration_writes": 0, "service_mutations": 0,
             "complete_application_function": False}
    units = {subject: unit(subject) for subject in SUBJECTS}
    if any(fields is None for fields in units.values()):
        value["observation_state"] = "unavailable"
        return value
    value.update(observation_state="observed", units=units)
    if any(fields["LoadState"] == "loaded" and fields["ActiveState"] == "active" for fields in units.values()):
        value["http"] = http(uri)
        value["http_paths"] = [http(extra) for extra in EXTRA_URIS]
    else:
        value["http"] = {"uri": uri, "measured": False, "reason": "unit_not_active"}
    return value


if __name__ == "__main__":
    sys.stdout.write(json.dumps(collect(), sort_keys=True, separators=(",", ":")) + "\n")
