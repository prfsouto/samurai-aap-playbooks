"""Pure helpers of the tenant provisioning role (ADR 0031, decision 7): no Ansible import, so they are tested directly.

The role commits ONE tenant file to the tenants repository (GitOps), checks the Vault precondition, and waits for the
ArgoCD Application. Everything here is data in, data out; the modules only wrap it. No secret is ever logged, and the
customer name is only ever DATA (serialized by a YAML library), never part of a command line or a template.
"""
from __future__ import annotations

import base64
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

# The same closed lists the BFF and the engine enforce (apps/dashboard/.../domain.py, remediation-engine service):
# a tenant file is validated again here, as the last line, before it reaches Git.
INTERNAL_ID = re.compile(r"[0-9a-f]{16,40}")
SLUG = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
ENVIRONMENTS = frozenset({"stg", "prod"})
NAME_CHARACTERS = re.compile(r"[\w .,&'’()/+@:-]+")
# A safe subset of RFC 5322 on purpose: no braces, quotes or backticks (Ansible templates extra-var values).
# Same rule as the BFF and the engine (domain labels of 1-63 characters, no hyphen at the ends).
_DOMAIN_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
EMAIL = re.compile(rf"[A-Za-z0-9._%+-]{{1,64}}@{_DOMAIN_LABEL}(?:\.{_DOMAIN_LABEL})*\.[A-Za-z]{{2,63}}")
TEXT_MAX = 255
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9._-]{1,100}")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")

DEFAULT_TIMEOUT = 20
# Plain http only to the machine itself (the tests' fake servers): a token never travels in clear text elsewhere.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ProvisionError(Exception):
    """A refusal or failure with a message that is safe to show (never a token, a key or a response body)."""


class ConflictError(ProvisionError):
    """The tenant file exists with a different identity: it is never overwritten."""


# ── Validation ───────────────────────────────────────────────────────────────


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > TEXT_MAX or not NAME_CHARACTERS.fullmatch(value):
        raise ProvisionError(f"{name} must be 1-{TEXT_MAX} letters, digits, spaces and . , & ' ’ ( ) / + @ : -")
    return value


def validate_tenant(*, internal_id: str, slug: str, environment: str, name: str, admin_email: str, admin_full_name: str) -> dict:
    """The tenant file document, in the closed schema of the tenants repository, or a refusal."""
    if not isinstance(internal_id, str) or not INTERNAL_ID.fullmatch(internal_id):
        raise ProvisionError("tenant_internal_id must be 16-40 lowercase hexadecimal characters")
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise ProvisionError("tenant_slug must be 1-63 lowercase letters, digits or '-'")
    if environment not in ENVIRONMENTS:
        raise ProvisionError("tenant_environment must be one of " + ", ".join(sorted(ENVIRONMENTS)))
    name = _text("tenant_name", name)
    full_name = _text("tenant_admin_full_name", admin_full_name)
    if not isinstance(admin_email, str) or len(admin_email) > TEXT_MAX or not EMAIL.fullmatch(admin_email):
        raise ProvisionError("tenant_admin_email is not a valid e-mail address")
    if any(ord(ch) < 32 for ch in admin_email):
        raise ProvisionError("tenant_admin_email has a control character")
    return {"tenantName": name, "admin": {"orgName": name, "email": admin_email, "fullName": full_name}}


def tenant_file_path(environment: str, internal_id: str) -> str:
    if environment not in ENVIRONMENTS or not INTERNAL_ID.fullmatch(internal_id):
        raise ProvisionError("environment or tenant id outside the contract")
    return f"tenants/{environment}/{internal_id}.yaml"


