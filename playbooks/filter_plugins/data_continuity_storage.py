"""Match provider volume identities to read-only lsblk observations."""
from ipaddress import ip_address
from pathlib import PurePosixPath
from uuid import UUID


class StorageObservationError(ValueError):
    pass


def _identity(value):
    if not isinstance(value, str) or not value.strip():
        raise StorageObservationError("Volume identity is missing")
    return value.strip().casefold()


def _require_unmounted(device):
    mounts = device.get("mountpoints")
    if not isinstance(mounts, list) or any(mount for mount in mounts):
        raise StorageObservationError("Destination mount state is unknown or mounted")
    children = device.get("children", [])
    if not isinstance(children, list):
        raise StorageObservationError("Destination topology is incomplete")
    for child in children:
        if not isinstance(child, dict):
            raise StorageObservationError("Destination child device is invalid")
        _require_unmounted(child)


def _aws_destination_disk(provider_volume_id, observation, binding):
    expected = _identity(provider_volume_id).replace("-", "")
    devices = observation.get("blockdevices") if isinstance(observation, dict) else None
    if not isinstance(devices, list):
        raise StorageObservationError("Block device observation is incomplete")
    matches = [device for device in devices if isinstance(device, dict)
               and isinstance(device.get("serial"), str)
               and device["serial"].replace("-", "").lower() == expected]
    if len(matches) != 1:
        raise StorageObservationError("Provider destination must resolve to exactly one device")
    device = matches[0]
    if device.get("type") != "disk" or not str(device.get("name") or "").startswith("/dev/"):
        raise StorageObservationError("Destination is not an observed disk device")
    return device



def _azure_destination_disk(volume_id, observation, binding):
    if isinstance(binding, list):
        matches = [row for row in binding if isinstance(row, dict)
                   and str(row.get('destination_volume_stable_id', '')).casefold() == volume_id.casefold()]
        if len(matches) != 1:
            raise ValueError('Azure destination requires exactly its observed provider binding')
        binding = matches[0]
    cloud, host = observation.get('cloud') or {}, observation.get('host') or {}
    instance = cloud.get('instance_id')
    vm_uuid = str(UUID(host.get('product_uuid')))
    if (cloud.get('provider') != 'azure' or vm_uuid != str(UUID(cloud.get('vm_id')))
            or vm_uuid != str(UUID(binding.get('instance_uuid')))
            or not isinstance(instance, str) or instance.casefold() != str(binding.get('candidate_instance_id', '')).casefold()):
        raise ValueError('Destination guest and ARM VM identities disagree')
    profile = cloud.get('storage_profile')
    disks = profile.get('dataDisks') if isinstance(profile, dict) else None
    links = cloud.get('device_links')
    if not isinstance(disks, list) or not isinstance(links, list):
        raise ValueError('Azure guest disk attachment evidence is incomplete')
    by_id, luns = {}, set()
    for disk in disks:
        identity = (disk.get('managedDisk') or {}).get('id')
        lun = disk.get('lun')
        if isinstance(lun, str) and lun.isascii() and lun.isdecimal():
            lun = int(lun)
        if (not isinstance(identity, str) or (not isinstance(lun, int) or isinstance(lun, bool)) or lun < 0
                or identity.casefold() in by_id or lun in luns):
            raise ValueError('Azure guest disk attachments are ambiguous')
        by_id[identity.casefold()] = lun
        luns.add(lun)
    lun = by_id.get(volume_id.casefold())
    if lun is None or lun != binding.get('attached_lun'):
        raise ValueError('IMDS and ARM disagree about the destination attachment')
    matching = [link for link in links if link.get('path') == f'/dev/disk/azure/scsi1/lun{lun}']
    if len(matching) != 1 or matching[0].get('is_block') is not True:
        raise ValueError('Azure destination attachment does not resolve to one block device')
    devices = [device for device in observation.get('blockdevices', []) if device.get('name') == matching[0].get('target')]
    if len(devices) != 1 or devices[0].get('type') != 'disk':
        raise ValueError('Azure attachment does not identify one observed physical disk')
    return devices[0]


