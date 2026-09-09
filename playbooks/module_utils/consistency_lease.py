"""RHEL filesystem fencing with a durable, generation-scoped host journal."""
import errno
import fcntl
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class FenceRejected(RuntimeError):
    pass


class FilesystemFence:
    def __init__(self, *, store, filesystem, boot_id):
        self.store = store
        self.filesystem = filesystem
        self.boot_id = boot_id

    def execute(self, command):
        scope = command['scope']
        action = scope['action']
        mounts = sorted(command['parameters']['volumes'], key=lambda v: (v.get('stable_id', ''), v['mount_point']))
        self.filesystem.validate(mounts)
        if action == 'inspect_candidate':
            if not all(self.filesystem.is_readonly(v['mount_point']) for v in mounts):
                raise FenceRejected('Candidate data filesystem is writable before commit')
            return dict(scope=scope, boot_id=self.boot_id, state='CANDIDATE_READ_ONLY', frozen=[],
                        acquired_at=datetime.now(timezone.utc).isoformat())
        record = self.store.read()
        if record and record['scope']['plan_digest'] == scope['plan_digest'] and record.get('volumes') != mounts:
            raise FenceRejected('Source fence volume manifest changed')
        if record and record['scope']['organization_id'] != scope['organization_id']:
            raise FenceRejected('Foreign organization owns the source fence')
        if record and record['scope']['generation'] > scope['generation']:
            raise FenceRejected('Stale fence generation')
        if record and record['scope']['plan_digest'] != scope['plan_digest']:
            if record['state'] not in ('RELEASED', 'CANDIDATE_WRITABLE'):
                raise FenceRejected('Another plan owns an unreleased source fence')
        if action == 'activate_candidate':
            if record and record['state'] not in ('CANDIDATE_WRITABLE', 'ACTIVATING'):
                raise FenceRejected('Candidate activation cannot release a source fence')
            record = dict(scope=scope, boot_id=self.boot_id, state='ACTIVATING', frozen=[],
                          volumes=mounts, acquired_at=datetime.now(timezone.utc).isoformat())
            self.store.write(record)
            for volume in mounts:
                self.filesystem.make_writable(volume['mount_point'])
                self.filesystem.confirm_writable(volume['mount_point'])
            self.filesystem.boot.activate(mounts)
            record['state'] = 'CANDIDATE_WRITABLE'
            self.store.write(record)
            return self.evidence(record)
        if action == 'acquire':
            return self.acquire(command, record, mounts)
        if not record or record['scope']['generation'] != scope['generation']:
            raise FenceRejected('No matching fence generation')
        if record['scope']['plan_digest'] != scope['plan_digest']:
            raise FenceRejected('Source fence plan mismatch')
        boot_readonly = False
        if record['boot_id'] != self.boot_id:
            boot_readonly = (self.filesystem.boot.verify(mounts)
                             and all(self.filesystem.is_readonly(v['mount_point']) for v in mounts))
            if not boot_readonly:
                raise FenceRejected('Host reboot invalidated the observed fence')
            if action != 'release_abort':
                record['boot_id'] = self.boot_id
                record['protection'] = 'boot_read_only'
                self.store.write(record)
        if action == 'release_abort':
            if record['state'] == 'TRANSFERRED':
                raise FenceRejected('Committed source must not resume writing')
            return self.release(record, mounts, boot_readonly=boot_readonly)
        if action not in ('inspect', 'transfer') or record['state'] not in ('HELD', 'TRANSFERRED'):
            raise FenceRejected('Source fence is not held')
        if not self.filesystem.boot.verify(mounts):
            raise FenceRejected('Persistent source boot fence was lost')
        if record.get('protection') == 'boot_read_only':
            if not self.filesystem.boot.verify(mounts) or not all(self.filesystem.is_readonly(v['mount_point']) for v in mounts):
                raise FenceRejected('Persistent source boot fence was lost')
        for volume in (() if boot_readonly or record.get('protection') == 'boot_read_only' else mounts):
            if self.filesystem.freeze(volume['mount_point']):
                record['state'] = 'UNCERTAIN'
                self.store.write(record)
                raise FenceRejected('Source fence was lost; refusing continuity proof')
        if action == 'transfer':
            record['state'] = 'TRANSFERRED'
            self.store.write(record)
        return self.evidence(record)

    def acquire(self, command, record, mounts):
        if record and record['state'] in ('HELD', 'TRANSFERRED'):
            inspection = dict(command, scope=dict(command['scope'], action='inspect'))
            return self.execute(inspection)
        if record and record['state'] not in ('RELEASED', 'CANDIDATE_WRITABLE'):
            raise FenceRejected('Uncertain acquisition requires explicit recovery')
        record = dict(scope=command['scope'], boot_id=self.boot_id, state='ACQUIRING', frozen=[], volumes=mounts,
                      acquired_at=datetime.now(timezone.utc).isoformat(), protection='filesystem_freeze',
                      boot_entries=self.filesystem.boot.capture(mounts))
        self.store.write(record)
        try:
            self.filesystem.boot.protect(mounts, record['boot_entries'])
            if not self.filesystem.boot.verify(mounts):
                raise FenceRejected('Source read-only boot fence was not persisted')
            for volume in mounts:
                mount = volume['mount_point']
                if not self.filesystem.freeze(mount):
                    raise FenceRejected('Filesystem already frozen without confirmed ownership')
                record['frozen'].append(mount)
                self.store.write(record)
        except Exception:
            record['state'] = 'UNCERTAIN'
            try:
                self._compensate_owned(record)
            finally:
                self.store.write(record)
            raise
        record['state'] = 'HELD'
        self.store.write(record)
        return self.evidence(record)

    def _compensate_owned(self, record):
        errors = []
        for mount in record['frozen']:
            try:
                self.filesystem.thaw(mount)
                self.filesystem.confirm_writable(mount)
            except Exception as exc:
                errors.append(exc)
        try:
            self.filesystem.boot.restore(record['boot_entries'])
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise FenceRejected('Source recovery did not confirm every owned filesystem') from errors[0]

    def release(self, record, mounts, boot_readonly=False):
        if record['state'] == 'RELEASED':
            return self.evidence(record)
        if record['state'] not in ('HELD', 'RELEASING'):
            raise FenceRejected('Uncertain acquisition cannot be thawed automatically')
        record['state'] = 'RELEASING'
        self.store.write(record)
        for mount in record['frozen']:
            if boot_readonly or record.get('protection') == 'boot_read_only':
                self.filesystem.make_writable(mount)
            else:
                self.filesystem.thaw(mount)
        for volume in mounts:
            self.filesystem.confirm_writable(volume['mount_point'])
        self.filesystem.boot.restore(record['boot_entries'])
        record['state'] = 'RELEASED'
        self.store.write(record)
        return self.evidence(record)

    @staticmethod
    def evidence(record):
        result = {key: record[key] for key in ('scope', 'boot_id', 'state', 'frozen', 'acquired_at')}
        result['protection'] = record.get('protection')
        return result


