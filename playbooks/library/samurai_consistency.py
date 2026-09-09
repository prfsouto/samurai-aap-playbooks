#!/usr/bin/python
"""Apply a governed filesystem fence through an Ansible execution."""
import json
import os
import re
from pathlib import Path

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.consistency_lease import FenceRejected, FilesystemFence, Journal, LinuxFilesystem


def observed_mounts(module):
    rc, output, error = module.run_command([
        'lsblk', '--json', '--bytes', '--output', 'NAME,SERIAL,FSTYPE,UUID,MOUNTPOINTS',
    ])
    if rc:
        raise FenceRejected('Source volume observation failed')
    result = {}
    mount_options = {}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        target = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[4])
        mount_options[target] = set(fields[5].split(','))
    def visit(device, serial=None):
        serial = device.get('serial') or serial
        stable = None
        if isinstance(serial, str) and re.fullmatch(r'vol-?[0-9a-f]+', serial):
            stable = 'vol-' + serial.removeprefix('vol').removeprefix('-')
        for mount in device.get('mountpoints') or []:
            if mount:
                if mount in result:
                    raise FenceRejected('Ambiguous source mount identity')
                result[mount] = dict(stable_id=stable, filesystem=device.get('fstype'), uuid=device.get('uuid'),
                                     readonly='ro' in mount_options.get(mount, set()))
        for child in device.get('children', []):
            visit(child, serial)
    for device in json.loads(output)['blockdevices']:
        visit(device)
    return result


def main():
    module = AnsibleModule(argument_spec=dict(command=dict(type='dict', required=True)), supports_check_mode=False)
    if os.geteuid() != 0:
        module.fail_json(msg='Filesystem fencing requires governed privilege escalation')
    journal = Journal()
    lock = journal.lock()
    try:
        def make_writable(mount):
            rc, _, _ = module.run_command(['mount', '-o', 'remount,rw', '--', mount])
            if rc:
                raise FenceRejected('Candidate write activation failed')
        fence = FilesystemFence(store=journal, filesystem=LinuxFilesystem(lambda: observed_mounts(module), make_writable),
                                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        evidence = fence.execute(module.params['command'])
        module.exit_json(changed=True, consistency_observation=evidence)
    except (FenceRejected, OSError, ValueError, KeyError, TypeError) as exc:
        module.fail_json(msg=str(exc))
    finally:
        os.close(lock)


if __name__ == '__main__':
    main()
