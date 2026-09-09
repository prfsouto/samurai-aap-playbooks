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
