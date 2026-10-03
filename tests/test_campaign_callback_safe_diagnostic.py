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


@pytest.mark.parametrize('file,operation,reason,expected_code', [
    ('campaign_copy_stage.yml', 'open-final-delta', 'Windows guest proof contains another volume',
     'WINDOWS_GUEST_VOLUME_PROOF_UNAVAILABLE'),
    ('campaign_abort_release.yml', 'release-quiesce', 'Windows abort proof includes a foreign volume',
     'WINDOWS_GUEST_VOLUME_PROOF_UNAVAILABLE'),
    ('campaign_abort_release.yml', 'release-quiesce', SECRET, 'CONTRACT_REFUSAL_UNCLASSIFIED'),
])
def test_http_200_contract_refusal_is_safe_and_correlated(tmp_path, file, operation, reason, expected_code):
    executable = shutil.which('ansible-playbook')
    assert executable, 'Use the existing project Ansible runtime'
    correlation = '00000000-1111-2222-3333-444444444444'

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            body = json.dumps({'pending': False, 'opened': False, 'released': False,
                               'reason': reason, 'token': SECRET,
                               'url': 'https://private.example/?sig=private-url'}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Correlation-ID', correlation)
            self.end_headers(); self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        tasks = [t for t in yaml.safe_load((TASKS / file).read_text()) if 'block' in t]
        for task in tasks:
            task['rescue'][0]['ansible.builtin.include_tasks'] = str(TASKS / 'campaign_callback_diagnostic.yml')
        play = [{'hosts': 'localhost', 'gather_facts': False, 'vars': {
            'organization_id': 1, 'samurai_campaign_server_execution_id': 40,
            'samurai_execution_id': 'rebuild-48-1-fixture', 'samurai_source_instance_id': 'i-fixture',
            'campaign_copy_operation': operation,
            'samurai_engine_base_url': f'http://127.0.0.1:{server.server_port}',
            'data_continuity_callback_headers': {'Authorization': 'Bearer ' + SECRET},
        }, 'tasks': tasks}]
        path = tmp_path / 'contract-refusal.yml'; path.write_text(yaml.safe_dump(play, sort_keys=False))
        result = subprocess.run([executable, '-i', 'localhost,', '-c', 'local', str(path)],
                                capture_output=True, text=True, timeout=40,
                                env={**os.environ, 'ANSIBLE_NOCOLOR': '1'})
        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert SECRET not in output and 'private.example' not in output and 'private-url' not in output
        match = re.search(r'"msg": (\{.*?\n    \})', output, re.S)
        assert match, output
        diagnostic = json.loads(match.group(1))
        assert diagnostic['http_status'] == 200 and diagnostic['reason_code'] == expected_code
        assert diagnostic['correlation_id'] == correlation
        assert diagnostic['operation'] == operation and diagnostic['bytes_processed'] == 0
        assert not diagnostic['opened'] and not diagnostic['released']
    finally:
        server.shutdown(); server.server_close()


def test_abort_waits_for_the_same_pending_release_before_requiring_evidence(tmp_path):
    executable = shutil.which('ansible-playbook')
    assert executable, 'Use the existing project Ansible runtime'
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, self.headers.get('Authorization')))
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            pending = len(calls) == 1
            body = json.dumps({'pending': pending, 'released': not pending}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        tasks = [t for t in yaml.safe_load((TASKS / 'campaign_abort_release.yml').read_text()) if 'block' in t]
        for task in tasks:
            task['rescue'][0]['ansible.builtin.include_tasks'] = str(TASKS / 'campaign_callback_diagnostic.yml')
        tasks[0]['block'][0]['delay'] = 0
        tasks[0]['block'][0]['retries'] = 2
        play = [{'hosts': 'localhost', 'gather_facts': False, 'vars': {
            'organization_id': 1, 'samurai_campaign_server_execution_id': 40,
            'samurai_execution_id': 'rebuild-48-1-fixture',
            'samurai_engine_base_url': f'http://127.0.0.1:{server.server_port}',
            'data_continuity_callback_headers': {'Authorization': 'Bearer ' + SECRET},
        }, 'tasks': tasks}]
        path = tmp_path / 'pending-release.yml'; path.write_text(yaml.safe_dump(play, sort_keys=False))
        result = subprocess.run([executable, '-i', 'localhost,', '-c', 'local', str(path)],
                                capture_output=True, text=True, timeout=40,
                                env={**os.environ, 'ANSIBLE_NOCOLOR': '1'})
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [('/campaign-executions/40/data-continuity/release-quiesce', 'Bearer ' + SECRET)] * 2
        assert SECRET not in result.stdout + result.stderr
    finally:
        server.shutdown(); server.server_close()
