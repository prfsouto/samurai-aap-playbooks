from pathlib import Path

import jmespath
import pytest
import yaml
from jinja2 import StrictUndefined, UndefinedError
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[1]
ROLE = yaml.safe_load((ROOT / "roles/aws_replacement/tasks/main.yml").read_text())
DESTINATION = yaml.safe_load((ROOT / "roles/aws_replacement/tasks/destination_volumes.yml").read_text())
ENV = NativeEnvironment(undefined=StrictUndefined)


def command_query():
    tasks = [task for block in ROLE for task in block.get("block", [])]
    argv = next(task["ansible.builtin.command"]["argv"] for task in tasks
                if "PublicIp:PublicIpAddress" in str(task.get("ansible.builtin.command", {})))
    return argv[argv.index("--query") + 1]


def zone_expression():
    argv = next(task["ansible.builtin.command"]["argv"] for task in DESTINATION
                if task["name"].endswith("Create the empty destination volume"))
    return argv[argv.index("--availability-zone") + 1]


def test_destination_zone_comes_from_the_actual_candidate_projection():
    response = {"Reservations": [{"Instances": [{"InstanceId": "i-0123456789abcdef0",
                "Placement": {"AvailabilityZone": "us-east-2b"}}]}]}
    proof = jmespath.search(command_query(), response)[0]
    rendered = ENV.from_string(zone_expression()).render(
        aws_replacement_describe_proof=proof, aws_replacement_availability_zone="us-east-2a")
    assert rendered == "us-east-2b"
    assert proof["InstanceId"] == "i-0123456789abcdef0"


@pytest.mark.parametrize("proof", [{}, {"Placement": {}}, {"Placement": None}])
def test_missing_candidate_zone_cannot_use_an_unobserved_fallback(proof):
    with pytest.raises(UndefinedError):
        str(ENV.from_string(zone_expression()).render(
            aws_replacement_describe_proof=proof, aws_replacement_availability_zone="us-east-2a"))
