import json
import os
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "playbooks/tasks"
SECRET = "fixture-private-callback-token"


@pytest.mark.parametrize("file,operation,result_name", [
    ("campaign_copy_stage.yml", "open-seed", "campaign_open_stage"),
    ("campaign_abort_release.yml", "release-quiesce", "campaign_abort_release"),
])
def test_real_private_uri_failure_emits_only_scoped_outcome(tmp_path, file, operation, result_name):
    executable = shutil.which("ansible-playbook")
    assert executable, "Use the existing project Ansible runtime"
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers.get("Authorization")))
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            data = json.dumps({"pending": False, "opened": False,
                               "reason": SECRET, "url": "https://private.example/?sig=private-url"}).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        tasks = yaml.safe_load((TASKS / file).read_text())
        private = next(task for task in tasks if "block" in task)
        private["rescue"][0]["ansible.builtin.include_tasks"] = str(TASKS / "campaign_callback_diagnostic.yml")
        play = [{"hosts": "localhost", "gather_facts": False, "vars": {
            "organization_id": 1, "samurai_campaign_server_execution_id": 40,
            "samurai_execution_id": "rebuild-48-1-fixture", "samurai_source_instance_id": "i-fixture",
            "campaign_copy_operation": operation,
            "samurai_engine_base_url": f"http://127.0.0.1:{server.server_port}",
            "data_continuity_callback_headers": {"Authorization": "Bearer " + SECRET},
        }, "tasks": [private]}]
        path = tmp_path / "failure.yml"
        path.write_text(yaml.safe_dump(play, sort_keys=False))
        result = subprocess.run([executable, "-i", "localhost,", "-c", "local", str(path)],
                                capture_output=True, text=True, timeout=40,
                                env={**os.environ, "ANSIBLE_NOCOLOR": "1"})
        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert requests == [(f"/campaign-executions/40/data-continuity/{operation}", "Bearer " + SECRET)]
        assert SECRET not in output and "private-url" not in output and "private.example" not in output
        match = re.search(r'"msg": (\{.*?\n    \})', output, re.S)
        assert match, output
        diagnostic = json.loads(match.group(1))
        assert diagnostic["http_status"] == 503
        assert diagnostic["operation"] == operation
        assert diagnostic["organization_id"] == 1
        assert diagnostic["campaign_execution_id"] == 40
        assert diagnostic["error_kind"] == "http"
        assert diagnostic["bytes_processed"] == 0
        assert "headers" not in diagnostic and "reason" not in diagnostic
    finally:
        server.shutdown()
        server.server_close()
