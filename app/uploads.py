"""Stream browser batches into staging, then atomically expose them to intake."""
from contextlib import contextmanager
import os
from pathlib import PurePosixPath
import re
import threading
from common import event, safe_child

LOCK = threading.RLock()

@contextmanager
def activity(settings):
    with LOCK, (settings.state / 'staging-activity.lock').open('a') as handle:
        if os.name != 'nt':
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        if (settings.state / 'staging-maintenance').exists():
            raise ValueError('Staging maintenance is active; retry later')
        yield

def batch_path(settings, batch):
    if not re.fullmatch(r'[a-f0-9]{32}', batch):
        raise ValueError('Invalid upload batch')
    return safe_child(settings.incoming.parent, '.uploads/' + batch)

def receive(settings, batch, name, stream, size):
    parts = PurePosixPath(name).parts
    if not parts or name.startswith('/') or '\\' in name or any(p in ('', '.', '..') or ':' in p or any(ord(c)<32 for c in p) for p in name.split('/')):
        raise ValueError('Unsafe upload path')
    limit = int(os.environ.get('CURATOR_UPLOAD_FILE_BYTES', str(2 * 1024**3)))
    if not 0 < size <= limit:
        raise ValueError('Empty file or upload exceeds per-file limit')
    with activity(settings):
        root = batch_path(settings, batch)
        if (settings.incoming / ('Upload-' + batch)).exists():
            raise ValueError('Batch was already completed')
        root.mkdir(parents=True, exist_ok=True)
        target = safe_child(root, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError('A file with that path already exists in this batch')
        files = list(root.rglob('*'))
        if sum(p.is_file() for p in files) >= 10000 or sum(p.stat().st_size for p in files if p.is_file()) + size > 50 * 1024**3:
            raise ValueError('Batch exceeds 10,000 files or 50 GiB')
        temporary = target.with_name(target.name + '.uploading')
        try:
            with temporary.open('xb') as output:
                remaining = size
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError('Upload interrupted; file was not added')
                    output.write(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            if os.name == 'nt':
                temporary.rename(target)  # Windows rename rejects an existing destination.
            else:
                os.link(temporary, target)  # Atomic no-overwrite publication, same filesystem.
        finally:
            temporary.unlink(missing_ok=True)
    return {'path': name, 'bytes': size}

def complete(settings, batch, count):
    with activity(settings):
        root = batch_path(settings, batch)
        files = [p for p in root.rglob('*') if p.is_file()]
        if not root.is_dir() or not files or len(files) != int(count) or any(p.name.endswith('.uploading') for p in files):
            raise ValueError('Upload batch is incomplete')
        destination = settings.incoming / ('Upload-' + batch)
        if destination.exists():
            raise ValueError('Batch destination already exists')
        root.rename(destination)
        event(settings, f'Browser upload completed: {len(files)} files in {destination.name}; awaiting manual scan')
        return {'files': len(files), 'folder': destination.name}