def _windows_destination_disk(volume_id, observation, provider, binding):
    cloud = observation.get('cloud') or {}
    devices = observation.get('blockdevices')
    if (cloud.get('platform') != 'windows_server' or cloud.get('provider') != provider
            or not isinstance(devices, list) or type(observation.get('organization_id')) is not int
            or observation['organization_id'] <= 0):
        raise StorageObservationError('Windows guest and tenant observation is incomplete')
    expected = _identity(volume_id)
    if isinstance(binding, list):
        owned = [row for row in binding if isinstance(row, dict)
                 and _identity(row.get('destination_volume_stable_id')) == expected]
        if len(owned) != 1:
            raise StorageObservationError('Windows destination binding is ambiguous')
        binding = owned[0]
    if (not isinstance(binding, dict) or not isinstance(cloud.get('instance_id'), str)
            or cloud['instance_id'].casefold() != str(binding.get('candidate_instance_id', '')).casefold()):
        raise StorageObservationError('Windows guest is not the bound candidate')
    if provider == 'aws':
        matches = [row for row in devices if isinstance(row, dict)
                   and str(row.get('serial') or '').replace('-', '').casefold() == expected.replace('-', '')]
    elif provider == 'azure':
        vm_uuid = str(UUID(cloud.get('vm_id')))
        if (str(UUID((observation.get('host') or {}).get('product_uuid'))) != vm_uuid
                or str(UUID(binding.get('instance_uuid'))) != vm_uuid
                or str(cloud.get('instance_id', '')).casefold() != str(binding.get('candidate_instance_id', '')).casefold()):
            raise StorageObservationError('Azure Windows guest and provider VM identities differ')
        lun = binding.get('attached_lun')
        if type(lun) is not int or lun < 0:
            raise StorageObservationError('Azure Windows attached LUN is invalid')
        data_disks = (cloud.get('storage_profile') or {}).get('dataDisks')
        if not isinstance(data_disks, list):
            raise StorageObservationError('Azure Windows disk attachment list is missing')
        attached = [disk for disk in data_disks if isinstance(disk, dict)
                    and _identity((disk.get('managedDisk') or {}).get('id')) == expected
                    and str(disk.get('lun')) == str(lun)]
        if len(attached) != 1:
            raise StorageObservationError('Azure Windows disk LUN is not owned by this VM')
        matches = [row for row in devices if isinstance(row, dict)
                   and row.get('lun') == lun]
    else:
        raise StorageObservationError('Windows destination provider is unsupported')
    if len(matches) != 1:
        raise StorageObservationError('Windows provider disk is missing or ambiguous')
    disk = matches[0]
    if (disk.get('type') != 'disk' or not str(disk.get('name') or '').startswith(r'\\.\PhysicalDrive')
            or disk.get('offline') is not True or disk.get('readonly') is not True):
        raise StorageObservationError('Windows data disk is not an isolated physical destination')
    return disk


_DESTINATION_FINDERS = {"aws": _aws_destination_disk, "azure": _azure_destination_disk}


def _find_destination_disk(provider_volume_id, observation, provider, binding=None):
    find = _DESTINATION_FINDERS.get(provider)
    if find is None:
        raise StorageObservationError("Destination provider is required and must be supported")
    try:
        if (observation.get('cloud') or {}).get('platform') == 'windows_server':
            return _windows_destination_disk(provider_volume_id, observation, provider, binding or {})
        device = find(provider_volume_id, observation, binding or {})
        if device.get('type') != 'disk' or not str(device.get('name', '')).startswith('/dev/'):
            raise StorageObservationError('Destination is not an observed disk device')
        return device
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise StorageObservationError("Destination identity could not be corroborated: " + str(exc)) from None


def require_unmounted(device):
    _require_unmounted(device)
    return device

