#!/usr/bin/python
"""Delete only candidate artifacts proven by the frozen campaign cleanup manifest."""
from __future__ import annotations

import re

from botocore.exceptions import ClientError, WaiterError


_VOLUME = re.compile(r"vol-[0-9a-f]+")
_SNAPSHOT = re.compile(r"snap-[0-9a-f]+")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ACCOUNT = re.compile(r"[0-9]{12}")
_GIB = 1024 ** 3


class CandidateCleanupRefused(RuntimeError):
    """Provider state or ownership does not permit candidate artifact deletion."""


def _require(condition, reason):
    if not condition:
        raise CandidateCleanupRefused(reason)


def _tags(rows):
    _require(isinstance(rows, list), "Provider tags are incomplete")
    result = {}
    for row in rows:
        _require(isinstance(row, dict) and isinstance(row.get("Key"), str)
                 and isinstance(row.get("Value"), str), "Provider tags are invalid")
        _require(row["Key"] not in result, "Provider tags are ambiguous")
        result[row["Key"]] = row["Value"]
    return result


def _optional_volume(ec2, volume_id):
    try:
        rows = ec2.describe_volumes(VolumeIds=[volume_id]).get("Volumes")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "InvalidVolume.NotFound":
            return None
        raise
    _require(isinstance(rows, list) and len(rows) <= 1, "Candidate volume observation is ambiguous")
    return rows[0] if rows else None


def _optional_snapshot(ec2, snapshot_id):
    try:
        rows = ec2.describe_snapshots(SnapshotIds=[snapshot_id]).get("Snapshots")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "InvalidSnapshot.NotFound":
            return None
        raise
    _require(isinstance(rows, list) and len(rows) <= 1, "Root snapshot observation is ambiguous")
    return rows[0] if rows else None


def _manifest(manifest, *, account_id, instance_id):
    _require(isinstance(manifest, dict) and manifest.get("schema") == "samurai.aws-candidate-cleanup/v1",
             "Candidate cleanup manifest is missing or unsupported")
    manifest_account = manifest.get("account_id")
    if type(manifest_account) is int:
        manifest_account = str(manifest_account)
    _require(isinstance(account_id, str) and _ACCOUNT.fullmatch(account_id)
             and isinstance(manifest_account, str) and _ACCOUNT.fullmatch(manifest_account),
             "Candidate cleanup account identity is invalid")
    _require(type(manifest.get("organization_id")) is int and manifest["organization_id"] > 0
             and type(manifest.get("campaign_id")) is int and manifest["campaign_id"] > 0
             and type(manifest.get("execution_id")) is int and manifest["execution_id"] > 0
             and type(manifest.get("candidate_managed_server_id")) is int
             and manifest["candidate_managed_server_id"] > 0,
             "Candidate cleanup identity is incomplete")
    _require(manifest_account == account_id and manifest.get("candidate_instance_id") == instance_id,
             "Candidate cleanup account or instance changed")
    _require(type(manifest.get("plan_id")) is int and manifest["plan_id"] > 0
             and type(manifest.get("plan_revision")) is int and manifest["plan_revision"] > 0
             and isinstance(manifest.get("plan_digest"), str)
             and _DIGEST.fullmatch(manifest["plan_digest"]),
             "Candidate cleanup plan authority is invalid")
    volumes = manifest.get("volumes")
    snapshots = manifest.get("previous_snapshots")
    _require(isinstance(volumes, list) and isinstance(snapshots, list),
             "Candidate cleanup artifact lists are incomplete")
    volume_ids, snapshot_ids = set(), set()
    for volume in volumes:
        _require(isinstance(volume, dict) and isinstance(volume.get("id"), str)
                 and _VOLUME.fullmatch(volume["id"])
                 and isinstance(volume.get("source_volume_stable_id"), str)
                 and volume["source_volume_stable_id"]
                 and isinstance(volume.get("destination_slot_id"), str)
                 and volume["destination_slot_id"]
                 and type(volume.get("size_bytes")) is int and volume["size_bytes"] > 0
                 and volume["size_bytes"] % _GIB == 0
                 and isinstance(volume.get("encryption_key_id"), str)
                 and volume["encryption_key_id"]
                 and isinstance(volume.get("availability_zone"), str)
                 and volume["availability_zone"],
                 "Candidate data volume manifest is invalid")
        _require(volume["id"] not in volume_ids, "Candidate data volume identities are duplicated")
        volume_ids.add(volume["id"])
    for snapshot in snapshots:
        _require(isinstance(snapshot, dict) and isinstance(snapshot.get("id"), str)
                 and _SNAPSHOT.fullmatch(snapshot["id"])
                 and isinstance(snapshot.get("volume_id"), str)
                 and _VOLUME.fullmatch(snapshot["volume_id"])
                 and isinstance(snapshot.get("execution_id"), str)
                 and snapshot["execution_id"]
                 and snapshot.get("description") == f"samurai-decommission {snapshot['execution_id']}",
                 "Prior root snapshot manifest is invalid")
        _require(snapshot["id"] not in snapshot_ids, "Prior root snapshot identities are duplicated")
        snapshot_ids.add(snapshot["id"])
    return volumes, snapshots


