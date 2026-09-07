"""Match provider volume identities to read-only lsblk observations."""
from ipaddress import ip_address


class StorageObservationError(ValueError):
    pass


def _identity(value):
    if not isinstance(value, str) or not value.strip():
        raise StorageObservationError("Volume identity is missing")
    return value.replace("-", "").lower()


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


def _find_destination_disk(provider_volume_id, observation):
    expected = _identity(provider_volume_id)
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


def destination_device(provider_volume_id, observation):
    return _find_destination_disk(provider_volume_id, observation)["name"]


def observe_destinations(bindings, observation, candidate_instance_id):
    if not isinstance(candidate_instance_id, str) or not candidate_instance_id:
        raise StorageObservationError("Candidate instance identity is missing")
    if not isinstance(bindings, list) or not bindings or not isinstance(observation, dict):
        raise StorageObservationError("Destination bindings and device observations are required")
    devices = observation.get("blockdevices")
    if not isinstance(devices, list):
        raise StorageObservationError("Block device observation is incomplete")
    result, seen_sources, seen_destinations = [], set(), set()
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get("candidate_instance_id") != candidate_instance_id:
            raise StorageObservationError("Destination does not belong to this candidate")
        source = _identity(binding.get("source_volume_stable_id"))
        destination = _identity(binding.get("destination_volume_stable_id"))
        if source == destination or source in seen_sources or destination in seen_destinations:
            raise StorageObservationError("Source and destination volume identities must be unique and distinct")
        device = _find_destination_disk(binding["destination_volume_stable_id"], observation)
        _require_unmounted(device)
        observed_size, required_size = device.get("size"), binding.get("size_bytes")
        if type(observed_size) is not int or type(required_size) is not int or required_size <= 0 or observed_size < required_size:
            raise StorageObservationError("Observed destination capacity is insufficient or unknown")
        result.append({**binding, "device_path_observed": device["name"],
                       "os_serial_observed": device["serial"],
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
                "samurai_destination_device": destination_device,
                "samurai_candidate_address": candidate_address}
