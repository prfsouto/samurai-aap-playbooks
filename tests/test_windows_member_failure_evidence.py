from copy import deepcopy
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("residues", [[], ["disk-protection:vol-destination"]])
def test_windows_preflight_failure_preserves_member_cloud_source_evidence(tmp_path, residues):
    ansible = shutil.which("ansible-playbook")
    if not ansible:
        pytest.skip("Ansible runtime is not installed")
    windows = yaml.safe_load((ROOT / "roles/data_sync/tasks/replicate_windows.yml").read_text())
    initialize = deepcopy(next(task for task in windows if task.get("name") ==
                               "[data_sync] Select the durable Windows journal and staged executable"))
    cleanup = deepcopy(next(task for task in windows[2]["always"] if task.get("name") ==
                            "[data_sync] Track unremoved executable for the commit hygiene gate"))
    publication = deepcopy(yaml.safe_load((ROOT / "playbooks/tasks/campaign_publish_member.yml").read_text())[0])
    publication["register"] = "member_publication"
    tasks = [initialize, {
        "block": [{"ansible.builtin.fail": {"msg": "Snapshot HTTPS transport failed before copying"}}],
        "rescue": [{"ansible.builtin.set_fact": {"preflight_failed": True}}],
        "always": [cleanup, publication],
    }, {"ansible.builtin.assert": {"that": [
        "preflight_failed is sameas true",
        "member_publication.ansible_stats.data.samurai_campaign_execution_37.data_sync_used_cloud_source is sameas true",
        "member_publication.ansible_stats.data.samurai_campaign_execution_37.preparation_completed is sameas false",
        "member_publication.ansible_stats.data.samurai_campaign_execution_37.validation_passed is sameas false",
        "member_publication.ansible_stats.data.samurai_campaign_execution_37.data_sync_volume_results == []",
        "member_publication.ansible_stats.data.samurai_campaign_execution_37.data_sync_residues == data_sync_cloud_residue",
        "member_publication.ansible_stats.data.samurai_campaign_execution_37.replacement.instance_id == 'i-candidate'",
    ]}}]
    variables = {
        "organization_id": 1, "samurai_campaign_server_execution_id": 37,
        "samurai_execution_id": "candidate-attempt", "new_hostname": "win-candidate",
        "replacement": {"instance_id": "i-candidate"}, "data_sync_source_snapshot": "snapshot-source",
        "data_sync_destination_volume_id": "vol-destination", "data_sync_cloud_residue": residues,
        "data_sync_windows_binary_observation": {"stat": {"exists": False}},
    }
    playbook = tmp_path / "failed-windows-preflight.yml"
    playbook.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False,
                                         "vars": variables, "tasks": tasks}]))
    result = subprocess.run([ansible, "-i", "localhost,", "-c", "local", str(playbook)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
