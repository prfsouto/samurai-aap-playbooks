"""Keep required data mounts read-only across source reboots until disposition."""
import os
import tempfile
from pathlib import Path


def escaped(value):
    return value.replace('\\', '\\134').replace(' ', '\\040').replace('\t', '\\011')


class BootFenceRejected(RuntimeError):
    pass


class FstabBootFence:
    def __init__(self, path='/etc/fstab', resolve_uuid=None):
        self.path = Path(path)
        self.resolve_uuid = resolve_uuid
        if self.path.is_symlink():
            raise BootFenceRejected("Symbolic fstab paths are not supported")

    def _check_aliases(self, volumes):
        for line in self.path.read_text().splitlines():
            fields = line.split()
            if line.lstrip().startswith('#') or len(fields) < 4:
                continue
            if (not fields[0].startswith('UUID=') and fields[2] not in ('ext4', 'xfs', 'auto')
                    and not {'bind', 'rbind'} & set(fields[3].split(','))):
                continue
            identity = fields[0][5:] if fields[0].startswith('UUID=') else None
            if identity is None and self.resolve_uuid is not None:
                identity = self.resolve_uuid(fields[0])
            if not identity:
                raise BootFenceRejected('Cannot exclude an alias with unresolved boot identity')
            for volume in volumes:
                if identity.casefold() == volume['uuid'].casefold() and fields[1] != escaped(volume['mount_point']):
                    raise BootFenceRejected('Another boot mount aliases a required filesystem')

    def capture(self, volumes):
        self._check_aliases(volumes)
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
        self._check_aliases(volumes)
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
        try:
            current = self.capture(volumes)
        except BootFenceRejected:
            return False
        return all(current[escaped(v['mount_point'])] is not None
                   and current[escaped(v['mount_point'])].split()[0] == 'UUID=' + v['uuid']
                   and 'ro' in current[escaped(v['mount_point'])].split()[3].split(',')
                   and not set(current[escaped(v['mount_point'])].split()[3].split(',')) & {'rw', 'noauto', 'nofail'}
                   for v in volumes)

    def activate(self, volumes):
        previous = self.capture(volumes)
        changes = {}
        for volume in volumes:
            mount = escaped(volume['mount_point'])
            line = previous[mount]
            if line is None:
                raise BootFenceRejected('Candidate boot mount is missing')
            fields = line.split()
            options = [option for option in fields[3].split(',') if option not in ('ro', 'rw')]
            fields[0] = 'UUID=' + volume['uuid']
            fields[3] = ','.join(options + ['rw'])
            changes[mount] = ' '.join(fields)
        self._replace(changes)

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
