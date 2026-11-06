from pathlib import Path
import os
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PLAYBOOKS = ROOT / "immutable/azure/legacy-to-immutable-rebuild/playbooks"
PUBLICATION = "07_commit_artifacts_to_git.yml"


@pytest.mark.parametrize("with_legacy_vars", [False, True])
def test_legacy_publication_path_only_reports_disabled(tmp_path, with_legacy_vars):
    evidence = tmp_path / "baseline.json"
    evidence.write_text('{"fixture": "synthetic"}')
    git_marker = tmp_path / "git-called"
    git_stub = tmp_path / "git"
    git_stub.write_text('#!/bin/sh\ntouch "$GIT_CALL_MARKER"\nexit 1\n')
    git_stub.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "GIT_CALL_MARKER": str(git_marker),
        "ANSIBLE_SHOW_CUSTOM_STATS": "true",
    }
    command = ["ansible-playbook", "-i", "localhost,", str(PLAYBOOKS / PUBLICATION)]
    if with_legacy_vars:
        command.extend([
            "-e", "git_repo_url=https://example.invalid/public.git",
            "-e", f"artifact_output_dir={tmp_path}",
        ])
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    assert '"samurai_git_committed": false' in result.stdout
    assert '"samurai_evidence_publication": "disabled"' in result.stdout
    assert not git_marker.exists()
    assert evidence.read_text() == '{"fixture": "synthetic"}'


def test_workflow_keeps_the_legacy_publication_entry_point():
    wrapper = yaml.safe_load((PLAYBOOKS / "00_full_workflow.yml").read_text())
    assert wrapper[-1]["import_playbook"] == PUBLICATION
    workflow = yaml.safe_load((PLAYBOOKS.parent / "workflow.yml").read_text())
    step = next(node for node in workflow["nodes"] if node["id"] == "commit_artifacts")
    assert step["playbook"] == f"playbooks/{PUBLICATION}"
    assert step["classification"] == "read-only"


def test_generated_inventory_is_not_tracked():
    tracked = subprocess.check_output(
        ["git", "ls-files", "--", "inventory/managed-servers/"], cwd=ROOT, text=True
    )
    assert not tracked, "Keep generated managed-server evidence in private storage"
