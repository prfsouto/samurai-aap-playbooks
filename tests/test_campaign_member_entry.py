import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

HELPER = Path(__file__).resolve().parents[1] / "playbooks/tasks/campaign_member_entry.yml"


@pytest.mark.parametrize("fault", [None, "two_hosts", "wrong_attempt", "source_as_target", "foreign_org"])
def test_slice_entry_loads_only_its_reserved_candidate(tmp_path, fault):
    ansible = shutil.which("ansible-playbook")
    if not ansible:
        pytest.skip("Ansible runtime is not installed")
    member = {"hostname": "rhel-candidate-21", "old_hostname": "rhel-source-21", "organization_id": 1,
              "samurai_campaign_server_execution_id": 21, "samurai_execution_id": "attempt-21",
              "samurai_source_instance_id": "i-source-21", "samurai_commit_barrier_passed": False}
    if fault == "wrong_attempt":
        member["samurai_execution_id"] = "old-attempt"
    elif fault == "source_as_target":
        member["old_hostname"] = member["hostname"]
    elif fault == "foreign_org":
        member["organization_id"] = 2
    hosts = {"rhel-candidate-21": {"ansible_connection": "local", "samurai_campaign_member": member}}
    if fault == "two_hosts":
        hosts["rhel-candidate-22"] = {"ansible_connection": "local", "samurai_campaign_member": member}
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(yaml.safe_dump({"all": {"hosts": hosts}}))
    play = [{"hosts": "all", "gather_facts": False, "tasks": [
        {"ansible.builtin.include_tasks": str(HELPER)},
        {"ansible.builtin.assert": {"that": ["new_hostname == inventory_hostname", "samurai_execution_id == 'attempt-21'",
                                             "samurai_source_instance_id == 'i-source-21'"]}},
    ]}]
    playbook = tmp_path / "entry.yml"
    playbook.write_text(yaml.safe_dump(play))
    extra = {"organization_id": 1, "samurai_campaign_attempts": {"21": "attempt-21", "22": "attempt-22"},
             "samurai_commit_barrier_passed": False, "samurai_engine_callback_auth": True}
    result = subprocess.run([ansible, "-i", str(inventory), str(playbook), "-e", json.dumps(extra)],
                            capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) is (fault is None), result.stdout + result.stderr
