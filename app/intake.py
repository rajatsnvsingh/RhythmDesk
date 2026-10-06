"""Lightweight change detection only: no probing, grouping or tagging."""
import hashlib
import json
import time
from common import connect
from configuration import runtime


def detect(settings):
    current = {}
    for path in settings.incoming.rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            stat = path.stat()
            current[path.relative_to(settings.incoming).as_posix()] = f'{stat.st_size}:{stat.st_mtime_ns}'
        except FileNotFoundError:
            continue
    fingerprint = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute("SELECT value FROM meta WHERE key='incoming_observation'").fetchone()
        observation = json.loads(previous['value']) if previous else {}
        if observation.get('fingerprint') != fingerprint:
            observation = dict(fingerprint=fingerprint, changed_at=time.time())
            db.execute("INSERT OR REPLACE INTO meta VALUES('incoming_observation',?)", (json.dumps(observation),))
        known = {(r['path'],r['signature']) for r in db.execute('SELECT path,signature FROM tracks')}
        pending = db.execute("SELECT value FROM meta WHERE key='scan_requested'").fetchone()
    changed = sum((path,sig) not in known for path,sig in current.items())
    wait = 0  # The user explicitly decides when the copy is ready to scan.
    return dict(changed_files=changed, settling_seconds=int(wait+0.999), ready=bool(changed and wait<=0),
                scan_pending=bool(pending and pending['value']=='true'))
