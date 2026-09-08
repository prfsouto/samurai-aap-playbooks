"""The teardown snapshots the provider's unique root volume before destruction."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jinja2 import StrictUndefined, UndefinedError
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[1]
ROLE = yaml.safe_load((ROOT / 'roles/decommission/tasks/main.yml').read_text())
ENV = NativeEnvironment(undefined=StrictUndefined)


def task(name, tasks=ROLE):
    for row in tasks:
        if row.get('name') == '[decommission] ' + name:
            return row
        found = task(name, row.get('block', [])) if row.get('block') else None
        if found:
            return found
    return None


def render(expression, values):
    return ENV.from_string(expression).render(**values)


def assert_checks(row, values):
    assert all(ENV.compile_expression(c)(**values) for c in row['ansible.builtin.assert']['that'])


def instance():
    return {'instance_id': 'i-0123456789abcdef0', 'state': {'name': 'running'},
            'root_device_name': '/dev/sda1', 'block_device_mappings': [
                {'device_name': '/dev/sdf', 'ebs': {'volume_id': 'vol-11111111111111111'}},
                {'device_name': '/dev/sda1', 'ebs': {'volume_id': 'vol-22222222222222222'}},
            ]}


def resolve(observed):
    values = {'old_server': {'instance_id': 'i-0123456789abcdef0'},
              'decommission_instances': {'instances': observed}, 'region': 'us-east-2'}
    assert_checks(task('Require an unambiguous provider observation'), values)
    active = render(task('Record whether cloud cleanup is still needed')['ansible.builtin.set_fact']['decommission_target_active'], values)
    if not active:
        return None
    values['decommission_root_volume_ids'] = render(
        task('Resolve the root volume stable ID')['ansible.builtin.set_fact']['decommission_root_volume_ids'], values)
    assert_checks(task('Require exactly one EBS root volume'), values)
    return render(task('Snapshot root volume before destroy')['amazon.aws.ec2_snapshot']['volume_id'], values)


def test_snapshot_uses_stable_root_volume_id_not_a_guessed_device_path():
    assert resolve([instance()]) == 'vol-22222222222222222'
    options = task('Snapshot root volume before destroy')['amazon.aws.ec2_snapshot']
    assert 'instance_id' not in options and 'device_name' not in options
    assert options['wait'] is True
    for name, module in [('Observe the exact instance before cloud cleanup', 'amazon.aws.ec2_instance_info'),
                         ('Snapshot root volume before destroy', 'amazon.aws.ec2_snapshot'),
                         ('Stop and terminate old instance', 'amazon.aws.ec2_instance')]:
        assert render(task(name)[module]['region'], {'region': 'us-east-2'}) == 'us-east-2'


@pytest.mark.parametrize('fault', ['foreign', 'ambiguous_instance', 'missing_root', 'ambiguous_root', 'missing_volume', 'missing_state'])
def test_missing_or_ambiguous_identity_refuses_snapshot(fault):
    row = instance()
    observed = [row]
    if fault == 'foreign':
        row['instance_id'] = 'i-11111111111111111'
    elif fault == 'ambiguous_instance':
        observed.append(deepcopy(row))
    elif fault == 'missing_root':
        row['block_device_mappings'] = row['block_device_mappings'][:1]
    elif fault == 'ambiguous_root':
        row['block_device_mappings'].append(deepcopy(row['block_device_mappings'][1]))
    elif fault == 'missing_volume':
        del row['block_device_mappings'][1]['ebs']['volume_id']
    else:
        del row['state']
    with pytest.raises((AssertionError, UndefinedError)):
        resolve(observed)


@pytest.mark.parametrize('observed', [[], [{'instance_id': 'i-0123456789abcdef0', 'state': {'name': 'terminated'}}]])
def test_already_absent_or_terminated_instance_does_not_snapshot_again(observed):
    assert resolve(observed) is None


@pytest.mark.parametrize('snapshot,destroy,expected', [(False, False, False), (True, False, True), (False, True, True)])
def test_inventory_only_cleanup_does_not_require_aws_observation(snapshot, destroy, expected):
    env = NativeEnvironment(undefined=StrictUndefined)
    # Inputs here are booleans, matching the caller's JSON contract.
    env.filters['bool'] = bool
    values = {'old_server': {'instance_id': 'i-0123456789abcdef0'},
              'decommission_keep_snapshot': snapshot, 'decommission_destroy_instance': destroy}
    for name in ['Observe the exact instance before cloud cleanup',
                 'Require an unambiguous provider observation',
                 'Record whether cloud cleanup is still needed']:
        assert all(env.compile_expression(c)(**values) for c in task(name)['when']) is expected


@pytest.mark.parametrize('region', [None, '', '   '])
def test_governed_cloud_cleanup_refuses_missing_region(region):
    with pytest.raises((AssertionError, UndefinedError)):
        assert_checks(task('Require the governed cloud region'), {'region': region})


def test_governed_cloud_cleanup_accepts_explicit_region():
    assert_checks(task('Require the governed cloud region'), {'region': 'us-east-2'})


def test_legacy_cleanup_without_campaign_context_keeps_environment_region_contract():
    condition = task('Require the governed cloud region')['when'][0]
    assert ENV.compile_expression(condition)() is False
    assert ENV.compile_expression(condition)(samurai_campaign_id=7) is True