def destination_device(provider_volume_id, observation, provider, binding=None):
    return _find_destination_disk(provider_volume_id, observation, provider, binding)["name"]


def destination_needs_unmount(provider_volume_id, observation, mount_point, provider, binding=None):
    if not isinstance(mount_point, str) or not mount_point.startswith("/"):
        raise StorageObservationError("A persistent mount point is required")
    path = PurePosixPath(mount_point)
    if ".." in path.parts or str(path) != mount_point or mount_point in {"/", "/boot", "/boot/efi", "/dev", "/proc", "/sys", "/run"}:
        raise StorageObservationError("System or ambiguous mount points cannot be unmounted")
    device = _find_destination_disk(provider_volume_id, observation, provider, binding)
    mounts = device.get("mountpoints")
    if not isinstance(mounts, list):
        raise StorageObservationError("Destination mount state is unknown")
    active = [mount for mount in mounts if mount is not None and mount != ""]
    if active and active != [mount_point]:
        raise StorageObservationError("Destination has unexpected or multiple mounts")
    children = device.get("children", [])
    if not isinstance(children, list):
        raise StorageObservationError("Destination topology is incomplete")
    for child in children:
        if not isinstance(child, dict):
            raise StorageObservationError("Destination child device is invalid")
        _require_unmounted(child)
    return bool(active)


def observe_destinations(bindings, observation, candidate_instance_id, provider):
    if not isinstance(candidate_instance_id, str) or not candidate_instance_id:
        raise StorageObservationError("Candidate instance identity is missing")
    if not isinstance(bindings, list) or not bindings or not isinstance(observation, dict):
        raise StorageObservationError("Destination bindings and device observations are required")
    devices = observation.get("blockdevices")
    if not isinstance(devices, list):
        raise StorageObservationError("Block device observation is incomplete")
    result, seen_sources, seen_destinations = [], set(), set()
    for binding in bindings:
        if not isinstance(binding, dict) or _identity(binding.get("candidate_instance_id")) != _identity(candidate_instance_id):
            raise StorageObservationError("Destination does not belong to this candidate")
        source = _identity(binding.get("source_volume_stable_id"))
        destination = _identity(binding.get("destination_volume_stable_id"))
        if source == destination or source in seen_sources or destination in seen_destinations:
            raise StorageObservationError("Source and destination volume identities must be unique and distinct")
        device = _find_destination_disk(binding["destination_volume_stable_id"], observation, provider, binding)
        _require_unmounted(device)
        observed_size, required_size = device.get("size"), binding.get("size_bytes")
        if (not isinstance(observed_size, int) or isinstance(observed_size, bool)) or (not isinstance(required_size, int) or isinstance(required_size, bool)) or required_size <= 0 or observed_size < required_size:
            raise StorageObservationError("Observed destination capacity is insufficient or unknown")
        result.append({**binding, "device_path_observed": device["name"],
                       "os_serial_observed": device.get("serial"),
                       "provider": provider,
                       "device_size_bytes_observed": observed_size})
        seen_sources.add(source)
        seen_destinations.add(destination)
    return result


def candidate_address(address, source_addresses):
    if not isinstance(address, str) or "%" in address:
        raise StorageObservationError("Candidate must have a literal IP address")
    try:
        target = ip_address(address)
        sources = {ip_address(value) for value in source_addresses if value}
    except (TypeError, ValueError) as exc:
        raise StorageObservationError("Candidate or source address is invalid") from exc
    if target.is_loopback or target.is_link_local or target.is_unspecified or target.is_multicast or target in sources:
        raise StorageObservationError("Candidate address points to a local, special or source address")
    return str(target)


class FilterModule:
    def filters(self):
        return {"samurai_observe_destinations": observe_destinations,
                "samurai_destination_observed_device": _find_destination_disk,
                "samurai_require_unmounted": require_unmounted,
                "samurai_destination_device": destination_device,
                "samurai_destination_needs_unmount": destination_needs_unmount,
                "samurai_candidate_address": candidate_address}
