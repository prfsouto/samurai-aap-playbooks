"""Listagem de um volume de origem: caminho, tamanho e sha256 de cada arquivo regular.

Utilidade de host, só stdlib, compatível com Python 3.6+. Só lê: não cria, não escreve,
não segue symlink e não atravessa para outro dispositivo, então um volume montado sob a
origem não entra na referência. Qualquer nome que não seja UTF-8 é erro, nunca é
normalizado em silêncio.
"""
import errno
import hashlib
import json
import os
import stat

LISTER_VERSION = 'samurai.file_listing/v1'
MAX_ENTRIES = 2000
MAX_SERIALIZED_BYTES = 256 * 1024
MAX_READ_BYTES = 4 * 1024 ** 3
_CHUNK_BYTES = 1024 * 1024


class FileListingError(ValueError):
    """A listagem não pode ser feita com integridade. O motivo é nomeado."""


class _Incomplete(Exception):
    def __init__(self, reason):
        super(_Incomplete, self).__init__(reason)
        self.reason = reason


def _open_read_only(path):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    noatime = getattr(os, 'O_NOATIME', 0)
    if noatime:
        try:
            return os.open(path, flags | noatime)
        except OSError as exc:
            # Sem dono do arquivo, O_NOATIME é recusado; a leitura segue sem ele.
            if exc.errno != errno.EPERM:
                raise
    return os.open(path, flags)


def _relative_utf8(root, full):
    relative = os.path.relpath(full, root).replace(os.sep, '/')
    try:
        relative.encode('utf-8')
    except UnicodeEncodeError:
        raise FileListingError('path_not_utf8')
    return relative


def _digest(full, root_device, total_bytes, max_read_bytes):
    fd = _open_read_only(full)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_dev != root_device:
            raise FileListingError('file_changed_during_listing')
        hasher = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(fd, _CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if total_bytes + size > max_read_bytes:
                raise _Incomplete('read_limit')
            hasher.update(chunk)
    finally:
        os.close(fd)
    return size, hasher.hexdigest()


def _walk_failed(error):
    raise FileListingError('walk_failed')


def list_volume(root, max_entries=MAX_ENTRIES, max_serialized_bytes=MAX_SERIALIZED_BYTES,
                max_read_bytes=MAX_READ_BYTES, lstat=os.lstat):
    """Lista o volume em ``root``. Limite estourado devolve ``complete=False`` com motivo."""
    if not os.path.isabs(root) or not stat.S_ISDIR(lstat(root).st_mode):
        raise FileListingError('mount_not_directory')
    root_device = lstat(root).st_dev
    files = []
    total_bytes = 0
    incomplete = None
    try:
        for directory, dirnames, filenames in os.walk(root, onerror=_walk_failed, followlinks=False):
            kept = []
            for name in dirnames:
                info = lstat(os.path.join(directory, name))
                if stat.S_ISDIR(info.st_mode) and info.st_dev == root_device:
                    kept.append(name)
            dirnames[:] = kept
            for name in filenames:
                full = os.path.join(directory, name)
                info = lstat(full)
                if not stat.S_ISREG(info.st_mode) or info.st_dev != root_device:
                    continue
                if len(files) >= max_entries:
                    raise _Incomplete('entry_limit')
                relative = _relative_utf8(root, full)
                size, digest = _digest(full, root_device, total_bytes, max_read_bytes)
                total_bytes += size
                files.append({'path': relative, 'size': size, 'sha256': digest})
    except _Incomplete as stop:
        incomplete = stop.reason
    result = {
        'lister_version': LISTER_VERSION,
        'scope': ['.'],
        'complete': incomplete is None,
        'incomplete_reason': incomplete,
        'file_count': len(files),
        'total_bytes': total_bytes,
        'files': sorted(files, key=lambda entry: entry['path']),
    }
    if incomplete is None and len(json.dumps(result, sort_keys=True).encode('utf-8')) > max_serialized_bytes:
        incomplete = 'serialized_limit'
    if incomplete is not None:
        result.update(complete=False, incomplete_reason=incomplete, files=[])
    return result
