from pathlib import Path
import yaml


def test_template_uses_the_importer_list_contract():
    path = Path(__file__).resolve().parents[1] / 'workflow_templates/samurai_consistency_job_template.yml'
    templates = yaml.safe_load(path.read_text())['job_templates']
    assert len(templates) == 1
    template = templates[0]
    assert template['project'] == 'samurai_immutable'
    assert template['playbook'] == 'playbooks/data_consistency.yml'
    assert template['ask_inventory_on_launch'] is True
    assert template['ask_credential_on_launch'] is True
    assert template['ask_variables_on_launch'] is True


def test_windows_consistency_preserves_the_required_nested_wire_members():
    plays = yaml.safe_load((Path(__file__).resolve().parents[1] / 'playbooks/data_consistency.yml').read_text())
    task = next(t for p in plays for t in p.get('tasks', []) if 'ansible.windows.win_powershell' in t)
    wire = {'consistency_observation': {'scope': {'organization_id': 1},
            'observed_volumes': [{'stable_id': 'vol-source', 'partition_guid': 'gpt-guid',
                                 'mount_point': 'D:\\', 'disk_size_bytes': 4294967296}]}}

    def nested_depth(value):
        if isinstance(value, dict):
            return 1 + max((nested_depth(x) for x in value.values()), default=0)
        if isinstance(value, list):
            return 1 + max((nested_depth(x) for x in value), default=0)
        return 0

    assert task['ansible.windows.win_powershell']['depth'] >= nested_depth(wire)
    assert task['register'] == 'windows_consistency_result'
