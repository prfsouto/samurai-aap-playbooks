"""Bounded read-only observation of the local HTTP endpoint and its registered units.

Não inicia, recarrega nem configura serviço. Lê o estado das unidades registradas e,
só com alguma ativa, faz um GET local sem proxy e sem seguir redirecionamento.
Compatível com o Python da plataforma do RHEL 8 (3.6).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

SCHEMA = "samurai.rhel_http_function_observation/v1"
SCOPE = "Local HTTP endpoint and its registered systemd units"
SUBJECTS = ("httpd.service", "nginx.service")
URI = "http://127.0.0.1/"
#: Caminhos registrados além da raiz, servidos por conteúdo de pacote (``/manual/`` vem do httpd-manual).
EXTRA_URIS = ("http://127.0.0.1/manual/",)
#: v4 (contrato): caminhos sem Host, medidos duas vezes; o status e o hash têm de se repetir para virar referência.
V4_EXTRA_URIS = ("http://127.0.0.1/.noindex.html", "http://127.0.0.1/icons/apache_pb2.gif",
                 "http://127.0.0.1/samurai-nao-existe-404")
#: Estados literais de ``client_host`` quando não há nome válido. Têm ``_``, então nunca são um nome de host.
CLIENT_HOST_NOT_CONFIGURED = "not_configured"
CLIENT_HOST_INVALID = "invalid_value"
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*")
#: Data no corpo (ISO, RFC 1123 ou autoindex): a página muda entre medições, então não serve de referência.
_DATE = re.compile(rb"\d{4}-\d{2}-\d{2}|\d{2}-[A-Za-z]{3}-\d{4}|\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} [A-Za-z]{3} \d{4}|Index of /")
UNIT_FIELDS = ("Id", "LoadState", "ActiveState", "SubState", "UnitFileState")
BODY_LIMIT = 1048576
UNIT_LIMIT = 16384
#: Configuração de cada unidade registrada: só arquivos regulares do primeiro nível destes diretórios.
#: O dump efetivo é a leitura do próprio servidor sobre a configuração carregada (só httpd tem).
CONFIG = {
    "httpd.service": {"root": "/etc/httpd", "dirs": ("conf", "conf.d", "conf.modules.d"),
                      "effective": ("httpd", "-t", "-D", "DUMP_VHOSTS", "-D", "DUMP_RUN_CFG", "-D", "DUMP_MODULES")},
    "nginx.service": {"root": "/etc/nginx", "dirs": (".", "conf.d", "default.d"), "effective": None},
}
CONFIG_FILE_LIMIT = 256
CONFIG_OUTPUT_LIMIT = 65536


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


def _header(headers, name):
    value = headers.get(name) if headers is not None else None
    return value[:256] if isinstance(value, str) else None


def measure(uri=URI, host=None, method="GET"):
    """Uma leitura local sem proxy e sem seguir redirecionamento; ``host`` vai no cabeçalho Host (rota do cliente)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    reading = {"uri": uri, "host": host, "method": method, "measured": False, "status": None,
               "body_sha256": None, "body_bytes": None, "truncated": None, "content_type": None,
               "server": None, "elapsed_ms": None, "dynamic": None}
    started = time.monotonic()
    try:
        response = opener.open(urllib.request.Request(uri, headers={"Host": host} if host else {}, method=method),
                               timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reading["error"] = type(exc).__name__
        return reading
    try:
        body = b"" if method == "HEAD" else response.read(BODY_LIMIT + 1)
        status = response.code if isinstance(response, urllib.error.HTTPError) else response.status
        headers = response.headers
    except OSError as exc:
        reading["error"] = type(exc).__name__
        return reading
    finally:
        response.close()
    truncated = len(body) > BODY_LIMIT
    whole = method == "GET" and not truncated
    reading.update(measured=True, status=status, truncated=truncated,
                   body_bytes=len(body) if whole else None,
                   body_sha256=hashlib.sha256(body).hexdigest() if whole else None,
                   dynamic=bool(_DATE.search(body)) if whole else None,
                   content_type=_header(headers, "Content-Type"), server=_header(headers, "Server"),
                   elapsed_ms=int((time.monotonic() - started) * 1000))
    return reading


def http(uri=URI):
    return measure(uri)


def client_host_state(raw):
    """Nome do cliente quando válido; ausente vira ``not_configured``, inválido vira ``invalid_value``. Nunca inventa nome."""
    name = (raw or "").strip().lower()
    if not name:
        return CLIENT_HOST_NOT_CONFIGURED
    return name if len(name) <= 253 and _HOST.fullmatch(name) else CLIENT_HOST_INVALID


def _stable(uri, host=None, method="GET"):
    """Mede duas vezes; a rota só é estável se a segunda medição repete o status (e o hash, no GET)."""
    first, again = measure(uri, host, method), measure(uri, host, method)
    first.update(repeat_status=again["status"], repeat_sha256=again["body_sha256"])
    return first


def contract_readings(client_host):
    """Leituras v4: GET dos caminhos sem Host e HEAD na raiz; com nome do cliente, GET e HEAD na raiz com o Host."""
    # Mesmos caminhos que o contrato v4 lê: raiz, /manual/ (EXTRA_URIS) e os caminhos extras da v4.
    readings = [_stable(uri) for uri in (URI,) + EXTRA_URIS + V4_EXTRA_URIS]
    readings.append(_stable(URI, None, "HEAD"))
    if client_host not in (CLIENT_HOST_NOT_CONFIGURED, CLIENT_HOST_INVALID):
        readings.append(_stable(URI, client_host))
        readings.append(_stable(URI, client_host, "HEAD"))
    return readings


def run(argv, limit=CONFIG_OUTPUT_LIMIT):
    """Saída limitada de um comando só de leitura; ``None`` quando não roda ou excede o limite."""
    try:
        process = subprocess.run(["timeout", "--signal=KILL", "8", *argv], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, env=dict(os.environ, LC_ALL="C"), timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if len(process.stdout) > limit:
        return None
    return {"rc": process.returncode, "stdout": process.stdout.decode("utf-8", "replace")}


def config_files(root, dirs):
    """Arquivos regulares do primeiro nível, sem seguir link; ``None`` quando algum não pôde ser lido."""
    files = []
    for name in dirs:
        folder = os.path.normpath(os.path.join(root, name))
        if not os.path.isdir(folder) or os.path.islink(folder):
            continue
        for entry in sorted(os.listdir(folder)):
            path = os.path.join(folder, entry)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            if len(files) >= CONFIG_FILE_LIMIT:
                return None
            try:
                with open(path, "rb") as handle:
                    body = handle.read(BODY_LIMIT + 1)
            except OSError:
                return None
            if len(body) > BODY_LIMIT:
                return None
            files.append({"path": path, "sha256": hashlib.sha256(body).hexdigest()})
    return files


def package_digests(owner):
    """Digest SHA-256 que o rpmdb registra para cada arquivo do pacote (algoritmo 8)."""
    reading = run(["rpm", "-q", "--qf", "%{FILEDIGESTALGO}\\n[%{FILENAMES}\\t%{FILEDIGESTS}\\n]", owner], 4 * CONFIG_OUTPUT_LIMIT)
    if reading is None or reading["rc"] != 0:
        return None
    lines = reading["stdout"].splitlines()
    if not lines or lines[0].strip() != "8":
        return None
    return dict(line.split("\t", 1) for line in lines[1:] if "\t" in line)


def configuration(subject):
    """Arquivos de configuração da unidade com hash, dono no rpmdb e se o conteúdo é o do pacote."""
    spec = CONFIG.get(subject)
    files = config_files(spec["root"], spec["dirs"]) if spec else None
    value = {"root": spec["root"] if spec else None, "complete": False, "files": files or [], "effective": None}
    if files is None:
        return value
    digests = {}
    for item in files:
        owner = run(["rpm", "-qf", "--qf", "%{NAME}\\n", item["path"]])
        names = owner["stdout"].split() if owner is not None and owner["rc"] == 0 else []
        item["owner"] = names[0] if len(names) == 1 else None
        if item["owner"] is not None and item["owner"] not in digests:
            digests[item["owner"]] = package_digests(item["owner"])
        recorded = digests.get(item["owner"]) if item["owner"] is not None else None
        item["package_digest"] = recorded.get(item["path"]) if recorded is not None else None
        # Dono sem digest legível deixa a origem sem prova; arquivo sem dono é configuração própria.
        if item["owner"] is not None and recorded is None:
            return value
    if spec["effective"] is not None:
        value["effective"] = run(list(spec["effective"]))
    value["complete"] = True
    return value


def collect(uri=URI, client_host=None):
    value = {"schema_version": SCHEMA, "scope": SCOPE, "configuration_writes": 0, "service_mutations": 0,
             "complete_application_function": False}
    units = {subject: unit(subject) for subject in SUBJECTS}
    if any(fields is None for fields in units.values()):
        value["observation_state"] = "unavailable"
        return value
    value.update(observation_state="observed", units=units, client_host=client_host_state(client_host))
    active = [subject for subject, fields in units.items()
              if fields["LoadState"] == "loaded" and fields["ActiveState"] == "active"]
    if active:
        value["http"] = http(uri)
        value["http_paths"] = [http(extra) for extra in EXTRA_URIS]
        value["http_v4"] = contract_readings(value["client_host"])
        value["configuration"] = {subject: configuration(subject) for subject in active}
    else:
        value["http"] = {"uri": uri, "measured": False, "reason": "unit_not_active"}
    return value


if __name__ == "__main__":
    # O nome do cliente chega como argumento do ``python -c``; sem argumento, a observação diz ``not_configured``.
    client = sys.argv[1] if len(sys.argv) > 1 else None
    sys.stdout.write(json.dumps(collect(client_host=client), sort_keys=True, separators=(",", ":")) + "\n")
