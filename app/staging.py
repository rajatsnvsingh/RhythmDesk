"""Explicit destructive staging maintenance, never library or state cleanup."""
import os
import shutil
import time
from common import connect, event


def statistics(settings):
    root = settings.incoming.parent
    files = size = 0
    for path in root.rglob('*'):
        if path.is_symlink():
            continue
        if path.is_file():
            files += 1
            size += path.stat().st_size
    return {'files': files, 'bytes': size, 'path': str(root)}


def purge(settings, confirmation):
    if confirmation is not True:
        raise ValueError('Confirm permanent staging deletion before proceeding')
    import fcntl
    # Serialize purge requests, then signal the live worker to yield between jobs.
    with (settings.state / 'staging-purge.lock').open('a') as request_lock:
        try:
            fcntl.flock(request_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('A staging purge is already in progress')
        marker = settings.state / 'staging-maintenance'
        marker.touch()
        try:
            with (settings.state / 'staging-activity.lock').open('a') as activity:
                # Wait for the current scan/release, not for every queued album.
                fcntl.flock(activity, fcntl.LOCK_EX)
                return _purge(settings)
        finally:
            marker.unlink(missing_ok=True)


def _purge(settings):
        root = settings.incoming.parent
        resolved = root.resolve()
        if root.is_symlink() or len(resolved.parts) < 4 or resolved == settings.root:
            raise ValueError('Unsafe staging root')
        for protected in (settings.library.resolve(), settings.state.resolve()):
            if protected == resolved or protected.is_relative_to(resolved) or resolved.is_relative_to(protected):
                raise ValueError('Staging overlaps protected library/state')
        paths = list(root.rglob('*'))
        if any(p.is_symlink() or p.is_mount() for p in paths):
            raise ValueError('Staging contains a symlink or nested mount; administrator review required')
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('Processing','Editing','Publishing') LIMIT 1").fetchone():
                raise ValueError('A job is active; wait for completion before purging')
            before = statistics(settings)
            # Keep top-level operational folders and their existing permissions.
            for child in root.iterdir():
                if child in (settings.incoming, settings.curated, settings.review):
                    for entry in child.iterdir():
                        shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
                else:
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
            db.execute("UPDATE jobs SET status='Purged',reason='Staging explicitly purged by user',updated=? WHERE status!='Approved'", (time.time(),))
            db.execute('DELETE FROM tracks')
            db.execute("DELETE FROM meta WHERE key LIKE 'force_artwork:%'")
        event(settings, f"Staging permanently purged: {before['files']} files, {before['bytes']} bytes. Library and state archives retained.", level='warning')
        return before
