#!/usr/bin/env python3
"""Verify a Packer Azure Gallery artifact against the owning ARM resource."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID


GALLERY_VERSION = re.compile(
    r"^/subscriptions/(?P<subscription>[0-9a-f-]{36})/"
    r"resourceGroups/[A-Za-z0-9._()-]+/providers/Microsoft\.Compute/"
    r"galleries/[A-Za-z0-9._-]+/images/[A-Za-z0-9._-]+/"
    r"versions/\d+\.\d+\.\d+$",
    re.IGNORECASE,
)
ARTIFACT_ID = re.compile(r"(?m)^\d+,[^,\n]*,artifact,\d+,id,([^\r\n]+)$")


class GalleryProofError(ValueError):
    """The built artifact or ARM response cannot establish image ownership."""


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError) as exc:
        raise GalleryProofError("Azure identity is not a UUID") from exc


def gallery_id_from_packer(output: str, subscription_id: str) -> str:
    artifacts = ARTIFACT_ID.findall(output)
    if len(artifacts) != 1:
        raise GalleryProofError("Packer returned no unique Azure artifact ID")
    artifact = artifacts[0].strip()
    artifact = artifact.removeprefix("Azure.ResourceManagement.VMImage:")
    match = GALLERY_VERSION.fullmatch(artifact)
    if match is None:
        raise GalleryProofError("Packer artifact is not one Gallery version ID")
    if _uuid(match.group("subscription")) != _uuid(subscription_id):
        raise GalleryProofError("Packer artifact belongs to another subscription")
    return artifact


def _region(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def validate_gallery_response(
    response: dict,
    *,
    image_id: str,
    subscription_id: str,
    region: str,
    organization_id: str,
    execution_id: str,
) -> dict:
    match = GALLERY_VERSION.fullmatch(image_id)
    if match is None or _uuid(match.group("subscription")) != _uuid(subscription_id):
        raise GalleryProofError("Requested image is outside the target subscription")
    actual_id = str(response.get("id") or "")
    properties = response.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    replication = properties.get("replicationStatus")
    replication = replication if isinstance(replication, dict) else {}
    summary = replication.get("summary")
    summary = summary if isinstance(summary, list) else []
    storage = properties.get("storageProfile")
    storage = storage if isinstance(storage, dict) else {}
    os_disk = storage.get("osDiskImage")
    os_disk = os_disk if isinstance(os_disk, dict) else {}
    tags = response.get("tags")
    tags = tags if isinstance(tags, dict) else {}
    size = os_disk.get("sizeInGB")
    region_ready = [
        item for item in summary
        if isinstance(item, dict) and _region(item.get("region")) == _region(region)
        and str(item.get("state") or "").casefold() == "completed"
        and item.get("progress") == 100
    ]
    if actual_id.casefold() != image_id.casefold():
        raise GalleryProofError("ARM returned another Gallery version")
    if str(properties.get("provisioningState") or "").casefold() != "succeeded":
        raise GalleryProofError("Gallery version is not Succeeded")
    if _region(response.get("location")) != _region(region):
        raise GalleryProofError("Gallery version region differs from the plan")
    if str(replication.get("aggregatedState") or "").casefold() != "completed" or len(region_ready) != 1:
        raise GalleryProofError("Gallery version has no completed target-region replica")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise GalleryProofError("Gallery version OS disk size is invalid")
    if (
        str(tags.get("SamuraiOrganizationId") or "") != organization_id
        or str(tags.get("SamuraiExecutionId") or "") != execution_id
    ):
        raise GalleryProofError("Gallery version execution tags do not match the plan")
    return {
        "image_id": actual_id,
        "state": "Succeeded",
        "owner_id": _uuid(subscription_id),
        "region": region,
        "replication_state": "Completed",
        "os_disk_size_gib": size,
        "organization_id": organization_id,
        "samurai_execution_id": execution_id,
        "arm_etag": str(response.get("etag") or ""),
    }


def _read_json(request: Request) -> dict:
    try:
        with urlopen(request, timeout=30) as response:
            data = json.load(response)
    except HTTPError as exc:
        raise GalleryProofError(f"Azure returned HTTP {exc.code}") from None
    except URLError as exc:
        raise GalleryProofError("Azure endpoint is unavailable") from exc
    except json.JSONDecodeError as exc:
        raise GalleryProofError("Azure returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise GalleryProofError("Azure returned a non-object response")
    return data


def read_gallery_from_arm(image_id: str, subscription_id: str) -> dict:
    client_id = os.environ.get("AZURE_CLIENT_ID", "")
    client_secret = os.environ.get("AZURE_SECRET", "")
    tenant_id = os.environ.get("AZURE_TENANT", "")
    injected_subscription = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    tenant_id = _uuid(tenant_id)
    client_id = _uuid(client_id)
    if _uuid(injected_subscription) != _uuid(subscription_id):
        raise GalleryProofError("AAP Azure subscription differs from the plan")
    if not client_secret:
        raise GalleryProofError("AAP Azure credential has no secret")
    token_body = urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://management.azure.com/.default",
    }).encode("utf-8")
    token = _read_json(Request(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data=token_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )).get("access_token")
    if not isinstance(token, str) or not token:
        raise GalleryProofError("Azure token response has no access token")
    return _read_json(Request(
        "https://management.azure.com"
        f"{image_id}?api-version=2024-03-03&%24expand=ReplicationStatus",
        headers={"Authorization": f"Bearer {token}"},
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packer-output", required=True)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        image_id = gallery_id_from_packer(
            Path(args.packer_output).read_text(), args.subscription
        )
        proof = validate_gallery_response(
            read_gallery_from_arm(image_id, args.subscription),
            image_id=image_id,
            subscription_id=args.subscription,
            region=args.region,
            organization_id=args.organization_id,
            execution_id=args.execution_id,
        )
        output = Path(args.output)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(output, flags, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(proof, sort_keys=True) + "\n")
        print(json.dumps(proof, sort_keys=True))
        return 0
    except (GalleryProofError, OSError) as exc:
        print(f"Azure Gallery build proof refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
