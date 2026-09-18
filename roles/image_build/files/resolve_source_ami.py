"""Resolve one publisher-owned AMI and its root mapping for the build adapter."""
import argparse
from datetime import datetime
import json
import re
import subprocess
import sys


# AWS CLI error text known to mean the credential itself is the problem, not
# the query. Kept as a flat tuple — not a general AWS error parser, just
# enough to route the sanitized message under a code an operator can act on.
_CREDENTIAL_ERROR_MARKERS = (
    "InvalidClientTokenId",
    "AccessDenied",
    "UnauthorizedOperation",
    "ExpiredToken",
    "AuthFailure",
)


class SourceAmiResolutionError(RuntimeError):
    """Mirrors the shape of CloudContractError (ree_cloud.domain) without
    importing it — the Cloud Executor image (containers/cloud-executor/
    Dockerfile.cloud-executor) is debian-slim + python3 stdlib and does not
    package remediation-engine. backoffLimit=0 already stops any retry at
    the Job level, so `retryable` here is metadata only, not flow control."""
    code = "SOURCE_AMI_RESOLUTION_FAILED"
    retryable = False
    next_action = "inspect_and_retry"


class CredentialRejected(SourceAmiResolutionError):
    code = "CREDENTIAL_REJECTED"
    next_action = "replace_or_revalidate_credential"


def _sanitize_stderr(stderr):
    return " ".join((stderr or "").split())[:500]


def select_source(images, owner):
    if not images:
        raise ValueError("No available source AMI matches the governed publisher and filters")
    dated = []
    for image in images:
        if image.get("OwnerId") != owner or image.get("State") != "available":
            raise ValueError("Source AMI publisher or availability does not match")
        created = datetime.fromisoformat(image["CreationDate"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("Source AMI creation time has no timezone")
        dated.append((created, image))
    newest = max(created for created, _ in dated)
    matches = [image for created, image in dated if created == newest]
    if len(matches) != 1:
        raise ValueError("Newest source AMI is ambiguous")
    image = matches[0]
    image_id = image.get("ImageId", "")
    root = image.get("RootDeviceName", "")
    if not re.fullmatch(r"ami-[0-9a-f]+", image_id):
        raise ValueError("Source AMI identity is missing")
    if image.get("RootDeviceType") != "ebs" or not re.fullmatch(r"/dev/[A-Za-z0-9]+", root):
        raise ValueError("Source AMI has no valid EBS root device")
    mappings = [item for item in image.get("BlockDeviceMappings", []) if item.get("DeviceName") == root]
    if len(mappings) != 1 or not isinstance(mappings[0].get("Ebs"), dict):
        raise ValueError("Source AMI root mapping is missing or ambiguous")
    size = mappings[0]["Ebs"].get("VolumeSize")
    if type(size) is not int or size <= 0 or size > 20:
        raise ValueError("Source AMI root cannot fit the governed 20 GiB build disk")
    return {"resolved_source_ami": image_id, "root_device_name": root}


def resolve_source(*, region, owner, name_filter, aws_cli="aws"):
    if not re.fullmatch(r"[0-9]{12}", owner) or not region or not name_filter:
        raise ValueError("Governed region, publisher account and image filter are required")
    filters = [
        {"Name": "name", "Values": [name_filter]},
        {"Name": "root-device-type", "Values": ["ebs"]},
        {"Name": "virtualization-type", "Values": ["hvm"]},
        {"Name": "state", "Values": ["available"]},
    ]
    try:
        result = subprocess.run(
            [aws_cli, "ec2", "describe-images", "--region", region, "--owners", owner,
             "--filters", json.dumps(filters), "--output", "json"],
            check=True, capture_output=True, text=True, timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        raw_stderr = exc.stderr or ""
        reason = _sanitize_stderr(raw_stderr)
        error_cls = (
            CredentialRejected
            if any(marker in raw_stderr for marker in _CREDENTIAL_ERROR_MARKERS)
            else SourceAmiResolutionError
        )
        raise error_cls(
            f"aws ec2 describe-images failed (exit {exc.returncode}): {reason}"
        ) from exc
    return select_source(json.loads(result.stdout)["Images"], owner)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--name-filter", required=True)
    parser.add_argument("--aws-cli", default="aws")
    args = parser.parse_args()
    try:
        print(json.dumps(resolve_source(region=args.region, owner=args.owner, name_filter=args.name_filter, aws_cli=args.aws_cli)))
    except SourceAmiResolutionError as exc:
        sys.stderr.write(f"{exc}\n")
        sys.exit(3)


if __name__ == "__main__":
    main()
