from pathlib import Path

import pytest
import yaml
from jinja2 import StrictUndefined, UndefinedError
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[1]
PLAY = yaml.safe_load((ROOT / "playbooks/campaign_prepare.yml").read_text())[0]
TASKS = PLAY["tasks"][1]["block"]
CONTEXT = next(task for task in TASKS if task["name"] == "Bind data copy to the observed candidate")
CLOUD = yaml.safe_load((ROOT / "roles/data_sync/tasks/cloud_source.yml").read_text())
ENV = NativeEnvironment(undefined=StrictUndefined)


def test_cloud_source_consumes_the_observed_candidate_context():
    replacement = {"instance_id": "i-0123456789abcdef0", "region": "us-east-2",
                   "describe_instances": {"Placement": {"AvailabilityZone": "us-east-2b"}}}
    values = {key: ENV.from_string(expr).render(replacement=replacement)
              for key, expr in CONTEXT["ansible.builtin.set_fact"].items()}
    assert values == {"instance_id": replacement["instance_id"], "data_sync_cloud_region": "us-east-2",
                      "data_sync_cloud_zone": "us-east-2b", "data_sync_org_tag": "samurai_organization_id"}
    create = next(t["amazon.aws.ec2_vol"] for t in CLOUD if t["name"].endswith("Create an ephemeral volume from the snapshot"))
    attach = next(t["amazon.aws.ec2_vol"] for t in CLOUD if t["name"].endswith("Attach it to the candidate, read-only for the engine"))
    assert ENV.from_string(create["zone"]).render(**values) == "us-east-2b"
    assert ENV.from_string(create["region"]).render(**values) == "us-east-2"
    assert ENV.from_string(attach["instance"]).render(**values) == replacement["instance_id"]
    check = next(t for t in CLOUD if t["name"].endswith("Verify the snapshot belongs to this organization"))
    value = ENV.from_string(check["vars"]["_snap_org"]).render(**values,
        data_sync_snapshot_info={"snapshots": [{"tags": {"samurai_organization_id": "7"}}]})
    assert str(value) == "7"


def test_stateless_campaign_does_not_require_data_context():
    assert ENV.compile_expression(CONTEXT["when"])(data_sync_destination_intents=[]) is False


@pytest.mark.parametrize("field", ["instance_id", "region", "describe_instances"])
def test_incomplete_candidate_cannot_supply_copy_context(field):
    replacement = {"instance_id": "i-0123456789abcdef0", "region": "us-east-2",
                   "describe_instances": {"Placement": {"AvailabilityZone": "us-east-2b"}}}
    del replacement[field]
    with pytest.raises(UndefinedError):
        for expr in CONTEXT["ansible.builtin.set_fact"].values():
            str(ENV.from_string(expr).render(replacement=replacement))


@pytest.mark.parametrize("state,done,failed", [("pending", False, False), ("completed", True, False),
                                              ("error", True, True), (None, True, True)])
def test_snapshot_readiness_waits_only_for_pending_and_refuses_errors(state, done, failed):
    wait = next(t for t in CLOUD if t["name"].endswith("Wait for the governed snapshot to complete"))
    values = {"data_sync_snapshot_ready": {"snapshots": [{"state": state}]}}
    assert ENV.compile_expression(wait["until"])(**values) is done
    assert ENV.compile_expression(wait["failed_when"])(**values) is failed
