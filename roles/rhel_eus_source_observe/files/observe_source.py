#!/usr/bin/python3
"""Fixed source observations; the retained catalogue is qualified separately."""
import configparser
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

PROFILE_HASHES = {
    "client": "77616ab6db51d54bb377d31e41628d3b7e6b09c494c7adc34631dbfdd80c3d3c",
    "eus": "c3a6903fc31a06b2631f8fcc098d52ad62f6b2f7d4e2b6fcbf646ed6708c3ea3",
}
VENDOR_FILES = {
    "/usr/bin/rhui-eus-switch": "89021837d81705d5bcc3011350f51aff16e36dbf4644e3bc3219fc5d5352c3fe",
    "/usr/bin/rhui-set-release": "bd30b7d2790cabf8930fddde6f8e23c2f4c77b4775f2410a8ed4e982a35780f5",
    "/usr/sbin/choose_repo.py": "a06e90f2e1159e108aaa1d7bb8dfb2771a050763614aa5008f802718642e99d9",
}
CALLBACK_PATH = "/usr/lib/python3.9/site-packages/dnf-plugins/amazon-id.py"
CALLBACK_SHA = "2a29c4c653a0ef31b22d3aa80037daa14b399b8c46ff4fff24c5e4b389e78779"
EUS_IDS = {"rhel-9-appstream-eus-rhui-rpms", "rhel-9-baseos-eus-rhui-rpms"}
CLIENT_ID = "rhui-client-config-server-9"
CORPUS_SHA = "efe00c019eecd9c7170842614c09408a362d2d72b2bae8f78363e3e746ca9505"
FILE_LIMIT = 262144
TRUSTED_FILE_LIMIT = 32768
OUTPUT_LIMIT = 32768


class ObservationError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise ObservationError(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_bounded(path, limit=FILE_LIMIT):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "source_file_limit")
    return raw


def read_trusted(path, limit=TRUSTED_FILE_LIMIT):
    before = os.lstat(path)
    require(stat.S_ISREG(before.st_mode) and before.st_uid == 0
            and not before.st_mode & 0o022, "trusted_file_ownership_or_mode")
    require(before.st_size <= limit, "source_file_limit")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino)
                and stat.S_ISREG(opened.st_mode) and opened.st_uid == 0
                and not opened.st_mode & 0o022, "trusted_file_changed_during_open")
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "source_file_limit")
    return raw


def parse_binding(raw):
    require(len(raw) <= 4096, "source_binding_limit")
    binding = json.loads(raw)
    keys = {"organization_id", "source_id", "inventory_run_id", "source_execution_id",
            "prepared_content_fingerprint", "instance_id", "account_id", "region"}
    require(isinstance(binding, dict) and set(binding) == keys, "source_binding_shape")
    for name in ("organization_id", "source_id", "inventory_run_id"):
        require(type(binding[name]) is int and binding[name] > 0, "source_binding_id")
    for name, pattern in {
        "source_execution_id": r"[a-f0-9]{32}",
        "prepared_content_fingerprint": r"[a-f0-9]{64}",
        "instance_id": r"i-[a-f0-9]{17}",
        "account_id": r"[0-9]{12}",
        "region": r"[a-z]{2}-[a-z]+-[0-9]+",
    }.items():
        require(isinstance(binding[name], str) and re.fullmatch(pattern, binding[name]), "source_binding_identity")
    return binding


