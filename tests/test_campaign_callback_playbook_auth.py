import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

HELPER = Path(__file__).resolve().parents[1] / "playbooks/tasks/data_continuity_callback_auth.yml"


@pytest.mark.parametrize("execution,enabled,wrong_attempt", [(21, True, False), (22, True, False),
                                                           (21, False, False), (21, True, True)])
def test_playbook_selects_only_its_attempt_credential(tmp_path, execution, enabled, wrong_attempt):
    ansible = shutil.which("ansible-playbook")
    if not ansible:
        pytest.skip("Ansible runtime is not installed")
    private_file = tmp_path / "credentials.json"
    private_file.write_text(json.dumps({"organization_id": 1, "executions": {
        str(item): {"attempt_id": f"attempt-{item}", "tokens": {"open-seed": f"test-only-token-{item}"}}
        for item in (21, 22)
    }}))
    private_file.chmod(0o600)
    expected = f"Bearer test-only-token-{execution}" if enabled else "Bearer legacy-test-token"
    checks = [f"data_continuity_callback_headers.Authorization == '{expected}'",
              "data_continuity_callback_headers['X-Organization-Id'] == '1'"]
    checks.append("data_continuity_callback_headers['X-Samurai-Callback-Job-Id'] == '101'" if enabled
                  else "'X-Samurai-Callback-Job-Id' not in data_continuity_callback_headers")
    play = [{"hosts": "localhost", "gather_facts": False, "vars": {
        "organization_id": 1, "samurai_campaign_server_execution_id": execution,
        "samurai_execution_id": "wrong" if wrong_attempt else f"attempt-{execution}",
        "samurai_engine_callback_auth": enabled, "awx_job_id": 101,
        "data_continuity_callback_operation": "open-seed", "samurai_engine_api_token": "legacy-test-token",
    }, "tasks": [{"ansible.builtin.include_tasks": str(HELPER)},
                  {"ansible.builtin.assert": {"that": checks}, "no_log": True}]}]
    playbook = tmp_path / "auth.yml"
    playbook.write_text(yaml.safe_dump(play))
    result = subprocess.run([ansible, "-i", "localhost,", "-c", "local", str(playbook)],
                            env={**os.environ, "SAMURAI_CAMPAIGN_CALLBACKS_FILE": str(private_file)},
                            capture_output=True, text=True, timeout=30)
    assert (result.returncode != 0) is wrong_attempt, result.stdout + result.stderr
    assert "test-only-token-" not in result.stdout + result.stderr
