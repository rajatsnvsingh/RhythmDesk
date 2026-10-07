"""Flexible intake and Beets curation. Never opens the final library."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from common import (AUDIO, Settings, atomic_json, clean_name, connect, event,
                    inventory, review_record, safe_child, sha256)


def inspect_audio(path):
    from mediafile import MediaFile
    result = subprocess.run([os.environ.get('FFPROBE', 'ffprobe'), '-v', 'error',
                             '-show_streams', '-show_format', '-of', 'json', str(path)],
                            capture_output=True, text=True, timeout=90, check=True)
    probe = json.loads(result.stdout)
    streams = [s for s in probe.get('streams', []) if s.get('codec_type') == 'audio']
    if not streams:
        raise ValueError('No readable audio stream')
    stream = streams[0]
    seconds = float(probe.get('format', {}).get('duration') or stream.get('duration') or 0)
    if seconds <= 0:
        raise ValueError('Audio duration is unavailable')
    media = MediaFile(path)
    return {'artist': media.artist or '', 'albumartist': media.albumartist or ('Various Artists' if media.comp else media.artist) or '',
            'album': media.album or '', 'title': media.title or path.stem,
            'year': media.year or 0, 'track': media.track or 0, 'disc': media.disc or 1,
            'release_id': media.mb_albumid or '', 'seconds': seconds,
            'bitrate': int(stream.get('bit_rate') or probe.get('format', {}).get('bit_rate') or 0),
            'sample_rate': int(stream.get('sample_rate') or 0),
            'bitdepth': int(stream.get('bits_per_raw_sample') or stream.get('bits_per_sample') or 0),
            'codec': stream.get('codec_name', ''), 'bytes': path.stat().st_size}


def scan(settings):
    count = 0
    # Directory names never define albums. Every stable file is inspected.
    for path in sorted(settings.incoming.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            safe_child(settings.incoming, path.relative_to(settings.incoming))
            stat = path.stat()
            relative = path.relative_to(settings.incoming).as_posix()
            signature = f'{stat.st_size}:{stat.st_mtime_ns}'
            with connect(settings) as db:
                if db.execute('SELECT 1 FROM tracks WHERE path=? AND signature=?', (relative, signature)).fetchone():
                    continue
            metadata = {}
            checksum = ''
            status, reason = 'Ignored', 'Non-audio file (original retained)'
            if path.suffix.lower() in AUDIO:
                try:
                    metadata = inspect_audio(path)
                    checksum = sha256(path)
                    status, reason = ('Ignored', 'Shorter than 10 seconds') if metadata['seconds'] < 10 else ('Incoming', '')
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    status, reason = 'Unresolved', f'Unreadable audio: {str(error)[:500]}'
            after = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                continue
            with connect(settings) as db:
                same_content = db.execute("SELECT id FROM tracks WHERE path=? AND sha256=? AND status!='Superseded' ORDER BY updated DESC LIMIT 1", (relative, checksum)).fetchone() if checksum else None
                if same_content:
                    # A timestamp-only change must not turn the sole original
                    # into its own duplicate or create a second curation job.
                    db.execute('UPDATE tracks SET signature=? WHERE id=?', (signature, same_content['id']))
                    continue
                duplicate = db.execute("SELECT id FROM tracks WHERE sha256=? AND status NOT IN ('Ignored','Superseded') LIMIT 1", (checksum,)).fetchone() if checksum and status == 'Incoming' else None
                if duplicate:
                    status, reason = 'Duplicate', 'Exact input duplicate of ' + duplicate['id']
                elif status == 'Incoming' and not (metadata.get('album') and metadata.get('albumartist')):
                    status, reason = 'Unresolved', 'No reliable album tags. Select tracks and assign a release for matching.'
                db.execute("UPDATE tracks SET status='Superseded' WHERE path=? AND status IN ('Incoming','Unresolved','Ignored','Duplicate')", (relative,))
                values = {'id': uuid.uuid4().hex, 'path': relative, 'signature': signature,
                          'sha256': checksum, 'status': status, 'reason': reason,
                          'updated': time.time(), **metadata}
                columns = ','.join(values)
                db.execute(f'INSERT OR IGNORE INTO tracks({columns}) VALUES({",".join("?" for _ in values)})', tuple(values.values()))
                count += 1
        except (ValueError, OSError) as error:
            event(settings, f'Intake: {path.name}: {error}', level='warning')
    if count:
        event(settings, f'Intake indexed {count} files; originals retained in incoming')
    with connect(settings) as db:
        db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', ('last_scan', str(time.time())))
    return count


def create_job(settings, track_ids, artist='', album='', year=0, release_id='', mode='auto'):
    if not track_ids or len(track_ids) > 1000:
        raise ValueError('Select between 1 and 1,000 audio tracks')
    if release_id:
        release_id = str(uuid.UUID(release_id))
    job_id = 'work.' + uuid.uuid4().hex[:16]
    mode = ('single' if len(track_ids)==1 else 'album') if mode=='auto' else mode
    if mode not in ('album','single','partial','manual') or (mode=='single' and len(track_ids)!=1):
        raise ValueError('Choose complete album, single track (one file), or partial album')
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute(f'SELECT * FROM tracks WHERE id IN ({",".join("?" for _ in track_ids)})', track_ids).fetchall()
        if len(rows) != len(set(track_ids)) or any(r['status'] not in ('Incoming', 'Unresolved') or not r['sha256'] or (r['seconds'] or 0) < 10 for r in rows):
            raise ValueError('Only readable, unassigned tracks of at least 10 seconds can be grouped')
        artist = artist.strip() or rows[0]['albumartist']
        album = album.strip() or rows[0]['album']
        # A manual grouping only assigns originals to an editor. It does not
        # write metadata; the manual submission still requires real values.
        if mode != 'manual':
            clean_name(artist)
            clean_name(album)
        if not 0 <= int(year or 0) <= 9999:
            raise ValueError('Invalid year')
        now = time.time()
        db.execute('INSERT INTO jobs(id,status,artist,album,year,release_id,reason,created,updated,track_count) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (job_id, 'Queued', artist, album, year or rows[0]['year'], release_id, '', now, now, len(rows)))
        for row in rows:
            db.execute("UPDATE tracks SET status='Assigned',job_id=?,updated=? WHERE id=?", (job_id, now, row['id']))
        db.execute('UPDATE jobs SET mode=? WHERE id=?',(mode,job_id))
        if mode=='manual':
            db.execute("UPDATE jobs SET status='Needs review',reason='Enter manual metadata to prepare this release' WHERE id=?",(job_id,))
    event(settings, f'Grouped {artist or "Unidentified artist"} / {album or "Untitled release"}: {len(rows)} tracks', job_id)
    return job_id


def group_tagged(settings):
    with connect(settings) as db:
        rows = db.execute("SELECT * FROM tracks WHERE status='Incoming' ORDER BY path").fetchall()
    groups = {}
    for row in rows:
        key = (row['albumartist'].casefold(), row['album'].casefold(), row['release_id'] or '', row['year'] or 0)
        groups.setdefault(key, []).append(row)
    for rows in groups.values():
        create_job(settings, [r['id'] for r in rows], release_id=rows[0]['release_id'] or '')


def normalize_output(audio):
    """Number globally across discs; avoid collisions and keep the reviewed tags."""
    from mediafile import MediaFile
    files = inventory(audio)
    pairs = [(audio / name, MediaFile(audio / name)) for name in files]
    pairs.sort(key=lambda pair: (pair[1].disc or 1, pair[1].track or 0, pair[0].name))
    destinations = []
    for index, (path, media) in enumerate(pairs, 1):
        artist = clean_name(media.albumartist or media.artist)
        album = clean_name(media.album)
        if not media.year:
            raise ValueError('Missing release year; review before marking Curated')
        destination = Path(artist) / f'{album} ({media.year})' / f'{index:02d} - {clean_name(media.title)}{path.suffix.lower()}'
        destinations.append((path, destination))
    temporary = audio.parent / ('renumber-' + uuid.uuid4().hex)
    temporary.mkdir()
    try:
        for path, destination in destinations:
            target = temporary / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        # This only replaces a newly generated or expressly edited working copy.
        shutil.rmtree(audio)
        temporary.rename(audio)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def artwork_config(settings, folder, work, force=False):
    """A per-release override: never fetch when every input already has art."""
    import yaml
    from mediafile import MediaFile
    files = [p for p in work.iterdir() if p.is_file() and p.suffix.lower() in AUDIO]
    from artwork import usable_image
    complete = not force and bool(files) and all(usable_image(MediaFile(p)) for p in files)
    config = yaml.safe_load(settings.config.read_text(encoding='utf-8')) or {}
    # Pinned base configs may still reference the retired media/state bind.
    # Resolve importer logs against the live state volume before invoking Beets.
    logs = settings.state / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    config.setdefault('import', {})['log'] = str(logs / 'beets-import.log')
    config.setdefault('fetchart', {})['auto'] = not complete
    config.setdefault('embedart', {}).update(auto=not complete, ifempty=False)
    # JSON is valid YAML and preserves the base matching/security policy.
    target = folder / 'effective-beets.yaml'
    atomic_json(target, config)
    return target, complete


def import_arguments(mode, release_id, work):
    arguments=['import','-q','--copy','--nomove']
    if mode in ('single','partial'):
        arguments.append('-s')
    elif release_id:
        arguments.extend(['--search-id',release_id])
    return [*arguments,'--',str(work)]


def process_job(settings, job_id):
    from mediafile import MediaFile
    from processing import update
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job or job['status'] != 'Queued':
            return
        db.execute("UPDATE jobs SET status='Processing',updated=? WHERE id=?", (time.time(), job_id))
        rows = db.execute('SELECT * FROM tracks WHERE job_id=? ORDER BY disc,track,path', (job_id,)).fetchall()
        flag = db.execute('SELECT value FROM meta WHERE key=?', ('force_artwork:' + job_id,)).fetchone()
        force_artwork = bool(flag and flag['value'] == 'true')
        manual_row=db.execute('SELECT value FROM meta WHERE key=?',('manual:'+job_id,)).fetchone()
        manual_payload=json.loads(manual_row['value']) if manual_row else None
        selected_row=db.execute('SELECT value FROM meta WHERE key=?',('candidate:'+job_id,)).fetchone()
        selected_candidate=selected_row['value'] if selected_row else ''
    update(settings,job_id,'Preparing','Checking originals and copying tracks',0,len(rows),started=time.time())
    folder = safe_child(settings.review, job_id)
    originals = folder / 'originals'
    work = folder / 'input'
    audio = folder / 'audio'
    try:
        previous_curated = safe_child(settings.curated, job_id)
        if previous_curated.exists():
            previous_curated.rename(settings.review / (job_id + '.previous-' + uuid.uuid4().hex[:8]))
        folder.mkdir(exist_ok=True)
        for directory in (originals, work, audio):
            directory.mkdir(exist_ok=True)
        if job['mode']=='manual' and not manual_payload:
            raise ValueError('Manual metadata is missing; enter it in the inspector')
        # Retries regenerate outputs, leaving original copies intact.
        for directory in (work, audio):
            shutil.rmtree(directory)
            directory.mkdir()
        database = folder / 'beets.db'
        if database.exists():
            database.unlink()
        candidates_path=folder/'CANDIDATES.json'
        if candidates_path.exists():
            candidates_path.unlink()
        for index, row in enumerate(rows, 1):
            source = safe_child(settings.incoming, row['path'])
            if sha256(source) != row['sha256']:
                raise ValueError(f'Input changed since scan: {row["path"]}')
            original = originals / f'{index:04d}{source.suffix.lower()}'
            if not original.exists():
                shutil.copy2(source, original)
            if sha256(original) != row['sha256']:
                raise ValueError('Original copy failed checksum validation')
            target = work / original.name
            shutil.copy2(original, target)
            media = MediaFile(target)
            media.albumartist = job['artist']
            media.album = job['album']
            media.artist = row['artist'] or job['artist']
            media.title = row['title']
            if job['year']:
                media.year = job['year']
            media.track = row['track'] or index
            media.disc = row['disc'] or 1
            if job['mode']!='manual':
                from metadata_clean import clean
                for field in ('title','album','artist','albumartist'):
                    before=getattr(media,field) or '';after=clean(before)
                    if after!=before:
                        setattr(media,field,after)
                        event(settings,f'Cleaned matching {field}: {before!r} → {after!r}',job_id)
            media.save()
            update(settings,job_id,'Preparing',source.name,index,len(rows))
        atomic_json(folder / 'SOURCES.json', [dict(row) for row in rows])
        if job['mode']=='manual':
            from manual import apply
            edits={edit['id']:edit for edit in manual_payload['tracks']}
            for index,row in enumerate(rows,1):
                source=work/f'{index:04d}{Path(row["path"]).suffix.lower()}'
                target=audio/source.name;shutil.copy2(source,target)
                apply(target,manual_payload,edits[row['id']])
                update(settings,job_id,'Writing manual tags',edits[row['id']]['title'],index,len(rows))
            atomic_json(folder/'MANUAL.json',dict(artist=job['artist'],album=job['album'],year=job['year'],
                                                provenance='User-supplied metadata; completeness not verified',
                                                custom_artwork=bool(manual_payload['artwork'])))
            (folder/'BEETS.log').write_text('Manual mode: catalogue matching and artwork downloading bypassed. User-supplied metadata; explicit publication approval required.\n',encoding='utf-8')
        else:
            executable = os.environ.get('BEET', 'beet')
            effective_config, complete_art = artwork_config(settings, folder, work, force_artwork)
            config=json.loads(effective_config.read_text(encoding='utf-8'))
            plugins=config.get('plugins', [])
            if isinstance(plugins,str):plugins=plugins.split()
            config['plugins']=list(dict.fromkeys([*plugins,'rhythm_candidates']))
            config['pluginpath']=[str(Path(__file__).resolve().parent)]
            config['rhythm_candidates']={'output':str(folder/'CANDIDATES.json'),
                                        'selected':selected_candidate}
            atomic_json(effective_config,config)
            if selected_candidate and (job['mode']!='album' or selected_candidate!=job['release_id']):
                raise ValueError('Candidate selection must identify the chosen complete album')
            event(settings, 'All source tracks have artwork: keeping embedded covers; fetching disabled' if complete_art
                  else 'Some source tracks lack artwork: fetched cover will update every track in the release', job_id)
            command = [executable, '-vv', '-c', str(effective_config), '-l', str(folder / 'beets.db'),
                       '-d', str(audio), *import_arguments(job['mode'],job['release_id'],work)]
            event(settings, f'Matching {job["artist"]} / {job["album"]}', job_id)
            beets_settings = folder / 'beets-settings';beets_settings.mkdir(exist_ok=True)
            environment = dict(os.environ, BEETSDIR=str(beets_settings),PYTHONUNBUFFERED='1')
            update(settings,job_id,'Matching','Waiting for MusicBrainz / Beets; no reliable percentage available')
            with (folder / 'BEETS.log').open('w', encoding='utf-8') as log:
                process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,env=environment)
                deadline=time.monotonic()+1800
                try:
                    while process.poll() is None:
                        if time.monotonic()>deadline:raise subprocess.TimeoutExpired(command,1800)
                        with (folder/'BEETS.log').open('rb') as stream:
                            stream.seek(max(0,(folder/'BEETS.log').stat().st_size-2000))
                            lines=stream.read().decode('utf-8','replace').splitlines()
                        message=next((line for line in reversed(lines) if any(term in line for term in ('Looking up:','search terms:','Found ','Skipping.','Searching for','Tagging '))), 'Waiting for MusicBrainz / Beets')
                        update(settings,job_id,'Matching',message[:400])
                        time.sleep(1)
                    if process.returncode:raise subprocess.CalledProcessError(process.returncode,command)
                finally:
                    if process.poll() is None:
                        process.kill();process.wait()
        if len(inventory(audio)) != len(rows):
            raise ValueError('Match skipped or incomplete: read the Beets log and correct the release')
        if job['mode'] in ('single','partial'):
            update(settings,job_id,'Resolving release','Checking recording-to-release membership')
            from trackmatch import resolve_release
            from metadata_clean import clean
            resolve_release([audio / name for name in inventory(audio)],clean(job['album']),job['release_id'])
        update(settings,job_id,'Renaming','Creating clean, numbered filenames')
        normalize_output(audio)
        update(settings,job_id,'Validating','Verifying tags, track count, artwork and review revision')
        record = review_record(audio, len(rows))
        if job['mode']=='manual':
            identities={(edit['disc'],edit['track']):edit['id'] for edit in manual_payload['tracks']}
            for track in record['tracks']:
                track['source_id']=identities[(track['disc'],track['track'])]
        record['mode'] = job['mode']
        if selected_candidate:
            record['selected_candidate']=selected_candidate
            record['match_selection']='User-selected catalogue edition; publication still requires approval'
        from common import revision
        record['revision'] = revision(record)
        from taxonomy import unknown
        unapproved = unknown(settings, record['tracks'])
        label_reason = 'Unapproved genres/tags: ' + json.dumps(unapproved) if any(unapproved.values()) else ''
        atomic_json(folder / 'REVIEW.json', record)
        destination = safe_child(settings.curated, job_id)
        if destination.exists():
            raise ValueError('Curated job already exists')
        folder.rename(destination)
        with connect(settings) as db:
            db.execute("UPDATE jobs SET status='Curated',revision=?,destination=?,path=?,reason=?,updated=? WHERE id=?",
                       (record['revision'], record['destination'], str(destination), label_reason, time.time(), job_id))
        event(settings, 'Curated. Waiting for your explicit approval.', job_id)
        update(settings,job_id,'Curated','Ready for your review; nothing published',len(rows),len(rows))
    except Exception as error:
        reason = str(error)[:2000]
        with connect(settings) as db:
            db.execute("UPDATE jobs SET status='Needs review',reason=?,path=?,updated=? WHERE id=?",
                       (reason, str(folder), time.time(), job_id))
        event(settings, reason, job_id, 'warning')
        update(settings,job_id,'Needs review',reason)


def tick(settings):
    if os.name != 'posix':
        return _tick(settings)
    import fcntl
    if (settings.state / 'staging-maintenance').exists():
        # Recover a stale maintenance marker after a crashed Web UI/purge.
        with (settings.state / 'staging-purge.lock').open('a') as request:
            try:
                fcntl.flock(request, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            (settings.state / 'staging-maintenance').unlink(missing_ok=True)
    with (settings.state / 'staging-activity.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (settings.state / 'staging-maintenance').exists():
            _tick(settings)


def _tick(settings):
    from configuration import runtime
    options = runtime(settings)
    if options['paused']:
        return
    settings.stable_seconds = options['stable_seconds']
    with connect(settings) as db:
        requested = db.execute("SELECT value FROM meta WHERE key='scan_requested'").fetchone()
        if requested and requested['value'] == 'true':
            db.execute("UPDATE meta SET value='false' WHERE key='scan_requested'")
    if requested and requested['value'] == 'true':
        from intake import detect
        state = detect(settings)
        if state['settling_seconds']:
            with connect(settings) as db:
                db.execute("INSERT OR REPLACE INTO meta VALUES('scan_requested','true')")
        else:
            scan(settings)
            if options['automatic_grouping']:
                group_tagged(settings)
    with connect(settings) as db:
        jobs = [row['id'] for row in db.execute("SELECT id FROM jobs WHERE status='Queued' ORDER BY created")]
    for job_id in jobs:
        if (settings.state / 'staging-maintenance').exists():
            break
        if runtime(settings)['paused']:
            break
        process_job(settings, job_id)
    with connect(settings) as db:
        db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', ('worker_heartbeat', str(time.time())))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--root')
    args = parser.parse_args()
    settings = Settings(args.root)
    settings.initialize()
    # Only one worker owns curation; never steal jobs from a live worker.
    import fcntl
    with (settings.state / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def heartbeat():
            while True:
                try:
                    with connect(settings) as db:
                        db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', ('worker_heartbeat', str(time.time())))
                except Exception:
                    pass
                time.sleep(15)
        threading.Thread(target=heartbeat, daemon=True).start()
        with connect(settings) as db:
            db.execute("UPDATE jobs SET status='Needs review',reason='Worker interrupted; inspect and retry' WHERE status IN ('Processing','Editing')")
        while True:
            try:
                tick(settings)
            except Exception as error:
                event(settings, f'Worker error: {error}', level='error')
            if args.once:
                break
            from configuration import runtime
            time.sleep(runtime(settings)['scan_interval'])


if __name__ == '__main__':
    main()
