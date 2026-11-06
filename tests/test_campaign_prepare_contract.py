from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]


def tasks():
    return yaml.safe_load((ROOT / "playbooks/campaign_prepare.yml").read_text())[0]["tasks"]


def walk(value):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk(nested)


@pytest.mark.parametrize("provider", ["aws", "azure"])
def test_preparation_excludes_cutover_and_decommission_roles(provider):
    env = Environment(undefined=StrictUndefined)
    roles = [
        env.from_string(node["ansible.builtin.include_role"]["name"]).render(samurai_destination_provider=provider)
        for node in walk(tasks()) if "ansible.builtin.include_role" in node
    ]
    assert roles == [f"{provider}_replacement", "data_sync", "bootstrap", "validation", "data_sync", "bootstrap", "data_sync"]


def test_copy_cannot_start_before_ssh_guard_and_seed_authorization():
    block = tasks()[1]["block"]
    guard = next(i for i, task in enumerate(block) if task.get("name", "").startswith("Refuse data operations"))
    seed = next(i for i, task in enumerate(block) if task.get("name") == "Open and perform seed")
    validation = next(i for i, task in enumerate(block)
                      if task.get("ansible.builtin.include_role", {}).get("name") == "validation")
    final = next(i for i, task in enumerate(block) if task.get("name") == "Open and perform final delta")
    assert guard < seed < validation < final
    assert "ansible_connection == 'ssh'" in block[guard]["ansible.builtin.assert"]["that"]
    assert block[seed]["block"][0]["vars"]["campaign_copy_operation"] == "open-seed"
    assert block[final]["block"][0]["vars"]["campaign_copy_operation"] == "open-final-delta"


def test_private_key_cleanup_is_in_the_always_path():
    cleanup = next(task["ansible.builtin.include_tasks"] for task in tasks()[1]["always"]
                   if task.get("name") == "Remove private candidate access from the execution container")
    assert cleanup["file"] == "tasks/campaign_candidate_key_cleanup.yml"
    assert cleanup["apply"]["delegate_to"] == "localhost"


def test_stage_consumes_backend_items_and_member_output_is_separate():
    stage = yaml.safe_load((ROOT / "playbooks/tasks/campaign_copy_stage.yml").read_text())
    assert stage[-1]["ansible.builtin.set_fact"]["data_sync_items"] == "{{ campaign_open_stage.json.data_sync_items }}"
    publication = yaml.safe_load((ROOT / "playbooks/tasks/campaign_publish_member.yml").read_text())[0]
    data = publication["ansible.builtin.set_stats"]["data"]
    assert set(data) == {"samurai_campaign_execution_{{ samurai_campaign_server_execution_id }}"}
