"""Keep required data mounts read-only across source reboots until disposition."""
import os
import tempfile
from pathlib import Path


def escaped(value):
    return value.replace('\\', '\\134').replace(' ', '\\040').replace('\t', '\\011')


class BootFenceRejected(RuntimeError):
    pass


class FstabBootFence:
    def __init__(self, path='/etc/fstab'):
        self.path = Path(path)
        if self.path.is_symlink():
            raise BootFenceRejected("Symbolic fstab paths are not supported")

    def capture(self, volumes):
        lines = self.path.read_text().splitlines()
        saved = {}
        for volume in volumes:
            mount = escaped(volume['mount_point'])
            matches = [line for line in lines if not line.lstrip().startswith('#')
                       and len(line.split()) >= 4 and line.split()[1] == mount]
            if len(matches) > 1 or any(line.split()[2] not in ('ext4', 'xfs') for line in matches):
                raise BootFenceRejected('Ambiguous required filesystem boot configuration')
            saved[mount] = matches[0] if matches else None
        return saved

    def protect(self, volumes, original):
        changes = {}
        for volume in volumes:
            mount = escaped(volume['mount_point'])
            previous = original[mount]
            options = previous.split()[3].split(',') if previous else ['defaults']
            options = [opt for opt in options if opt not in ('rw', 'ro', 'noauto', 'nofail')]
            if any(opt.startswith('x-systemd.') for opt in options):
                raise BootFenceRejected('Custom mount boot ordering is not supported by this fence')
            changes[mount] = '{} {} {} {} 0 2'.format(
                'UUID=' + volume['uuid'], mount, volume['filesystem'], ','.join(options + ['ro']))
        self._replace(changes)

    def verify(self, volumes):
        current = self.capture(volumes)
        return all(current[escaped(v['mount_point'])] is not None
                   and current[escaped(v['mount_point'])].split()[0] == 'UUID=' + v['uuid']
                   and 'ro' in current[escaped(v['mount_point'])].split()[3].split(',')
                   and not set(current[escaped(v['mount_point'])].split()[3].split(',')) & {'rw', 'noauto', 'nofail'}
                   for v in volumes)

    def restore(self, original):
        self._replace(original)

    def _replace(self, changes):
        before = self.path.stat()
        lines = self.path.read_text().splitlines()
        result = []
        consumed = set()
        for line in lines:
            fields = line.split()
            mount = fields[1] if len(fields) >= 4 and not line.lstrip().startswith('#') else None
            if mount not in changes:
                result.append(line)
            elif mount not in consumed:
                consumed.add(mount)
                if changes[mount] is not None:
                    result.append(changes[mount])
        result.extend(value for mount, value in changes.items() if mount not in consumed and value is not None)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent)
        try:
            os.fchmod(fd, before.st_mode & 0o777)
            for attribute in os.listxattr(self.path):
                os.setxattr(temporary, attribute, os.getxattr(self.path, attribute))
            os.fchown(fd, before.st_uid, before.st_gid)
            with os.fdopen(fd, 'w') as stream:
                stream.write('\n'.join(result) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