def physical_identity(binding):
    opener = build_opener(ProxyHandler({}))
    request = Request("http://169.254.169.254/latest/api/token", method="PUT",
                      headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
    with opener.open(request, timeout=5) as response:
        token = response.read(4097)
    require(0 < len(token) <= 4096, "imds_token_limit")
    request = Request("http://169.254.169.254/latest/dynamic/instance-identity/document",
                      headers={"X-aws-ec2-metadata-token": token.decode("ascii")})
    with opener.open(request, timeout=5) as response:
        raw = response.read(16385)
    del token, request
    require(len(raw) <= 16384, "physical_identity_limit")
    identity = json.loads(raw)
    require(isinstance(identity, dict) and identity.get("instanceId") == binding["instance_id"]
            and identity.get("accountId") == binding["account_id"]
            and identity.get("region") == binding["region"]
            and identity.get("architecture") == "x86_64", "physical_identity_mismatch")
    release = {}
    for line in read_bounded("/etc/os-release").decode().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    require(release.get("ID") == "rhel" and release.get("VERSION_ID") == "9.6", "physical_release_mismatch")
    return {"instance_id": identity["instanceId"], "account_id": identity["accountId"],
            "region": identity["region"], "image_id": identity.get("imageId"),
            "platform": "rhel", "release": "9.6", "architecture": "amd64"}


def configuration_snapshot(repo_dir=Path("/etc/yum.repos.d")):
    paths = list(repo_dir.glob("*.repo*"))
    for directory in (Path("/etc/dnf/vars"), Path("/etc/yum/vars")):
        paths.extend(directory.glob("*"))
    if Path("/etc/dnf/dnf.conf").is_file():
        paths.append(Path("/etc/dnf/dnf.conf"))
    require(len(paths) <= 96, "source_configuration_file_limit")
    return {str(path): sha(read_bounded(path)) for path in sorted(paths) if path.is_file()}


def observe_profile(repo_dir=Path("/etc/yum.repos.d")):
    files = []
    active = set()
    paths = sorted(repo_dir.glob("*.repo*"))
    require(len(paths) <= 32, "repository_file_count_limit")
    for path in paths:
        raw = read_trusted(path)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(raw.decode())
        loaded = path.suffix == ".repo"
        sections = set(parser.sections())
        for section in sections:
            if loaded and parser.getboolean(section, "enabled", fallback=True):
                active.add(section)
        files.append((path, sha(raw), parser, sections, loaded))
    client = [row for row in files if CLIENT_ID in row[3] and row[1] == PROFILE_HASHES["client"]]
    eus = [row for row in files if EUS_IDS <= row[3] and row[1] == PROFILE_HASHES["eus"]]
    require(len(client) == len(eus) == 1, "target_profile_file_identity")
    selected = [(client[0], {CLIENT_ID}), (eus[0], EUS_IDS)]
    origins = {}
    for row, ids in selected:
        for repo in ids:
            parser = row[2]
            require(parser.getboolean(repo, "sslverify", fallback=True), "repository_ssl_verification")
            hosts = []
            for key in ("baseurl", "mirrorlist", "metalink"):
                for value in parser.get(repo, key, fallback="").split():
                    endpoint = urlsplit(value)
                    require(endpoint.scheme == "https" and endpoint.hostname, "repository_https_origin")
                    hosts.append(endpoint.hostname)
            require(hosts, "repository_origin_absent")
            origins[repo] = {"https_hosts": sorted(set(hosts)), "sslverify": True}
    return {"state": "target_profile_available", "repository_ids": sorted(EUS_IDS | {CLIENT_ID}),
            "profile_file_sha256": dict(PROFILE_HASHES),
            "profile_paths": {"client": str(client[0][0]), "eus": str(eus[0][0])},
            "eus_file_loaded_by_current_dnf": eus[0][4],
            "active_repository_ids": sorted(active), "origins": origins}


def vendor_snapshot():
    expected = dict(VENDOR_FILES)
    expected[CALLBACK_PATH] = CALLBACK_SHA
    observed = {path: sha(read_trusted(path)) for path in expected}
    require(observed == expected, "reviewed_vendor_code_mismatch")
    return observed


def installed_state():
    result = subprocess.run(["/usr/bin/rpm", "-qa", "--qf", "%{NAME} %{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\\n"],
                            capture_output=True, timeout=10)
    require(result.returncode == 0 and len(result.stdout) <= 1048576, "installed_state_unavailable")
    lines = sorted(result.stdout.splitlines())
    require(lines, "installed_state_empty")
    return {"packages_count": len(lines), "sha256": sha(b"\n".join(lines) + b"\n")}


def observe(mode, binding):
    require(mode in ("repository", "eligibility"), "observation_mode")
    require(os.geteuid() == 0, "vendor_root_execution_required")
    identity = physical_identity(binding)
    before = configuration_snapshot()
    vendor_before = vendor_snapshot()
    installed_before = installed_state()
    profile = observe_profile()
    eligibility = {"state": "not_measured", "switch_branch_requested": False}
    if mode == "eligibility":
        result = subprocess.run(["/usr/bin/rhui-eus-switch"], capture_output=True, timeout=15)
        require(len(result.stdout) <= OUTPUT_LIMIT and len(result.stderr) <= OUTPUT_LIMIT, "vendor_output_limit")
        require(result.returncode == 0, "vendor_no_argument_drycheck_failed")
        eligibility = {"state": "measured_vendor_drycheck", "rc": result.returncode,
                       "stdout_sha256": sha(result.stdout), "stderr_sha256": sha(result.stderr),
                       "stdout_bytes": len(result.stdout), "stderr_bytes": len(result.stderr),
                       "switch_branch_requested": False}
    after = configuration_snapshot()
    vendor_after = vendor_snapshot()
    installed_after = installed_state()
    require(after == before and vendor_after == vendor_before, "source_configuration_changed")
    require(installed_after == installed_before, "source_installed_state_changed")
    return {"schema": "samurai.rhel_eus_source_observation/v1", "observation": mode,
            "observed_at": datetime.now(timezone.utc).isoformat(), "source": binding,
            "physical_identity": identity, "measurement_euid": os.geteuid(),
            "target_profile": profile, "vendor_files": dict(VENDOR_FILES), "vendor_callback_sha256": CALLBACK_SHA,
            "corpus_manifest_digest": CORPUS_SHA, "eligibility": eligibility,
            "source_configuration_hashes_before": before, "source_configuration_hashes_after": after,
            "installed_state_before": installed_before, "installed_state_after": installed_after,
            "mode": "index-only-live-archive", "source_configuration_changed": False,
            "new_repository_requests": 0, "package_transactions": 0, "transfers_approval": False,
            "catalog_qualified": False}


def deadline(signum, frame):
    raise ObservationError("observation_deadline")


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(60)
    try:
        require(len(sys.argv) == 3, "fixed_observation_arguments")
        output = json.dumps(observe(sys.argv[1], parse_binding(sys.argv[2])), sort_keys=True, separators=(",", ":"))
        require(len(output.encode()) <= OUTPUT_LIMIT, "observation_output_limit")
        signal.alarm(0)
        print(output)
    except Exception as error:
        signal.alarm(0)
        print(json.dumps({"schema": "samurai.rhel_eus_source_observation/v1", "state": "failed",
                          "error": str(error) if isinstance(error, ObservationError) else type(error).__name__}))
        sys.exit(1)
