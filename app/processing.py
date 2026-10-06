"""Small durable processing updates shared by worker and review UI."""
import json
import time
from common import connect


def update(settings, job, phase, message='', completed=None, total=None, started=None):
    with connect(settings) as db:
        row=db.execute('SELECT value FROM meta WHERE key=?',('progress:'+job,)).fetchone()
        previous=json.loads(row['value']) if row else {}
        value=dict(phase=phase,message=message,completed=completed,total=total,
                   started=started or previous.get('started') or time.time(),updated=time.time())
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('progress:'+job,json.dumps(value)))
    return value


def read(settings, job):
    with connect(settings) as db:
        row=db.execute('SELECT value FROM meta WHERE key=?',('progress:'+job,)).fetchone()
    return json.loads(row['value']) if row else None
