from pathlib import Path

import pytest
import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[1]
ENV = NativeEnvironment(undefined=StrictUndefined)
DEFAULTS = yaml.safe_load((ROOT / 'roles/bootstrap/defaults/main.yml').read_text())
TASKS = yaml.safe_load((ROOT / 'roles/bootstrap/tasks/main.yml').read_text())


@pytest.mark.parametrize('kind', ['monitoring', 'security'])
@pytest.mark.parametrize('requested', [None, False, True])
def test_agent_stage_requires_an_explicit_request(kind, requested):
    variable = f'samurai_install_{kind}_agent'
    context = {} if requested is None else {variable: requested}
    enabled = ENV.from_string(DEFAULTS[f'bootstrap_install_{kind}']).render(**context)
    task = next(t for t in TASKS if t.get('name') == f'[bootstrap] Install {kind} agent')
    assert task['when'] == f'bootstrap_install_{kind} | bool'
    assert enabled is (requested is True)
    if kind == 'monitoring' and enabled:
        assert task['block'][0]['ansible.builtin.package'] == {'name': 'telegraf', 'state': 'present'}
