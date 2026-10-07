"""Explicit destructive staging maintenance, never library or state cleanup."""
import os
import shutil
import time
import contextlib
import hashlib
import json
import re
from common import connect, event, safe_child, sha256


def statistics(settings):
    root = settings.incoming.parent
    files = size = 0
    breakdown = {name: dict(files=0, bytes=0) for name in ('Incoming originals', 'Curated copies', 'Needs review / work', 'Temporary / other')}
    for path in root.rglob('*'):
        if path.is_symlink():
            continue
        if path.is_file():
            try:
                amount = path.stat().st_size
            except FileNotFoundError:
                continue
            files += 1
            size += amount
            top = path.relative_to(root).parts[0]
            kind = {settings.incoming.name: 'Incoming originals', settings.curated.name: 'Curated copies',
                    settings.review.name: 'Needs review / work'}.get(top, 'Temporary / other')
            breakdown[kind]['files'] += 1
            breakdown[kind]['bytes'] += amount
    return {'files': files, 'bytes': size, 'path': str(root), 'breakdown': breakdown}


def purge(settings, confirmation):
    if confirmation is not True:
        raise ValueError('Confirm permanent staging deletion before proceeding')
    with maintenance(settings):
        return _purge(settings)


@contextlib.contextmanager
def maintenance(settings):
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
                yield
        finally:
            marker.unlink(missing_ok=True)


def deletion_plan(settings, job_id, db):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',job_id) or job_id in ('.','..'):
        raise ValueError('Invalid release ID')
    job=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not job or job['status'] not in ('Needs review','Curated','Queued'):
        raise ValueError('Only unpublished, idle releases can be deleted from staging')
    root=settings.incoming.parent.resolve()
    if len(root.parts)<4 or root==settings.root.resolve():
        raise ValueError('Unsafe staging root')
    for protected in (settings.library.resolve(),settings.state.resolve()):
        if root==protected or root.is_relative_to(protected) or protected.is_relative_to(root):
            raise ValueError('Staging overlaps protected library/state')
    directories=[]
    for parent in (settings.incoming,settings.curated,settings.review):
        if parent.is_symlink() or not parent.resolve().is_relative_to(root):
            raise ValueError('Unsafe staging directory')
        if parent==settings.incoming:continue
        paths=[safe_child(parent,job_id)]
        if parent==settings.review:
            paths.extend(p for p in parent.iterdir() if re.fullmatch(re.escape(job_id)+r'\.previous-[0-9a-f]{8}',p.name))
        directories.extend(p for p in paths if p.exists())
    rows=db.execute('SELECT * FROM tracks WHERE job_id=?',(job_id,)).fetchall()
    sources=[]
    for row in rows:
        shared=db.execute("SELECT 1 FROM tracks t JOIN jobs j ON j.id=t.job_id WHERE t.path=? AND t.job_id!=? AND j.status NOT IN ('Approved','Purged','Regrouped') LIMIT 1",(row['path'],job_id)).fetchone()
        if shared:
            raise ValueError('An Incoming file is shared with another release; regroup before deleting')
        source=safe_child(settings.incoming,row['path'])
        if source.exists():
            if not source.is_file() or source.is_mount():raise ValueError('Incoming track is not a regular file')
            prior=next((checksum for path,checksum in sources if path==source),None)
            if prior is not None:
                if prior!=row['sha256']:raise ValueError('Conflicting Incoming versions; resolve before deleting')
                continue
            sources.append((source,row['sha256']))
    files=set(p for p,_ in sources)
    for directory in directories:
        if not directory.is_dir():raise ValueError('Release work path is not a directory')
        for path in [directory,*directory.rglob('*')]:
            if path.is_symlink() or path.is_mount():
                raise ValueError('Release contains a symlink or nested mount; administrator review required')
            if path.is_file():files.add(path)
    signature=[(str(p.relative_to(root)),p.stat().st_size,p.stat().st_mtime_ns) for p in sorted(files)]
    payload={'status':job['status'],'revision':job['revision'],'files':signature,
             'directories':[str(p.relative_to(root)) for p in directories],
             'sources':[(row['id'],row['path'],row['sha256']) for row in rows]}
    token=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    return dict(job_id=job_id,album=job['album'],files=len(files),bytes=sum(p.stat().st_size for p in files),
                originals=len(sources),token=token),directories,sources


def preview_release(settings,job_id):
    with connect(settings) as db:
        return deletion_plan(settings,job_id,db)[0]


def delete_release(settings,job_id,confirmation,token):
    if confirmation is not True:
        raise ValueError('Confirm permanent release deletion before proceeding')
    with maintenance(settings),connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        plan,directories,sources=deletion_plan(settings,job_id,db)
        if token!=plan['token']:
            raise ValueError('Release changed since confirmation; preview deletion again')
        for path,expected in sources:
            if sha256(path)!=expected:
                raise ValueError('An Incoming original changed since scan; no files deleted')
        for directory in directories:shutil.rmtree(directory)
        for path,_ in sources:path.unlink()
        db.execute("UPDATE jobs SET status='Purged',reason='Release explicitly deleted from staging',updated=? WHERE id=?",(time.time(),job_id))
        db.execute('DELETE FROM tracks WHERE job_id=?',(job_id,))
        for prefix in ('candidate:','force_artwork:','manual:','progress:'):
            db.execute('DELETE FROM meta WHERE key=?',(prefix+job_id,))
    event(settings,f"Release permanently deleted from staging: {plan['files']} files, {plan['bytes']} bytes. Published library untouched.",job_id,'warning')
    return plan


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
