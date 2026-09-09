import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'playbooks/module_utils'))
from consistency_boot import FstabBootFence, BootFenceRejected

VOLUMES = [{'mount_point': '/data', 'uuid': 'synthetic-uuid', 'filesystem': 'ext4'}]


def test_boot_fence_survives_new_process_and_abort_restores_original(tmp_path):
    fstab = tmp_path / 'fstab'
    original = '# original\nUUID=root / xfs defaults 0 1\nUUID=synthetic-uuid /data ext4 defaults,nofail,rw,nodev 0 2\n'
    fstab.write_text(original)
    boot = FstabBootFence(fstab)
    saved = boot.capture(VOLUMES)
    boot.protect(VOLUMES, saved)
    restarted = FstabBootFence(fstab)
    assert restarted.verify(VOLUMES)
    assert 'UUID=root / xfs defaults 0 1' in fstab.read_text()
    data_entry = next(line for line in fstab.read_text().splitlines() if '/data ' in line)
    assert 'nodev' in data_entry
    assert 'nofail' not in data_entry
    restarted.restore(saved)
    assert fstab.read_text() == original


def test_mount_without_boot_entry_gets_fail_closed_boot_protection(tmp_path):
    fstab = tmp_path / 'fstab'
    fstab.write_text('UUID=root / xfs defaults 0 1\n')
    boot = FstabBootFence(fstab)
    saved = boot.capture(VOLUMES)
    boot.protect(VOLUMES, saved)
    assert boot.verify(VOLUMES)
    boot.restore(saved)
    assert '/data' not in fstab.read_text()


def test_custom_mount_ordering_is_refused_before_fstab_mutation(tmp_path):
    fstab = tmp_path / 'fstab'
    original = 'UUID=synthetic-uuid /data ext4 defaults,x-systemd.automount 0 2\n'
    fstab.write_text(original)
    boot = FstabBootFence(fstab)
    with pytest.raises(BootFenceRejected, match='Custom'):
        boot.protect(VOLUMES, boot.capture(VOLUMES))
    assert fstab.read_text() == original


def test_boot_fence_loss_is_detected_even_without_reboot(tmp_path):
    fstab = tmp_path / 'fstab'
    fstab.write_text('UUID=synthetic-uuid /data ext4 defaults,rw 0 2\n')
    boot = FstabBootFence(fstab)
    saved = boot.capture(VOLUMES)
    boot.protect(VOLUMES, saved)
    fstab.write_text('UUID=synthetic-uuid /data ext4 defaults,rw 0 2\n')
    assert not boot.verify(VOLUMES)


@pytest.mark.parametrize('source', ['UUID=synthetic-uuid', 'LABEL=data', '/dev/disk/by-id/synthetic'])
def test_rw_alias_of_required_filesystem_is_refused_before_boot_mutation(tmp_path, source):
    fstab = tmp_path / 'fstab'
    original = source + ' /alias ext4 defaults,rw 0 2\nUUID=synthetic-uuid /data ext4 defaults,rw 0 2\n'
    fstab.write_text(original)
    boot = FstabBootFence(fstab, resolve_uuid=lambda value: 'synthetic-uuid')
    with pytest.raises(BootFenceRejected, match='aliases'):
        boot.capture(VOLUMES)
    assert fstab.read_text() == original
    assert not boot.verify(VOLUMES)


def test_candidate_activation_preserves_security_options(tmp_path):
    fstab = tmp_path / 'fstab'
    fstab.write_text('UUID=synthetic-uuid /data ext4 nodev,nosuid,noexec,ro 0 2\n')
    FstabBootFence(fstab).activate(VOLUMES)
    options = fstab.read_text().split()[3].split(',')
    assert set(options) == {'nodev', 'nosuid', 'noexec', 'rw'}