def _verify_volume(row, *, volume, manifest):
    _require(isinstance(row, dict) and row.get("VolumeId") == volume["id"],
             "Candidate volume ID differs from the manifest")
    tags = _tags(row.get("Tags"))
    expected = {
        "managed_by": "samurai-shield",
        "samurai_organization_id": str(manifest["organization_id"]),
        "samurai_campaign_server_execution_id": str(manifest["execution_id"]),
        "samurai_plan_id": str(manifest["plan_id"]),
        "samurai_plan_revision": str(manifest["plan_revision"]),
        "samurai_plan_digest": manifest["plan_digest"],
        "samurai_source_volume_stable_id": volume["source_volume_stable_id"],
        "samurai_destination_slot_id": volume["destination_slot_id"],
    }
    _require(all(tags.get(key) == value for key, value in expected.items()),
             "Candidate data volume provider ownership differs from the plan")
    _require(row.get("State") == "available" and row.get("Attachments") == []
             and row.get("Encrypted") is True
             and type(row.get("Size")) is int
             and row["Size"] * _GIB == volume["size_bytes"]
             and row.get("KmsKeyId") == volume["encryption_key_id"]
             and row.get("AvailabilityZone") == volume["availability_zone"],
             "Candidate data volume is attached or differs from frozen geometry")


def _verify_snapshot(row, *, snapshot, account_id):
    _require(isinstance(row, dict) and row.get("SnapshotId") == snapshot["id"]
             and row.get("OwnerId") == account_id
             and row.get("VolumeId") == snapshot["volume_id"]
             and row.get("Description") == snapshot["description"]
             and row.get("State") == "completed",
             "Prior root snapshot provider identity is incomplete")
    tags = _tags(row.get("Tags"))
    _require(tags.get("managed_by") == "samurai-shield"
             and tags.get("samurai_execution_id") == snapshot["execution_id"],
             "Prior root snapshot provider ownership differs from AAP evidence")


def reconcile_candidate_artifacts(ec2, *, manifest, account_id, instance_id):
    """Require exact provider ownership, delete, then independently observe absence."""
    volumes, snapshots = _manifest(manifest, account_id=account_id, instance_id=instance_id)
    present_volumes = []
    present_snapshots = []
    for volume in volumes:
        row = _optional_volume(ec2, volume["id"])
        if row is not None:
            _verify_volume(row, volume=volume, manifest=manifest)
            present_volumes.append(volume)
    for snapshot in snapshots:
        row = _optional_snapshot(ec2, snapshot["id"])
        if row is not None:
            _verify_snapshot(row, snapshot=snapshot, account_id=account_id)
            present_snapshots.append(snapshot)
    for volume in present_volumes:
        try:
            ec2.delete_volume(VolumeId=volume["id"])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "InvalidVolume.NotFound":
                raise
        ec2.get_waiter("volume_deleted").wait(
            VolumeIds=[volume["id"]], WaiterConfig={"Delay": 5, "MaxAttempts": 24},
        )
        _require(_optional_volume(ec2, volume["id"]) is None,
                 "Candidate data volume deletion is not confirmed")
    for snapshot in present_snapshots:
        try:
            ec2.delete_snapshot(SnapshotId=snapshot["id"])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "InvalidSnapshot.NotFound":
                raise
        _require(_optional_snapshot(ec2, snapshot["id"]) is None,
                 "Prior root snapshot deletion is not confirmed")
    changed = bool(present_volumes or present_snapshots)
    return {
        "changed": changed,
        "cloud_cleanup": {
            "schema": "samurai.aws-candidate-cleanup/v1",
            "ready": True,
            "organization_id": manifest["organization_id"],
            "execution_id": manifest["execution_id"],
            "instance_id": instance_id,
            "account_id": account_id,
            "volume_ids_absent": sorted(volume["id"] for volume in volumes),
            "snapshot_ids_absent": sorted(snapshot["id"] for snapshot in snapshots),
            "backup_requested": False,
        },
    }


def main():
    from ansible.module_utils.basic import AnsibleModule
    import boto3

    module = AnsibleModule(argument_spec={
        "manifest": {"type": "dict", "required": True},
        "region": {"type": "str", "required": True},
        "account_id": {"type": "str", "required": True},
        "instance_id": {"type": "str", "required": True},
    })
    args = module.params
    try:
        session = boto3.session.Session(region_name=args["region"])
        observed_account = session.client("sts").get_caller_identity().get("Account")
        _require(observed_account == args["account_id"],
                 "Candidate cleanup AWS account differs from frozen authority")
        result = reconcile_candidate_artifacts(
            session.client("ec2"), manifest=args["manifest"],
            account_id=args["account_id"], instance_id=args["instance_id"],
        )
    except (CandidateCleanupRefused, ClientError, WaiterError) as exc:
        if isinstance(exc, CandidateCleanupRefused):
            module.fail_json(msg=str(exc), code="CANDIDATE_CLEANUP_REFUSED")
        elif isinstance(exc, ClientError):
            module.fail_json(msg="AWS candidate cleanup provider call failed",
                             code=exc.response.get("Error", {}).get("Code"))
        else:
            module.fail_json(msg="AWS candidate cleanup verification timed out",
                             code="CANDIDATE_CLEANUP_TIMEOUT")
    module.exit_json(**result)


if __name__ == "__main__":
    main()
