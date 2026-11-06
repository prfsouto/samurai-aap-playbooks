from hashlib import sha256
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "playbooks/remediation_candidates/debian12_brotli_dla_4855"


def test_candidate_syntax():
    subprocess.run(
        [sys.executable, "-m", "ansible.cli.playbook", "-i", "localhost,",
         "--syntax-check", str(CANDIDATE / "remediate.yml")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_artifact_and_license_match_provenance():
    manifest = yaml.safe_load((CANDIDATE / "provenance.yml").read_text())
    play = yaml.safe_load((CANDIDATE / "remediate.yml").read_text())[0]
    assert play["hosts"] == "samurai_remediation_lab"
    assert play["vars"]["brotli_asset_path"].endswith(manifest["artifact"]["name"])
    assert play["vars"]["brotli_asset_sha256"] == manifest["artifact"]["sha256"]
    assert sha256((CANDIDATE / "LICENSE").read_bytes()).hexdigest() == manifest["upstream"]["license_sha256"]


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1.0.9-2", 1),
        ("1.0.9-2+deb12u1~test1", 1),
        ("1.0.9-2+deb12u1", 0),
        ("1.0.9-2+deb12u2", 0),
    ],
)
def test_advisory_floor_uses_debian_version_order(version, expected):
    play = yaml.safe_load((CANDIDATE / "remediate.yml").read_text())[0]
    floor = play["vars"]["brotli_fixed_version"]
    result = subprocess.run(["dpkg", "--compare-versions", version, "ge", floor], check=False)
    assert result.returncode == expected
