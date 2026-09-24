"""Bind a source fence to the guest VM and its observed cloud disks."""
import json
import os
from pathlib import Path
import re
import stat
import urllib.request
from uuid import UUID


class SourceIdentityRejected(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _resource_parts(value, kind):
    parts = value.strip('/').split('/') if isinstance(value, str) and value.startswith('/') else []
    if (len(parts) != 8 or parts[0].casefold() != 'subscriptions'
            or parts[2].casefold() != 'resourcegroups'
            or parts[4].casefold() != 'providers'
            or parts[5].casefold() != 'microsoft.compute'
            or parts[6].casefold() != kind.casefold()
            or not all(parts[index] for index in (1, 3, 7))):
        raise SourceIdentityRejected('Azure source resource identity is invalid')
    return parts


def _device_target(lun):
    links = ('/dev/disk/azure/scsi1/lun' + str(lun),
             '/dev/disk/azure/data/by-lun/' + str(lun))
    roots = {os.path.realpath(link) for link in ('/dev/disk/azure/root', '/dev/disk/azure/os')
             if os.path.islink(link)}
    targets = set()
    for link in links:
        if not os.path.islink(link):
            continue
        target = os.path.realpath(link)
        try:
            valid = (target.startswith('/dev/') and stat.S_ISBLK(os.stat(target).st_mode)
                     and not os.path.exists('/sys/class/block/' + os.path.basename(target) + '/partition')
                     and target not in roots)
        except OSError:
            valid = False
        if not valid:
            raise SourceIdentityRejected('Azure data disk LUN does not resolve to a data block device')
        targets.add(target)
    if len(targets) != 1:
        raise SourceIdentityRejected('Azure data disk LUN link is absent or ambiguous')
    return targets.pop()


def azure_disk_bindings(compute, expected_instance, product_uuid, resolve=_device_target):
    if not isinstance(compute, dict):
        raise SourceIdentityRejected('Azure instance metadata is incomplete')
    expected = _resource_parts(expected_instance, 'virtualMachines')
    observed = _resource_parts(compute.get('resourceId'), 'virtualMachines')
    try:
        same_vm = str(UUID(product_uuid)) == str(UUID(compute.get('vmId')))
    except (TypeError, ValueError, AttributeError):
        same_vm = False
    if [part.casefold() for part in observed] != [part.casefold() for part in expected]:
        raise SourceIdentityRejected('Azure guest does not match the frozen source VM')
    if not same_vm:
        raise SourceIdentityRejected('Azure guest VM UUID does not match instance metadata')
    profile = compute.get('storageProfile')
    disks = profile.get('dataDisks') if isinstance(profile, dict) else None
    if not isinstance(disks, list):
        raise SourceIdentityRejected('Azure guest disk attachments are incomplete')
    bindings, seen_luns, seen_disks = {}, set(), set()
    for disk in disks:
        managed = disk.get('managedDisk') if isinstance(disk, dict) else None
        disk_id = managed.get('id') if isinstance(managed, dict) else None
        parts = _resource_parts(disk_id, 'disks')
        lun = disk.get('lun')
        if isinstance(lun, str) and lun.isascii() and lun.isdecimal():
            lun = int(lun)
        if (type(lun) is not int or lun < 0 or lun in seen_luns
                or parts[1].casefold() != expected[1].casefold()
                or disk_id.casefold() in seen_disks):
            raise SourceIdentityRejected('Azure guest disk attachment is foreign or ambiguous')
        target = resolve(lun)
        if target in bindings:
            raise SourceIdentityRejected('Azure guest LUNs resolve to the same block device')
        bindings[target] = disk_id
        seen_luns.add(lun)
        seen_disks.add(disk_id.casefold())
    return bindings


def source_disk_bindings(expected_instance):
    if not isinstance(expected_instance, str) or not expected_instance:
        raise SourceIdentityRejected('Frozen source instance identity is missing')
    chassis = Path('/sys/devices/virtual/dmi/id/board_asset_tag').read_text().strip()
    if re.fullmatch(r'i-[0-9a-f]{8,17}', expected_instance) and chassis == expected_instance:
        return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(
        'http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01',
        headers={'Metadata': 'true'},
    )
    with opener.open(request, timeout=5) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise SourceIdentityRejected('Azure instance metadata exceeds its bound')
    product_uuid = Path('/sys/class/dmi/id/product_uuid').read_text().strip()
    return azure_disk_bindings(json.loads(raw), expected_instance, product_uuid)
