from pathlib import Path

import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[1]
ENV = NativeEnvironment(undefined=StrictUndefined)


def read(relative):
    return yaml.safe_load((ROOT / relative).read_text())


def test_repository_preparation_precedes_installation_and_is_redhat_scoped():
    role = read('roles/bootstrap/tasks/main.yml')
    monitoring = next(t for t in role if t['name'] == '[bootstrap] Install monitoring agent')
    assert monitoring['when'] == 'bootstrap_install_monitoring | bool'
    prepare = next(t for t in monitoring['block'] if 'ansible.builtin.include_tasks' in t)
    install = next(t for t in monitoring['block'] if 'ansible.builtin.package' in t)
    assert monitoring['block'].index(prepare) < monitoring['block'].index(install)
    assert prepare['ansible.builtin.include_tasks'] == 'monitoring_repository.yml'
    assert install['ansible.builtin.package'] == {'name': 'telegraf', 'state': 'present'}
    context = {'bootstrap_monitoring_repository': {'baseurl': 'https://packages.example.invalid/stable', 'gpgkey': 'https://packages.example.invalid/key', 'fingerprint': '1234567890ABCDEF1234567890ABCDEF12345678'}}
    for family, expected in [('RedHat', True), ('Debian', False)]:
        assert all(ENV.compile_expression(expr)(ansible_os_family=family, **context)
                   for expr in prepare['when']) is expected
    assert not all(ENV.compile_expression(expr)(ansible_os_family='RedHat', bootstrap_monitoring_repository={})
                   for expr in prepare['when'])


def test_repository_authenticates_packages_and_limits_scope_to_telegraf():
    validate, trust, configure = read('roles/bootstrap/tasks/monitoring_repository.yml')
    assert trust['ansible.builtin.rpm_key']['validate_certs'] is True
    assert trust['ansible.builtin.rpm_key']['fingerprint'] == '{{ bootstrap_monitoring_repository.fingerprint }}'
    repo = configure['ansible.builtin.yum_repository']
    assert repo['gpgcheck'] is True
    assert repo['sslverify'] is True
    assert repo['includepkgs'] == 'telegraf'
    assert repo['enabled'] is True


def test_existing_internal_mirror_can_supply_url_and_trust_without_changing_tasks():
    validate, trust, configure = read('roles/bootstrap/tasks/monitoring_repository.yml')
    mirror = {'baseurl': 'https://packages.example.invalid/rpm/$basearch',
              'gpgkey': 'https://packages.example.invalid/keys/repository.asc',
              'fingerprint': '1234567890ABCDEF1234567890ABCDEF12345678'}
    for module, key in [(trust['ansible.builtin.rpm_key'], 'fingerprint'),
                        (configure['ansible.builtin.yum_repository'], 'baseurl'),
                        (configure['ansible.builtin.yum_repository'], 'gpgkey')]:
        assert ENV.from_string(module[key]).render(bootstrap_monitoring_repository=mirror) == mirror[key]


def test_partial_repository_is_rejected_before_importing_trust():
    validate = read('roles/bootstrap/tasks/monitoring_repository.yml')[0]['ansible.builtin.assert']
    partial = {'baseurl': 'https://packages.example.invalid/stable'}
    assert not all(ENV.compile_expression(expr)(bootstrap_monitoring_repository=partial)
                   for expr in validate['that'])
    complete = read('roles/bootstrap/defaults/main.yml')['bootstrap_monitoring_repository']
    assert all(ENV.compile_expression(expr)(bootstrap_monitoring_repository=complete)
               for expr in validate['that'])