def render_tenant_yaml(document: dict) -> str:
    """Deterministic YAML from DATA: the same document is always the same bytes (so a rerun commits nothing).

    Every value is a double-quoted scalar (a JSON string is one): Helm reads YAML 1.1, where "Y", "n" or "0o17" would
    become a boolean or a number, and PyYAML does not do that.
    """
    def block(mapping: dict, indent: str) -> str:
        lines = []
        for key, value in mapping.items():
            if isinstance(value, dict):
                lines.append(f"{indent}{key}:\n{block(value, indent + '  ')}")
            else:
                lines.append(f"{indent}{key}: {json.dumps(str(value), ensure_ascii=False)}\n")
        return "".join(lines)

    return block(document, "")


def managed_identity(document: object) -> tuple | None:
    """The part of a tenant file this role owns. Anything else (image tags, a password rotation request) is a person's."""
    if not isinstance(document, dict) or not isinstance(document.get("admin"), dict):
        return None
    admin = document["admin"]
    return document.get("tenantName"), admin.get("orgName"), admin.get("email"), admin.get("fullName")


# ── HTTP ─────────────────────────────────────────────────────────────────────


def http_for(ca_file: str | None) -> "Http":
    """The client, trusting a private CA only when a non-empty CA file was given (an unset credential field injects an empty file)."""
    import os

    usable = bool(ca_file) and os.path.isfile(ca_file) and os.path.getsize(ca_file) > 0
    return Http(ca_file=ca_file if usable else None)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, never a second request: urllib would copy the credential headers to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Http:
    """A small client over urllib: TLS verified (optionally against a private CA), no redirects, bounded, and it
    never echoes a header or a body into an error."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, opener=None, ca_file: str | None = None):
        self._timeout = timeout
        if opener is not None:
            self._open = opener
            return
        context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
        self._open = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=context)).open

    def request(self, method: str, url: str, *, headers: dict | None = None, body: dict | None = None) -> tuple[int, object]:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS):
            raise ProvisionError("the URL must be https")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Accept": "application/json", **(headers or {})})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._open(req, timeout=self._timeout) as response:
                return response.status, _json(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _json(exc.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProvisionError(f"could not reach {urllib.parse.urlsplit(url).netloc}: {type(exc).__name__}") from None


def _json(raw: bytes) -> object:
    try:
        return json.loads(raw.decode() or "null")
    except ValueError:
        return None


# ── GitHub App ───────────────────────────────────────────────────────────────


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def github_app_jwt(app_id: str, private_key_pem: bytes, now: int | None = None) -> str:
    """RS256 JWT of a GitHub App (iss = app id, at most 10 minutes), signed with the App's private key."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    if not isinstance(app_id, str) or not app_id.isdigit():
        raise ProvisionError("the GitHub App id must be numeric")
    try:
        key = serialization.load_pem_private_key(private_key_pem, password=None)
    except (ValueError, TypeError):
        raise ProvisionError("the GitHub App private key is not a readable PEM key") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ProvisionError("the GitHub App private key must be RSA")
    issued = int(time.time() if now is None else now)
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({"iat": issued - 60, "exp": issued + 540, "iss": app_id}, separators=(",", ":")).encode())
    signature = key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


def installation_token(http: Http, api_url: str, jwt: str, installation_id: str, repository: str) -> str:
    """A token for ONE repository and `contents: write` only, whatever the installation could otherwise reach."""
    if not isinstance(installation_id, str) or not installation_id.isdigit():
        raise ProvisionError("the GitHub App installation id must be numeric")
    if not REPOSITORY.fullmatch(repository):
        raise ProvisionError("the repository must be owner/name")
    status, body = http.request(
        "POST",
        f"{api_url.rstrip('/')}/app/installations/{installation_id}/access_tokens",
        headers={"Authorization": f"Bearer {jwt}", "X-GitHub-Api-Version": "2022-11-28"},
        body={"repositories": [repository.split("/", 1)[1]], "permissions": {"contents": "write"}},
    )
    token = body.get("token") if isinstance(body, dict) else None
    if status != 201 or not isinstance(token, str) or not token:
        raise ProvisionError(f"GitHub did not issue an installation token (HTTP {status})")
    return token


