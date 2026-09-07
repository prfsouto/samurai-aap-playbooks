"""An observed destination must be unique, unmounted and large enough to copy."""
import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_PLAYBOOKS = _ROOT / "ansible" / "playbooks"
if not _PLAYBOOKS.is_dir():
    _PLAYBOOKS = _ROOT / "playbooks"
_SPEC = importlib.util.spec_from_file_location("data_continuity_storage", _PLAYBOOKS / "filter_plugins/data_continuity_storage.py")
_STORAGE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_STORAGE)


def inputs():
    return ([{"source_volume_stable_id": "vol-source", "destination_volume_stable_id": "vol-destination",
              "destination_slot_id": "data", "candidate_instance_id": "i-candidate", "size_bytes": 20,
              "replication_execution_id": None}],
            {"blockdevices": [{"name": "/dev/nvme2n1", "serial": "voldestination", "size": 20,
                               "type": "disk", "fstype": None, "mountpoints": [None]}]})


def test_observation_preserves_binding_and_publishes_measured_serial_and_path():
    bindings, observed = inputs()
    original = deepcopy(bindings)
    result = _STORAGE.observe_destinations(bindings, observed, "i-candidate")
    assert result[0] == {**bindings[0], "device_path_observed": "/dev/nvme2n1",
                         "os_serial_observed": "voldestination", "device_size_bytes_observed": 20}
    assert bindings == original
    assert result[0]["replication_execution_id"] is None


@pytest.mark.parametrize("fault", ["source_is_destination", "foreign_candidate", "duplicate_source", "duplicate_destination", "missing_disk", "duplicate_disk", "mounted", "mounted_child", "unknown_mounts", "undersized", "unknown_size", "partition", "relative_device"])
def test_unsafe_or_ambiguous_destination_is_refused(fault):
    bindings, observed = inputs()
    disk = observed["blockdevices"][0]
    if fault == "source_is_destination":
        bindings[0]["source_volume_stable_id"] = "voldestination"
    elif fault == "foreign_candidate":
        bindings[0]["candidate_instance_id"] = "i-other"
    elif fault == "duplicate_source":
        bindings *= 2
    elif fault == "duplicate_destination":
        bindings.append({**bindings[0], "source_volume_stable_id": "vol-other"})
    elif fault == "missing_disk":
        observed["blockdevices"] = []
    elif fault == "duplicate_disk":
        observed["blockdevices"] *= 2
    elif fault == "mounted":
        disk["mountpoints"] = ["/data"]
    elif fault == "mounted_child":
        disk["children"] = [{"mountpoints": ["/data"]}]
    elif fault == "unknown_mounts":
        del disk["mountpoints"]
    elif fault == "undersized":
        disk["size"] = 19
    elif fault == "unknown_size":
        disk["size"] = None
    elif fault == "partition":
        disk["type"] = "part"
    elif fault == "relative_device":
        disk["name"] = "nvme2n1"
    with pytest.raises(_STORAGE.StorageObservationError):
        _STORAGE.observe_destinations(bindings, observed, "i-candidate")


@pytest.mark.parametrize("address", ["127.0.0.2", "::1", "169.254.169.254", "0.0.0.0", "224.0.0.1", "10.0.0.1", "host -oProxyCommand=bad", "fe80::1%eth0"])
def test_candidate_address_cannot_reach_source_metadata_or_controller(address):
    with pytest.raises(_STORAGE.StorageObservationError):
        _STORAGE.candidate_address(address, ["10.0.0.1"])


def test_private_candidate_address_is_valid_when_distinct_from_source():
    assert _STORAGE.candidate_address("10.0.0.2", ["10.0.0.1", ""]) == "10.0.0.2"


def test_bootstrap_resolves_the_current_device_even_after_device_renumbering():
    _, observed = inputs()
    observed["blockdevices"][0].update(name="/dev/nvme8n1", mountpoints=["/data"])
    observed["blockdevices"].append({"name": "/dev/nvme2n1", "serial": "vol-other", "type": "disk"})
    assert _STORAGE.destination_device("vol-destination", observed) == "/dev/nvme8n1"


def test_bootstrap_refuses_duplicate_provider_serials():
    _, observed = inputs()
    observed["blockdevices"] *= 2
    with pytest.raises(_STORAGE.StorageObservationError, match="exactly one"):
        _STORAGE.destination_device("vol-destination", observed)
