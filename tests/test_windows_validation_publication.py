"""Exercise the Windows validation facts consumed by the scoped publication."""
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = ROOT / "ansible" if (ROOT / "ansible").is_dir() else ROOT


@pytest.mark.parametrize("verdict,checks_count,disks_count,allowed", [
    (True, 2, 1, True), (False, 2, 1, False), (1, 2, 1, False),
    (True, 1, 1, False), (True, 2, 0, False),
])
def test_windows_validation_publishes_only_observed_complete_health(
    tmp_path, verdict, checks_count, disks_count, allowed,
):
    ansible = shutil.which("ansible-playbook")
    if not ansible:
        pytest.skip("Ansible runtime is not installed")
    windows = yaml.safe_load((ANSIBLE / "roles/validation/tasks/windows.yml").read_text())
    guard = next(i for i, task in enumerate(windows) if task["name"] ==
                 "[validation] Refuse an incomplete Windows health observation")
    phase = deepcopy(windows[guard:])
    publication = deepcopy(yaml.safe_load(
        (ANSIBLE / "playbooks/tasks/campaign_publish_member.yml").read_text(),
    )[0])
    publication["register"] = "member_publication"
    checks = [{"name": "server_os", "status": "passed"},
              {"name": "root_free_pct", "status": "passed"}][:checks_count]
    variables = {
        "organization_id": 1, "samurai_campaign_server_execution_id": 49,
        "samurai_execution_id": "attempt-observed", "new_hostname": "win-candidate",
        "replacement": {"instance_id": "i-candidate", "source_instance_id": "i-source"},
        "validation_passed": False,
        "data_sync_windows_health": {"result": {"validation_passed": verdict, "checks": checks}},
        "data_sync_destination_bindings": [{"volume_id": "vol-candidate"}],
        "data_sync_windows_validation_disks": {"result": {"observation": {
            "blockdevices": [{"volume_id": "vol-candidate"}][:disks_count],
        }}},
    }
    assertions = [
        "member_publication.ansible_stats.data.samurai_campaign_execution_49.validation_passed is sameas " + str(allowed).lower(),
        "member_publication.ansible_stats.data.samurai_campaign_execution_49.attempt_id == 'attempt-observed'",
        "member_publication.ansible_stats.data.samurai_campaign_execution_49.organization_id == 1",
        "member_publication.ansible_stats.data.samurai_campaign_execution_49.replacement.instance_id != member_publication.ansible_stats.data.samurai_campaign_execution_49.replacement.source_instance_id",
    ]
    if allowed:
        assertions += ["validation_checks == data_sync_windows_health.result.checks",
                       "validation_refused | default(false) is sameas false"]
    else:
        assertions += ["validation_refused is sameas true"]
    tasks = [{"block": phase, "rescue": [{"ansible.builtin.set_fact": {
        "validation_refused": True,
    }}]}, publication, {"ansible.builtin.assert": {"that": assertions}}]
    playbook = tmp_path / "windows-validation-publication.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": tasks,
    }]))
    result = subprocess.run([ansible, "-i", "localhost,", "-c", "local", str(playbook)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