def _gh_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"}


def _read_file(http: Http, api_url: str, token: str, repository: str, branch: str, path: str) -> tuple[str | None, str | None]:
    """(sha, text) of the file on the branch, or (None, None) when it does not exist."""
    status, body = http.request(
        "GET", f"{api_url.rstrip('/')}/repos/{repository}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(branch)}",
        headers=_gh_headers(token),
    )
    if status == 404:
        return None, None
    if status != 200 or not isinstance(body, dict) or body.get("type") != "file":
        raise ProvisionError(f"could not read {path} (HTTP {status})")
    try:
        return str(body["sha"]), base64.b64decode(body.get("content", "")).decode()
    except (KeyError, ValueError):
        raise ProvisionError(f"{path} came back unreadable") from None


def _last_commit(http: Http, api_url: str, token: str, repository: str, branch: str, path: str) -> str:
    status, body = http.request(
        "GET", f"{api_url.rstrip('/')}/repos/{repository}/commits?path={urllib.parse.quote(path)}&sha={urllib.parse.quote(branch)}&per_page=1",
        headers=_gh_headers(token),
    )
    if status != 200 or not isinstance(body, list) or not body or not isinstance(body[0], dict) or not body[0].get("sha"):
        raise ProvisionError(f"could not find the commit of {path} (HTTP {status})")
    return str(body[0]["sha"])


def upsert_tenant_file(
    http: Http, *, api_url: str, token: str, repository: str, branch: str, path: str, document: dict, message: str,
    check_mode: bool = False, attempts: int = 3,
) -> dict:
    """Create the tenant file, or confirm it. Never overwrites a different identity, never commits the same content twice.

    * absent: one commit creates it;
    * present with the SAME managed identity: nothing is committed (a rerun, a retry, or a file a person has since
      extended with image tags or a password rotation request), and the commit that last touched it is reported;
    * present with ANOTHER identity: a ConflictError, because overwriting would destroy somebody's tenant.
    """
    if not REPOSITORY.fullmatch(repository) or not BRANCH.fullmatch(branch):
        raise ProvisionError("repository or branch outside the contract")
    content = render_tenant_yaml(document)
    for _ in range(attempts):
        sha, existing = _read_file(http, api_url, token, repository, branch, path)
        if existing is not None:
            try:
                current = yaml.safe_load(existing)
            except yaml.YAMLError:
                raise ConflictError(f"{path} exists and is not valid YAML: refusing to overwrite it") from None
            if managed_identity(current) != managed_identity(document):
                raise ConflictError(f"{path} already exists for a different tenant identity: refusing to overwrite it")
            return {"changed": False, "commit_sha": _last_commit(http, api_url, token, repository, branch, path), "path": path}
        if check_mode:
            return {"changed": True, "commit_sha": None, "path": path}
        status, body = http.request(
            "PUT", f"{api_url.rstrip('/')}/repos/{repository}/contents/{urllib.parse.quote(path)}",
            headers=_gh_headers(token),
            body={"message": message, "content": base64.b64encode(content.encode()).decode(), "branch": branch},
        )
        commit = (body or {}).get("commit", {}).get("sha") if isinstance(body, dict) else None
        if status in (200, 201) and commit:
            return {"changed": True, "commit_sha": str(commit), "path": path}
        if status in (409, 422):
            continue  # someone wrote it between the read and the write: read again and decide
        raise ProvisionError(f"could not write {path} (HTTP {status})")
    raise ProvisionError(f"could not settle {path} after {attempts} attempts")


# ── ArgoCD ───────────────────────────────────────────────────────────────────


