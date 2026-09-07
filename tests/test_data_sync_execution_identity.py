from copy import deepcopy
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_volume_execution_identity_survives_controller_extra_var_precedence(tmp_path):
    ansible = shutil.which("ansible-playbook")
    if not ansible:
        pytest.skip("Ansible runtime is not installed")
    source = yaml.safe_load((ROOT / "roles/data_sync/tasks/replicate_item.yml").read_text())
    selected = next(task for task in source if task.get("name") == "[data_sync] Point the stage at this item")
    identity_name = "data_sync_execution_id" if "data_sync_execution_id" in selected["ansible.builtin.set_fact"] else "samurai_execution_id"
    tasks = []
    for value in ("volume-one-seed", "volume-two-seed", "volume-one-final"):
        task = deepcopy(selected)
        task["vars"] = {"data_sync_item": {"replication_execution_id": value, "source_volume_stable_id": "vol-source"},
                        "data_sync_item_destination_volume": "vol-destination", "data_sync_destination_resolution": {"stdout": "/dev/test serial"}}
        tasks.extend([task, {"ansible.builtin.assert": {"that": [f"{identity_name} == '{value}'", "samurai_execution_id == 'candidate-attempt'"]}}])
    entry = yaml.safe_load((ROOT / "roles/data_sync/tasks/main.yml").read_text())
    initializer = next(task for task in entry if task.get("name") == "[data_sync] Initialize the scalar copy execution identity")
    tasks.extend([initializer, {"ansible.builtin.assert": {"that": ["data_sync_execution_id == 'candidate-attempt'"]}}])
    playbook = tmp_path / "identity.yml"
    playbook.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "tasks": tasks}]))
    result = subprocess.run([ansible, "-i", "localhost,", "-c", "local", str(playbook), "-e", "samurai_execution_id=candidate-attempt"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
