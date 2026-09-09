"""Partial failures cannot become evidence of uninterrupted write fencing."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'playbooks/module_utils'))
from consistency_lease import FenceRejected, FilesystemFence


class Store:
    def __init__(self):
        self.record = None

    def read(self):
        return copy.deepcopy(self.record)

    def write(self, record):
        self.record = copy.deepcopy(record)


class Boot:
    def __init__(self):
        self.protected = False

    def capture(self, volumes):
        return {'/data': 'original boot entry'}

    def protect(self, volumes, original):
        self.protected = True

    def verify(self, volumes):
        return self.protected

    def restore(self, original):
        self.protected = False


class Filesystem:
    def __init__(self):
        self.frozen = set()
        self.thawed = []
        self.boot = Boot()
        self.readonly = False

    def is_readonly(self, mount):
        return self.readonly

    def make_writable(self, mount):
        self.readonly = False

    def validate(self, volumes):
        pass

    def freeze(self, mount):
        if mount in self.frozen:
            return False
        self.frozen.add(mount)
        return True

    def thaw(self, mount):
        self.frozen.discard(mount)
        self.thawed.append(mount)

    def confirm_writable(self, mount):
        assert mount not in self.frozen


def command(action='acquire', org=7, generation=2):
    return {'scope': {'organization_id': org, 'generation': generation, 'action': action,
                      'plan_digest': 'a' * 64},
            'parameters': {'volumes': [{'mount_point': '/data'}]}}


def setup():
    store, fs = Store(), Filesystem()
    return store, fs, FilesystemFence(store=store, filesystem=fs, boot_id='boot-a')


def test_response_loss_replays_existing_held_fence():
    store, fs, fence = setup()
    fence.execute(command())
    assert fence.execute(command())['state'] == 'HELD'
    assert fs.thawed == []


def test_durable_intent_precedes_kernel_mutation():
    store, fs, fence = setup()
    def crash(mount):
        assert store.record['state'] == 'ACQUIRING'
        raise RuntimeError('worker lost')
    fs.freeze = crash
    with pytest.raises(RuntimeError):
        fence.execute(command())
    with pytest.raises(FenceRejected, match='Uncertain'):
        fence.execute(command())


def test_recovery_observes_writable_host_before_reusing_uncertain_intent():
    store, fs, fence = setup()
    store.record = {
        'scope': command()['scope'], 'boot_id': 'boot-a', 'state': 'UNCERTAIN',
        'frozen': ['/data'], 'volumes': [{'mount_point': '/data'}],
        'acquired_at': None, 'protection': 'filesystem_freeze',
    }
    assert fence.execute(command('inspect_outcome'))['state'] == 'NO_FENCE'


def test_recovery_keeps_uncertain_intent_when_host_is_not_writable():
    store, fs, fence = setup()
    store.record = {
        'scope': command()['scope'], 'boot_id': 'boot-a', 'state': 'UNCERTAIN',
        'frozen': ['/data'], 'volumes': [{'mount_point': '/data'}],
        'acquired_at': None, 'protection': 'filesystem_freeze',
    }
    fs.frozen = {'/data'}
    fs.confirm_writable = lambda mount: (_ for _ in ()).throw(FenceRejected('still frozen'))
    with pytest.raises(FenceRejected, match='not held'):
        fence.execute(command('inspect_outcome'))


def test_reboot_never_reuses_prior_fence_proof():
    store, fs, fence = setup()
    fence.execute(command())
    restarted = FilesystemFence(store=store, filesystem=fs, boot_id='boot-b')
    with pytest.raises(FenceRejected, match='reboot'):
        restarted.execute(command('inspect'))


def test_stale_or_foreign_release_does_not_touch_source():
    store, fs, fence = setup()
    fence.execute(command())
    for request in (command('release_abort', org=8), command('release_abort', generation=1)):
        with pytest.raises(FenceRejected):
            fence.execute(request)
    assert fs.thawed == []


def test_transfer_keeps_source_fenced_and_refuses_abort():
    store, fs, fence = setup()
    fence.execute(command())
    assert fence.execute(command('transfer'))['state'] == 'TRANSFERRED'
    with pytest.raises(FenceRejected, match='Committed'):
        fence.execute(command('release_abort'))
    assert fs.frozen == {'/data'}
    assert fs.thawed == []


def test_abort_confirms_write_recovery():
    store, fs, fence = setup()
    fence.execute(command())
    assert fence.execute(command('release_abort'))['state'] == 'RELEASED'
    assert fs.thawed == ['/data']


def test_lost_kernel_fence_cannot_be_relabelled_held():
    store, fs, fence = setup()
    fence.execute(command())
    fs.frozen.clear()
    with pytest.raises(FenceRejected, match='lost'):
        fence.execute(command('inspect'))
    assert store.record['state'] == 'UNCERTAIN'


def test_changed_volume_manifest_cannot_release_original_fence():
    store, fs, fence = setup()
    fence.execute(command())
    request = command('release_abort')
    request['parameters']['volumes'] = [{'mount_point': '/other'}]
    with pytest.raises(FenceRejected, match='manifest'):
        fence.execute(request)
    assert fs.thawed == []


def test_reboot_retains_fence_only_with_persisted_readonly_boot_and_mount():
    store, fs, fence = setup()
    fence.execute(command())
    fs.frozen.clear()
    fs.readonly = True
    restarted = FilesystemFence(store=store, filesystem=fs, boot_id='boot-b')
    assert restarted.execute(command('inspect'))['state'] == 'HELD'
    fs.readonly = False
    with pytest.raises(FenceRejected, match='boot fence was lost'):
        restarted.execute(command('inspect'))


def test_abort_after_readonly_boot_restores_the_only_writer():
    store, fs, fence = setup()
    fence.execute(command())
    fs.frozen.clear()
    fs.readonly = True
    restarted = FilesystemFence(store=store, filesystem=fs, boot_id='boot-b')
    assert restarted.execute(command('release_abort'))['state'] == 'RELEASED'
    assert not fs.readonly
    assert not fs.boot.protected


@pytest.mark.parametrize('mount', ['/data\n/other', '/data\x00', '/data\talias'])
def test_mount_control_characters_are_rejected_before_device_io(mount):
    from consistency_lease import LinuxFilesystem
    filesystem = LinuxFilesystem(lambda: {})
    with pytest.raises(FenceRejected, match='Unsafe'):
        filesystem.validate([{'mount_point': mount, 'stable_id': 'vol-synthetic', 'uuid': 'uuid', 'filesystem': 'ext4'}])


def test_missing_stable_id_is_a_controlled_refusal():
    from consistency_lease import LinuxFilesystem
    filesystem = LinuxFilesystem(lambda: {})
    with pytest.raises(FenceRejected, match='stable volume identity'):
        filesystem.validate([{'mount_point': '/data', 'uuid': 'uuid', 'filesystem': 'ext4'}])


def test_journal_counts_first_run_state_creation(tmp_path):
    from consistency_lease import Journal
    journal = Journal(tmp_path / 'journal')
    lock = journal.lock()
    try:
        assert journal.mutations == 2
    finally:
        import os
        os.close(lock)


def test_volume_order_does_not_change_replay_identity():
    store, fs, fence = setup()
    request = command()
    request['parameters']['volumes'] = [{'mount_point': '/b'}, {'mount_point': '/a'}]
    fence.execute(request)
    request['parameters']['volumes'].reverse()
    assert fence.execute(request)['state'] == 'HELD'
    assert fs.thawed == []


def test_observed_mount_alias_is_refused(monkeypatch):
    import os
    import consistency_lease
    original_stat = os.stat
    observation = {'/data': {'stable_id': 'vol-synthetic', 'uuid': 'uuid', 'filesystem': 'ext4'},
                   '/alias': {'stable_id': 'vol-synthetic', 'uuid': 'uuid', 'filesystem': 'ext4'}}
    def mounted_stat(path, *args, **kwargs):
        if str(path) in ('/data', '/alias'):
            fields = list(original_stat('/'))
            fields[2] += 1
            return os.stat_result(fields)
        return original_stat(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(consistency_lease.os, 'stat', mounted_stat)
        filesystem = consistency_lease.LinuxFilesystem(lambda: observation)
        with pytest.raises(FenceRejected, match='mount aliases'):
            filesystem.validate([{'mount_point': '/data', 'stable_id': 'vol-synthetic', 'uuid': 'uuid', 'filesystem': 'ext4'}])


def test_existing_kernel_fence_does_not_report_a_new_mutation(tmp_path, monkeypatch):
    import errno
    import os
    import consistency_lease
    filesystem = consistency_lease.LinuxFilesystem(lambda: {})
    mount = str(tmp_path)
    filesystem.devices[mount] = os.stat(mount).st_dev
    def already_frozen(*args):
        raise OSError(errno.EBUSY, 'already frozen')
    monkeypatch.setattr(consistency_lease.fcntl, 'ioctl', already_frozen)
    assert filesystem.freeze(mount) is False
    assert filesystem.mutations == 0
    monkeypatch.setattr(consistency_lease.fcntl, 'ioctl', lambda *args: 0)
    assert filesystem.freeze(mount) is True
    assert filesystem.mutations == 1


def test_failed_journal_write_does_not_prevent_compensating_owned_freezes():
    store, fs, fence = setup()
    original_write = store.write
    def full_journal(record):
        if record['frozen']:
            raise OSError('journal filesystem full')
        original_write(record)
    store.write = full_journal
    with pytest.raises(OSError, match='full'):
        fence.execute(command())
    assert fs.frozen == set()
    assert fs.thawed == ['/data']
    assert not fs.boot.protected


def test_compensation_attempts_every_owned_mount_after_one_thaw_fails():
    store, fs, fence = setup()
    fs.frozen = {'/a', '/b'}
    attempts = []
    original_thaw = fs.thaw
    def partial_thaw(mount):
        attempts.append(mount)
        if mount == '/a':
            raise OSError('device unavailable')
        original_thaw(mount)
    fs.thaw = partial_thaw
    with pytest.raises(FenceRejected, match='every owned'):
        fence._compensate_owned({'frozen': ['/a', '/b'], 'boot_entries': {}})
    assert attempts == ['/a', '/b']
    assert fs.frozen == {'/a'}
