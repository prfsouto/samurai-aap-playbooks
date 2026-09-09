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
