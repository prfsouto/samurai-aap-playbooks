import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

TASKS = Path(__file__).resolve().parents[1] / "playbooks/tasks"


@pytest.mark.parametrize("fail_after_key", [False, True])
def test_candidate_key_is_private_and_removed_even_after_job_failure(tmp_path, fail_after_key):
    ansible = shutil.which("ansible-playbook")
    if not ansible:
        pytest.skip("Ansible runtime is not installed")
    private = tmp_path / "access.json"
    private.write_text(json.dumps({"organization_id": 1, "executions": {"21": {
        "attempt_id": "attempt-21", "candidate_access": {"login_user": "samurai", "private_key": "test-only-private-key"}
    }}}))
    private.chmod(0o600)
    expected_hash = hashlib.sha256(b"test-only-private-key").hexdigest()
    tasks = [{"ansible.builtin.include_tasks": str(TASKS / "campaign_candidate_key.yml")},
             {"ansible.builtin.stat": {"path": "{{ data_continuity_candidate_key_file }}"}, "register": "key_stat"},
             {"ansible.builtin.slurp": {"src": "{{ data_continuity_candidate_key_file }}"}, "register": "key_content", "no_log": True},
             {"ansible.builtin.assert": {"that": ["key_stat.stat.mode == '0600'", f"key_content.content | b64decode | hash('sha256') == '{expected_hash}'"]}, "no_log": True}]
    if fail_after_key:
        tasks.append({"ansible.builtin.fail": {"msg": "Deliberate local test failure after key creation"}})
    play = [{"hosts": "localhost", "gather_facts": False, "vars": {
        "organization_id": 1, "samurai_campaign_server_execution_id": 21, "samurai_execution_id": "attempt-21",
        "aws_replacement": {"ssh_username": "samurai"},
    }, "tasks": [{"block": tasks, "always": [{"ansible.builtin.include_tasks": str(TASKS / "campaign_candidate_key_cleanup.yml")}]}]}]
    playbook = tmp_path / "key.yml"
    playbook.write_text(yaml.safe_dump(play))
    result = subprocess.run([ansible, "-i", "localhost,", "-c", "local", str(playbook)],
        env={**os.environ, "TMPDIR": str(tmp_path), "SAMURAI_CAMPAIGN_CALLBACKS_FILE": str(private)},
        capture_output=True, text=True, timeout=30)
    assert (result.returncode != 0) is fail_after_key, result.stdout + result.stderr
    assert "test-only-private-key" not in result.stdout + result.stderr
    assert list(tmp_path.rglob("samurai-candidate-*")) == []