def wait_for_application(
    http: Http, *, server: str, token: str, name: str, timeout_seconds: int, interval_seconds: int,
    project: str = "tenants", clock=time.monotonic, sleep=time.sleep,
) -> dict:
    """Wait until the Application exists and is Synced and Healthy. Read-only.

    The Application is generated by the ApplicationSet from the file just committed, so "not found" is the normal
    first answer and only counts against the deadline. A 401/403 is a refusal, not a wait. The read names the project:
    without it ArgoCD answers 403 (not 404) for an Application that does not exist, so the first read would fail.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", project):
        raise ProvisionError("the ArgoCD project must be a plain name")
    if not re.fullmatch(r"tenant-[0-9a-f]{16,40}", name):
        raise ProvisionError("the Application name must be tenant-<id>")
    deadline = clock() + timeout_seconds
    last = {"sync": None, "health": None, "found": False}
    while True:
        status, body = http.request("GET", f"{server.rstrip('/')}/api/v1/applications/{name}?projects={project}", headers={"Authorization": f"Bearer {token}"})
        if status in (401, 403):
            raise ProvisionError(f"ArgoCD refused the read of {name} (HTTP {status})")
        if status == 200 and isinstance(body, dict):
            state = body.get("status") if isinstance(body.get("status"), dict) else {}
            last = {
                "found": True,
                "sync": (state.get("sync") or {}).get("status"),
                "health": (state.get("health") or {}).get("status"),
            }
            if last["sync"] == "Synced" and last["health"] == "Healthy":
                return {"application": name, **last}
        elif status not in (404,):
            raise ProvisionError(f"unexpected answer from ArgoCD for {name} (HTTP {status})")
        if clock() >= deadline:
            seen = f"sync={last['sync']} health={last['health']}" if last["found"] else "not generated yet"
            raise ProvisionError(f"{name} was not Synced and Healthy in {timeout_seconds}s ({seen})")
        sleep(interval_seconds)


# ── Vault (precondition, read-only, never a secret value) ───────────────────


def _is_past(timestamp: object) -> bool:
    """A KV v2 version with a deletion_time in the future is still readable (delete_version_after)."""
    if not timestamp:
        return False
    from datetime import datetime, timezone

    try:
        moment = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return True  # an unreadable time is not proof that the secret is there
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment <= datetime.now(timezone.utc)


def check_vault_precondition(http: Http, *, addr: str, token: str, internal_id: str) -> dict:
    """The tenant's secret exists (and is not deleted), and so do its policy and role. Reads no secret value."""
    if not INTERNAL_ID.fullmatch(internal_id):
        raise ProvisionError("tenant id outside the contract")
    base = addr.rstrip("/")
    headers = {"X-Vault-Token": token}
    missing = []

    status, body = http.request("GET", f"{base}/v1/secret/metadata/tenants/{internal_id}/runtime", headers=headers)
    if status in (401, 403):
        raise ProvisionError(f"Vault refused the read (HTTP {status}): the token needs read on the tenant metadata")
    data = body.get("data") if isinstance(body, dict) else None
    current = str(data.get("current_version")) if isinstance(data, dict) else ""
    version = (data.get("versions") or {}).get(current) if isinstance(data, dict) else None
    if status != 200 or not isinstance(version, dict) or version.get("destroyed") or _is_past(version.get("deletion_time")):
        missing.append(f"secret/tenants/{internal_id}/runtime")

    status, _ = http.request("GET", f"{base}/v1/sys/policies/acl/tenant-{internal_id}", headers=headers)
    if status == 404:
        missing.append(f"policy tenant-{internal_id}")
    elif status != 200:
        raise ProvisionError(f"could not read the tenant policy (HTTP {status})")

    status, _ = http.request("GET", f"{base}/v1/auth/kubernetes/role/tenant-{internal_id}", headers=headers)
    if status == 404:
        missing.append(f"role tenant-{internal_id}")
    elif status != 200:
        raise ProvisionError(f"could not read the tenant role (HTTP {status})")

    if missing:
        raise ProvisionError("the Vault precondition is missing: " + ", ".join(missing) + " (see docs/runbook/vault-tenant-gitops.md)")
    return {"secret": True, "policy": True, "role": True}