class Journal:
    def __init__(self, directory='/.samurai-consistency'):
        self.mutations = 0
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or info.st_dev != os.stat('/').st_dev):
            raise FenceRejected('Unsafe fence journal directory')
        self.path = self.directory / 'source.json'

    def lock(self):
        fd = os.open(self.directory / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(fd) as stream:
            return json.load(stream)

    def write(self, record):
        fd, path = tempfile.mkstemp(dir=self.directory)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(record, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, self.path)
            self.mutations += 1
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class LinuxFilesystem:
    def __init__(self, observe, make_writable=None, boot=None):
        self.observe = observe
        self.make_writable = make_writable
        self.mutations = 0
        self.devices = {}
        self.boot = boot

    def validate(self, volumes):
        if not volumes:
            raise FenceRejected('No required filesystem to fence')
        observed = self.observe()
        seen = set()
        root_device = os.stat('/').st_dev
        for volume in volumes:
            mount = volume['mount_point']
            if (not isinstance(mount, str) or not mount.startswith('/') or mount == '/'
                    or any(ord(character) < 32 or ord(character) == 127 for character in mount)
                    or mount in seen or '..' in mount.split('/')):
                raise FenceRejected('Unsafe or repeated source mount')
            seen.add(mount)
            if not isinstance(volume.get('uuid'), str) or not volume['uuid']:
                raise FenceRejected('Required filesystem UUID is missing')
            if os.stat(mount).st_dev == root_device:
                raise FenceRejected('Source volume shares the root filesystem holding the fence journal')
            self.devices[mount] = os.stat(mount).st_dev
            actual = observed.get(mount)
            if sum(1 for entry in observed.values() if entry.get('stable_id') == volume['stable_id'] and entry.get('uuid') == volume['uuid']) != 1:
                raise FenceRejected('Required filesystem has multiple observed mount aliases')
            if (not actual or actual['stable_id'] != volume['stable_id']
                    or actual['filesystem'] not in ('ext4', 'xfs')
                    or actual['filesystem'] != volume['filesystem']
                    or actual['uuid'] != volume['uuid']):
                raise FenceRejected('Observed source filesystem identity does not match')

    def is_readonly(self, mount):
        return self.observe().get(mount, {}).get('readonly') is True

    def _ioctl(self, mount, request):
        fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.fstat(fd).st_dev != self.devices.get(mount):
                raise FenceRejected("Source device changed after observation")
            fcntl.ioctl(fd, request, 0)
            self.mutations += 1
        finally:
            os.close(fd)

    def freeze(self, mount):
        try:
            self._ioctl(mount, 0xC0045877)
            return True
        except OSError as exc:
            if exc.errno == errno.EBUSY:
                return False
            raise

    def thaw(self, mount):
        try:
            self._ioctl(mount, 0xC0045878)
        except OSError as exc:
            if exc.errno != errno.EINVAL:
                raise

    @staticmethod
    def confirm_writable(mount):
        fd = os.open(mount, os.O_WRONLY | os.O_TMPFILE, 0o600)
        try:
            os.write(fd, b'write-authority-check\n')
            os.fsync(fd)
        finally:
            os.close(fd)
