"""Browse a fixed read-only mount and copy explicit selections into Incoming."""
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid
from common import connect, event, safe_child
from uploads import activity

def root(settings):
    mount = Path(os.environ.get('CURATOR_SOURCE_ROOT', '/mnt/music-source'))
    with connect(settings) as db:
        row = db.execute("SELECT value FROM meta WHERE key='source_subdirectory'").fetchone()
    return safe_child(mount, json.loads(row['value']) if row else '')

def excluded(settings, path):
    mount = Path(os.environ.get('CURATOR_SOURCE_ROOT', '/mnt/music-source')).resolve()
    host = Path(os.environ.get('CURATOR_HOST_SOURCE', '/srv/media/music'))
    for key in ('CURATOR_HOST_STAGING', 'CURATOR_HOST_STATE', 'CURATOR_HOST_LIBRARY'):
        value = os.environ.get(key)
        if value:
            try:
                blocked = mount / Path(value).relative_to(host)
            except ValueError:
                continue
            if path == blocked or blocked in path.parents:
                return True
    # Also protect conventional operational directories when host mappings are unavailable.
    return any(p in ('staging', 'curator-state', 'rhythm-attic', '.uploads', '.imports') for p in path.relative_to(mount).parts)

def resolve(settings, relative):
    if not isinstance(relative,str) or '\\' in relative:
        raise ValueError('Invalid source path')
    path = safe_child(root(settings), relative)
    if excluded(settings, path):
        raise ValueError('Curator operational directories are excluded from imports')
    return path

def describe(settings):
    source = root(settings)
    return dict(host=os.environ.get('CURATOR_HOST_SOURCE', 'Not configured'),
                mount=os.environ.get('CURATOR_SOURCE_ROOT', '/mnt/music-source'),
                available=source.is_dir() and os.access(source, os.R_OK | os.X_OK),
                subdirectory=str(source.relative_to(Path(os.environ.get('CURATOR_SOURCE_ROOT', '/mnt/music-source')).resolve())),
                access='Read only; selections are copied into Incoming')

def save(settings, subdirectory):
    mount = Path(os.environ.get('CURATOR_SOURCE_ROOT', '/mnt/music-source'))
    if not isinstance(subdirectory,str) or '\\' in subdirectory:
        raise ValueError('Use a relative folder inside the read-only mount')
    target = safe_child(mount, subdirectory)
    if not target.is_dir() or excluded(settings,target):
        raise ValueError('Source folder is unavailable or protected')
    with connect(settings) as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES('source_subdirectory',?)", (json.dumps(subdirectory),))
    return describe(settings)

def browse(settings, relative='', offset=0, search=''):
    path = resolve(settings,relative)
    if not path.is_dir():
        raise ValueError('Source directory is not mounted or readable')
    rows=[]
    for child in path.iterdir():
        if child.is_symlink() or excluded(settings,child) or search.casefold() not in child.name.casefold():
            continue
        try:
            if not child.is_file() and not child.is_dir():continue
            rows.append(dict(name=child.name,path=child.relative_to(root(settings)).as_posix(),
                             directory=child.is_dir(),bytes=child.stat().st_size if child.is_file() else None))
        except PermissionError:
            continue
    rows.sort(key=lambda r:(not r['directory'],r['name'].casefold()))
    offset=max(0,int(offset))
    return dict(path=relative,parent=str(Path(relative).parent).replace('\\','/') if relative else '',
                items=rows[offset:offset+200],total=len(rows),offset=offset,source=describe(settings))

def state(settings, job):
    if not isinstance(job,str) or len(job)!=32 or any(c not in '0123456789abcdef' for c in job):
        raise ValueError('Invalid import ID')
    with connect(settings) as db:
        row=db.execute('SELECT value FROM meta WHERE key=?',('source_import:'+job,)).fetchone()
    if not row:raise ValueError('Import not found')
    return json.loads(row['value'])

def write_state(settings, job, value):
    with connect(settings) as db:
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('source_import:'+job,json.dumps(value)))

def start(settings, selections):
    if not isinstance(selections,list) or not 1<=len(selections)<=1000 or any(not isinstance(x,str) or not x for x in selections):
        raise ValueError('Select between 1 and 1,000 files or folders')
    paths=[resolve(settings,x) for x in dict.fromkeys(selections)]
    # Parent selections cover their children; avoid copying the same source twice.
    paths=[p for p in paths if not any(other!=p and other in p.parents for other in paths)]
    if any(not p.exists() for p in paths):raise ValueError('A selected source no longer exists')
    job=uuid.uuid4().hex
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        active=db.execute("SELECT value FROM meta WHERE key LIKE 'source_import:%'").fetchall()
        if any(json.loads(row['value']).get('status')=='Copying' for row in active):
            raise ValueError('An import is already running; wait for it to finish')
        db.execute('INSERT INTO meta VALUES(?,?)',('source_import:'+job,json.dumps(dict(status='Copying',files=0,bytes=0,current='',reason=''))))
    threading.Thread(target=copy_batch,args=(settings,job,paths),daemon=True).start()
    return {'id':job}

def copy_batch(settings, job, selections):
    result=dict(status='Copying',files=0,bytes=0,current='',reason='')
    try:
        with activity(settings):
            temp=safe_child(settings.incoming.parent,'.imports/'+job)
            temp.mkdir(parents=True)
            # Preserve paths relative to the drawer, not just basenames (avoids collisions).
            source=root(settings)
            for selected in selections:
                candidates=selected.rglob('*') if selected.is_dir() else [selected]
                for path in candidates:
                    if path.is_symlink() or excluded(settings,path):continue
                    path=safe_child(source,path.relative_to(source).as_posix())
                    if not path.is_file():continue
                    before=path.stat()
                    if result['files']>=10000 or result['bytes']+before.st_size>50*1024**3:
                        raise ValueError('Import exceeds 10,000 files or 50 GiB; use smaller batches')
                    destination=safe_child(temp,path.relative_to(source).as_posix())
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    with path.open('rb') as incoming,destination.open('xb') as output:
                        shutil.copyfileobj(incoming,output,1024*1024)
                    after=path.stat()
                    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or destination.stat().st_size!=before.st_size:
                        raise ValueError('Source changed during copy; import not exposed to Incoming')
                    result.update(files=result['files']+1,bytes=result['bytes']+before.st_size,current=path.relative_to(source).as_posix())
                    write_state(settings,job,result)
            if not result['files']:raise ValueError('No readable regular files selected')
            temp.rename(settings.incoming/('Import-'+job))
            result.update(status='Complete',folder='Import-'+job,current='')
            event(settings,f"Read-only drawer import copied {result['files']} files; waiting for manual Scan")
    except Exception as error:
        result.update(status='Failed',reason=str(error))
        event(settings,'Source import failed: '+str(error),level='warning')
    write_state(settings,job,result)

def recover(settings):
    with connect(settings) as db:
        for row in db.execute("SELECT key,value FROM meta WHERE key LIKE 'source_import:%'").fetchall():
            value=json.loads(row['value'])
            if value.get('status')=='Copying':
                value.update(status='Interrupted',reason='Web service restarted. Originals are untouched; re-select to retry. Temporary copies remain in staging.')
                db.execute('UPDATE meta SET value=? WHERE key=?',(json.dumps(value),row['key']))
