"""Local web review desk. Library read-only; publisher owns all library writes."""
import argparse
import hmac
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from common import (Settings, atomic_json, clean_name, connect, event,
                    review_record, safe_child)
from library import scan_library
from worker import create_job, normalize_output
from branding import asset, ASSET_PATHS

STATIC = Path(__file__).parent / 'static'


class Desk:
    def __init__(self, settings, token, demo=False):
        self.settings = settings
        self.token = token
        self.demo = demo
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        self.login_attempts = {}
        with connect(settings) as db:
            db.execute('CREATE TABLE IF NOT EXISTS web_sessions (id_hash TEXT PRIMARY KEY, csrf TEXT, expires REAL, credential TEXT)')
            db.execute('DELETE FROM web_sessions WHERE expires<=? OR credential!=?', (time.time(), self.credential()))

    def credential(self):
        return hashlib.sha256(('rhythm-session-v1:' + self.token).encode()).hexdigest()

    def save_session(self, session_id, csrf):
        with connect(self.settings) as db:
            db.execute('DELETE FROM web_sessions WHERE expires<=?', (time.time(),))
            db.execute('INSERT INTO web_sessions VALUES(?,?,?,?)',
                       (hashlib.sha256(session_id.encode()).hexdigest(), csrf, time.time()+43200, self.credential()))

    def get_session(self, session_id):
        if not session_id or len(session_id) > 128:
            return None
        with connect(self.settings) as db:
            row = db.execute('SELECT csrf,expires FROM web_sessions WHERE id_hash=? AND expires>? AND credential=?',
                             (hashlib.sha256(session_id.encode()).hexdigest(), time.time(), self.credential())).fetchone()
        return dict(row) if row else None

    def revoke_session(self, session_id):
        with connect(self.settings) as db:
            db.execute('DELETE FROM web_sessions WHERE id_hash=?', (hashlib.sha256(session_id.encode()).hexdigest(),))

    def refresh_stats(self):
        if not self.stats_lock.acquire(blocking=False):
            return False
        def run():
            try:
                scan_library(self.settings)
            except Exception as error:
                event(self.settings, f'Library stats: {error}', level='error')
            finally:
                self.stats_lock.release()
        threading.Thread(target=run, daemon=True).start()
        return True

    def snapshot(self, offset=0, limit=200, query='', status='', job_query='', job_filter='',
                 job_offset=0, job_limit=50, job_sort='updated', job_direction=-1, track_sort='updated'):
        with connect(self.settings) as db:
            counts = {row['status']: row['n'] for row in db.execute('SELECT status,count(*) n FROM tracks GROUP BY status')}
            job_counts = {row['status']: row['n'] for row in db.execute('SELECT status,count(*) n FROM jobs GROUP BY status')}
            clauses, values = ["status!='Superseded'"], []
            if query:
                clauses.append('(path LIKE ? OR artist LIKE ? OR album LIKE ? OR title LIKE ?)')
                values.extend(['%' + query + '%'] * 4)
            if status == 'actionable':
                clauses.append("status IN ('Incoming','Unresolved')")
            elif status:
                clauses.append('status=?')
                values.append(status)
            where = ' AND '.join(clauses)
            total = db.execute(f'SELECT count(*) n FROM tracks WHERE {where}', values).fetchone()['n']
            track_order = {'title': 'title COLLATE NOCASE,path', 'artist': 'artist COLLATE NOCASE,title',
                           'album': 'album COLLATE NOCASE,disc,track', 'updated': 'updated DESC'}.get(track_sort, 'updated DESC')
            tracks = [dict(row) for row in db.execute(f'SELECT * FROM tracks WHERE {where} ORDER BY {track_order} LIMIT ? OFFSET ?', [*values, limit, offset])]
            jobs = [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY updated DESC')]
            events = [dict(row) for row in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')]
            meta = {row['key']: row['value'] for row in db.execute("SELECT * FROM meta WHERE key IN ('worker_heartbeat','last_scan','library_stats') OR key LIKE 'progress:%'")}
        reconciled = False
        publisher = not self.demo and Path(self.settings.socket).exists()
        from ux import readiness, recovery, release_identity, queue_page
        for job in jobs:
            job['progress']=json.loads(meta.get('progress:'+job['id'],'null'))
            if job['status'] in ('Curated', 'Publishing'):
                receipt = self.settings.state / 'approvals' / (job['id'] + '.json')
                if receipt.exists():
                    data = json.loads(receipt.read_text(encoding='utf-8'))
                    if data['revision'] == job['revision']:
                        self.finish_approval(job['id'], data)
                        job['status'] = 'Approved'
                        reconciled = True
            target = self.settings.library / (job['destination'] or '__unset__')
            job['destination_exists'] = bool(job['destination'] and target.exists())
            review_path = self.job_folder(job) / 'REVIEW.json'
            job['cover_url'] = None
            record, found = None, {}
            if review_path.exists():
                from taxonomy import unknown
                from urllib.parse import urlencode
                try:
                    record = json.loads(review_path.read_text(encoding='utf-8'))
                except (OSError, json.JSONDecodeError):
                    record = None
            if record:
                found = unknown(self.settings, record['tracks'])
                if job['status'] == 'Curated':
                    job['reason'] = 'Unapproved genres/tags: ' + json.dumps(found) if any(found.values()) else ''
                cover = next((t for t in record['tracks'] if t.get('artwork')), None)
                if cover:
                    job['cover_url'] = '/api/media?' + urlencode(dict(job=job['id'],file=cover['file'],art=1))
            job['readiness'] = readiness(job, record, found, job['destination_exists'], publisher, self.demo)
            job['recovery'] = recovery(job)
            job['identity'] = release_identity(job, record)
        if reconciled:
            with connect(self.settings) as db:
                counts = {row['status']: row['n'] for row in db.execute('SELECT status,count(*) n FROM tracks GROUP BY status')}
                job_counts = {row['status']: row['n'] for row in db.execute('SELECT status,count(*) n FROM jobs GROUP BY status')}
            approved_ids = {job['id'] for job in jobs if job['status'] == 'Approved'}
            for track in tracks:
                if track['job_id'] in approved_ids:
                    track['status'] = 'Approved'
        from intake import detect
        page, job_total, review_counts = queue_page(jobs, job_query, job_filter, job_offset, job_limit, job_sort, job_direction)
        decisions = [j for j in jobs if j['status'] in ('Curated', 'Needs review')][:6]
        active = [j for j in jobs if j['status'] in ('Queued', 'Processing', 'Editing', 'Publishing')][:8]
        from configuration import runtime
        from taxonomy import policy
        return {'intake': detect(self.settings), 'tracks': tracks, 'track_counts': counts, 'jobs': page, 'job_counts': job_counts,
                'decisions': decisions, 'active_jobs': active, 'review_counts': review_counts, 'job_total': job_total,
                'job_offset': job_offset, 'job_limit': job_limit, 'runtime': runtime(self.settings), 'taxonomy': policy(self.settings),
                'events': events, 'track_total': total, 'offset': offset, 'limit': limit,
                'worker_heartbeat': float(meta.get('worker_heartbeat', 0)),
                'last_scan': float(meta.get('last_scan', 0)),
                'library': json.loads(meta.get('library_stats', '{}')),
                'stats_refreshing': self.stats_lock.locked(), 'demo': self.demo,
                'approval_available': publisher}

    def job_folder(self, job):
        for root in (self.settings.curated, self.settings.review, self.settings.archive):
            candidate = safe_child(root, job['id'])
            if candidate.exists():
                return candidate
        root = self.settings.archive if job['status'] == 'Approved' else self.settings.curated if job['status'] in ('Curated', 'Editing', 'Publishing') else self.settings.review
        return safe_child(root, job['id'])

    def detail(self, job_id):
        with connect(self.settings) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise ValueError('Job not found')
            job = dict(row)
            sources = [dict(row) for row in db.execute('SELECT * FROM tracks WHERE job_id=? ORDER BY disc,track,path', (job_id,))]
        folder = self.job_folder(job)
        from mediafile import MediaFile
        for source in sources:
            from metadata_clean import clean
            source['suggested']={field:clean(source[field]) for field in ('title','artist','album','albumartist')}
            source['artwork'] = False
            try:
                from artwork import usable_image
                media = MediaFile(safe_child(self.settings.incoming, source['path']))
                from taxonomy import read_labels
                source.update(read_labels(media))
                source['artwork'] = usable_image(media) is not None
                source['artwork_invalid'] = bool(media.images) and not source['artwork']
            except (OSError, ValueError):
                pass
        review_path = safe_child(folder, 'REVIEW.json')
        review = json.loads(review_path.read_text(encoding='utf-8')) if review_path.exists() else None
        from taxonomy import unknown, read_labels
        if review:
            from mediafile import MediaFile
            for track in review['tracks']:
                track.update(read_labels(MediaFile(safe_child(folder / 'audio', track['file']))))
        log = safe_child(folder, 'BEETS.log')
        log_text = ''
        if log.exists():
            with log.open('rb') as stream:
                stream.seek(max(0, log.stat().st_size - 24000))
                log_text = stream.read().decode('utf-8', 'replace')
        if review and job['status'] == 'Curated':
            found = unknown(self.settings, review['tracks'])
            job['reason'] = 'Unapproved genres/tags: ' + json.dumps(found) if any(found.values()) else ''
        from processing import read
        job['progress']=read(self.settings,job_id)
        from ux import readiness, recovery, release_identity
        found = unknown(self.settings, review['tracks']) if review else {}
        exists = bool(job['destination'] and (self.settings.library / job['destination']).exists())
        from rhythm_candidates import read_candidates
        return {'job': job, 'review': review, 'sources': sources, 'log': log_text,
                'candidates': read_candidates(folder, log_text),
                'unknown_labels': found, 'taxonomy': __import__('taxonomy').policy(self.settings),
                'readiness': readiness(job, review, found, exists, not self.demo and Path(self.settings.socket).exists(), self.demo),
                'recovery': recovery(job), 'identity': release_identity(job, review), 'destination_exists': exists}

    def retry(self, job_id, release_id, force_artwork=False, mode=None, selected_candidate=''):
        import uuid
        if type(force_artwork) is not bool:
            raise ValueError('Force artwork must be a boolean')
        if release_id:
            release_id = str(uuid.UUID(release_id))
        with connect(self.settings) as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not job or job['status'] != 'Needs review':
                raise ValueError('Only Needs review jobs can be retried')
            mode = mode or job['mode']
            if selected_candidate:
                if mode!='album' or selected_candidate!=release_id:
                    raise ValueError('Choose a complete-album candidate')
                from rhythm_candidates import read_candidates
                folder=self.job_folder(dict(job))
                log=folder/'BEETS.log'
                candidates=read_candidates(folder,log.read_text(encoding='utf-8') if log.exists() else '')
                candidate=next((c for c in candidates if c['release_id']==selected_candidate),None)
                if not candidate or candidate.get('missing') or candidate.get('unmatched'):
                    raise ValueError('Candidate unavailable or incomplete; refresh the inspector')
            db.execute('DELETE FROM meta WHERE key=?',('candidate:'+job_id,))
            if selected_candidate:
                db.execute('INSERT INTO meta VALUES(?,?)',('candidate:'+job_id,selected_candidate))
            if mode not in ('album','single','partial') or (mode=='single' and job['track_count']!=1):
                raise ValueError('Single mode requires one track; choose album or partial')
            db.execute('UPDATE jobs SET mode=? WHERE id=?',(mode,job_id))
            db.execute('DELETE FROM meta WHERE key=?',('progress:'+job_id,))
            db.execute("UPDATE jobs SET status='Queued',release_id=?,reason='',updated=? WHERE id=?", (release_id or job['release_id'], time.time(), job_id))
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('force_artwork:' + job_id, json.dumps(force_artwork)))
        event(self.settings, 'Retry queued with the chosen release', job_id)

    def ungroup(self, job_id):
        with connect(self.settings) as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not job or job['status'] not in ('Needs review', 'Curated', 'Queued'):
                raise ValueError('Only unpublished, idle jobs can be regrouped')
            review_path = self.job_folder(job) / 'REVIEW.json'
            if review_path.exists():
                record = json.loads(review_path.read_text(encoding='utf-8'))
                record['status'] = 'Regrouped'
                atomic_json(review_path, record)
            db.execute("UPDATE jobs SET status='Regrouped',updated=? WHERE id=?", (time.time(), job_id))
            db.execute("UPDATE tracks SET status='Unresolved',job_id=NULL,reason='Released from previous group; select tracks and assign a release',updated=? WHERE job_id=?", (time.time(), job_id))
        event(self.settings, 'Returned tracks to intake for manual regrouping; working copies retained', job_id)

    def edit(self, job_id, data):
        from mediafile import MediaFile
        from manual import cover
        replacement = cover(data.get('artwork'))
        with connect(self.settings) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row['status'] != 'Curated' or row['revision'] != data.get('revision'):
                raise ValueError('The reviewed version changed. Reload before editing.')
            db.execute("UPDATE jobs SET status='Editing',updated=? WHERE id=?", (time.time(), job_id))
        folder = safe_child(self.settings.curated, job_id)
        import tempfile
        temporary = Path(tempfile.mkdtemp(prefix='edit-', dir=folder))
        swapped = False
        try:
            record = json.loads((folder / 'REVIEW.json').read_text(encoding='utf-8'))
            audio = temporary / 'audio'
            shutil.copytree(folder / 'audio', audio, symlinks=True)
            artist = clean_name(data['artist'])
            album = clean_name(data['album'])
            year = int(data['year'])
            if not 1000 <= year <= 9999:
                raise ValueError('Year must have four digits')
            edits = data.get('tracks', [])
            if len(edits) != len(record['tracks']):
                raise ValueError('Edit must include every reviewed track')
            for original, edit in zip(record['tracks'], edits):
                if edit.get('file') != original['file']:
                    raise ValueError('Track order changed; reload first')
                media = MediaFile(safe_child(audio, original['file']))
                if replacement:
                    from mediafile import Image, ImageType
                    media.images = [Image(replacement, type=ImageType.front)]
                media.albumartist, media.album, media.year = artist, album, year
                media.artist = str(edit['artist']).strip()
                media.title = str(edit['title']).strip()
                media.track, media.disc = int(edit['track']), int(edit['disc'])
                from taxonomy import labels
                if 'genres' in edit:
                    media.genres = labels(edit['genres'])
                if 'tags' in edit:
                    media.grouping = '; '.join(labels(edit['tags']))
                if not media.artist or not media.title or media.track < 1 or media.disc < 1:
                    raise ValueError('Track artist, title and positive disc/track numbers are required')
                media.save()
            normalize_output(audio)
            new_record = review_record(audio, len(edits))
            if record.get('mode')=='manual':
                identities={(int(edit['disc']),int(edit['track'])):original.get('source_id') for original,edit in zip(record['tracks'],edits)}
                for track in new_record['tracks']:
                    source_id=identities.get((track['disc'],track['track']))
                    if source_id:track['source_id']=source_id
            if 'mode' in record:
                from common import revision
                new_record['mode'] = record['mode']
                new_record['revision'] = revision(new_record)
            history = folder / ('audio-before-' + secrets.token_hex(4))
            (folder / 'audio').rename(history)
            try:
                audio.rename(folder / 'audio')
                atomic_json(folder / 'REVIEW.json', new_record)
                swapped = True
            except BaseException:
                if (folder / 'audio').exists():
                    shutil.rmtree(folder / 'audio')
                history.rename(folder / 'audio')
                raise
            with connect(self.settings) as db:
                db.execute("UPDATE jobs SET status='Curated',artist=?,album=?,year=?,revision=?,destination=?,updated=? WHERE id=?",
                           (artist, album, year, new_record['revision'], new_record['destination'], time.time(), job_id))
            event(self.settings, 'Tags edited. New audio revision requires fresh approval.', job_id)
        finally:
            shutil.rmtree(temporary)
            if not swapped:
                with connect(self.settings) as db:
                    db.execute("UPDATE jobs SET status='Curated' WHERE id=? AND status='Editing'", (job_id,))

    def finish_approval(self, job_id, receipt):
        source = safe_child(self.settings.curated, job_id)
        archived = safe_child(self.settings.archive, job_id)
        if source.exists() and not archived.exists():
            try:
                source.rename(archived)
            except OSError as error:
                import errno
                if error.errno != errno.EXDEV:
                    raise
                # Docker bind mounts can reject rename even on one host disk.
                # Copy and verify everything; retain the source as a recovery copy.
                from common import sha256
                temporary = self.settings.archive / ('.archive-' + secrets.token_hex(8))
                try:
                    shutil.copytree(source, temporary, symlinks=False)
                    for file in source.rglob('*'):
                        if file.is_symlink():
                            raise ValueError('Symlink in archive source')
                        if file.is_file() and sha256(file) != sha256(temporary / file.relative_to(source)):
                            raise ValueError('Archive copy failed verification; source retained')
                    temporary.rename(archived)
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary)
        review_file = archived / 'REVIEW.json'
        if review_file.exists():
            record = json.loads(review_file.read_text(encoding='utf-8'))
            if record.get('status') != 'Approved':
                record.update(status='Approved', approved_at=receipt['approved_at'], approved_by=receipt['approved_by'])
                atomic_json(review_file, record)
        with connect(self.settings) as db:
            changed = db.execute("UPDATE jobs SET status='Approved',path=?,updated=? WHERE id=? AND status!='Approved'",
                                 (str(archived), time.time(), job_id)).rowcount
            db.execute("UPDATE tracks SET status='Approved' WHERE job_id=?", (job_id,))
        if changed:
            event(self.settings, 'Explicitly approved and published to Rhythm Attic', job_id)

    def approve(self, job_id, reviewed_revision):
        if self.demo:
            raise ValueError('Demo is isolated; publication is disabled')
        from taxonomy import validate_audio
        validate_audio(self.settings, safe_child(self.settings.curated, job_id) / 'audio')
        with connect(self.settings) as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not job or job['status'] != 'Curated' or job['revision'] != reviewed_revision:
                raise ValueError('Album changed or is not Curated. Reload and review again.')
            db.execute("UPDATE jobs SET status='Publishing',updated=? WHERE id=?", (time.time(), job_id))
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(120)
                client.connect(self.settings.socket)
                client.sendall(json.dumps({'action': 'approve', 'job_id': job_id, 'revision': reviewed_revision}).encode() + b'\n')
                buffer = b''
                while b'\n' not in buffer:
                    chunk = client.recv(4096)
                    if not chunk or len(buffer) > 65536:
                        raise ValueError('Publisher did not return a valid receipt; check activity')
                    buffer += chunk
                response = json.loads(buffer.split(b'\n', 1)[0])
            if not response['ok']:
                raise ValueError(response['error'])
            self.finish_approval(job_id, response['receipt'])
            self.refresh_stats()
            return response['receipt']
        except Exception:
            # A timeout may mean publication succeeded. Reconcile its receipt
            # before re-enabling approval; existing destinations never overwrite.
            receipt_path = self.settings.state / 'approvals' / (job_id + '.json')
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
                self.finish_approval(job_id, receipt)
                return receipt
            with connect(self.settings) as db:
                db.execute("UPDATE jobs SET status='Curated' WHERE id=? AND status='Publishing'", (job_id,))
            raise


class Handler(BaseHTTPRequestHandler):
    server_version = 'RhythmDesk/1.0'

    @property
    def desk(self):
        return self.server.desk

    def headers_common(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; base-uri 'none'")

    def json(self, data, code=200, cookie=None):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.headers_common()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(payload)

    def session(self):
        cookie = SimpleCookie(self.headers.get('Cookie', ''))
        item = cookie.get('rhythm_session')
        session_id = item.value if item else ''
        return self.desk.get_session(session_id)

    def check_origin(self):
        host = self.headers.get('Host', '')
        hostname = host.split(':')[0]
        allowed = os.environ.get('CURATOR_ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')
        if hostname not in allowed:
            raise ValueError('Host is not allowed by the web service')
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).netloc != host:
            raise ValueError('Cross-origin request refused')

    def body(self):
        if not self.headers.get('Content-Type', '').startswith('application/json'):
            raise ValueError('JSON body required')
        length = int(self.headers.get('Content-Length', '0'))
        limit=16*1024**2 if self.path.startswith('/api/jobs/') and self.path.endswith(('/manual', '/edit')) else 512000
        if not 0 < length <= limit:
            raise ValueError('Request too large or empty')
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        try:
            self.check_origin()
            url = urlparse(self.path)
            brand = asset(url.path) if url.path in ASSET_PATHS else None
            if brand is not None:
                payload, content_type = brand
                self.send_response(200)
                self.headers_common()
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if url.path in ('/', '/app.js', '/style.css', '/settings.js', '/settings.css', '/review-tools.js', '/drawer.js', '/mobile.css', '/archive.css', '/model.js'):
                file = STATIC / ('index.html' if url.path == '/' else url.path[1:])
                self.send_file(file)
                return
            session = self.session()
            if not session and url.path == '/api/session' and not self.desk.token:
                session_id, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                with self.desk.session_lock:
                    self.desk.sessions = {k: v for k, v in self.desk.sessions.items() if v['expires'] > time.time()}
                    self.desk.save_session(session_id, csrf)
                secure = '; Secure' if os.environ.get('CURATOR_SECURE_COOKIE') == '1' else ''
                self.json({'csrf': csrf, 'demo': self.desk.demo, 'auth_required': False},
                          cookie=f'rhythm_session={session_id}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200{secure}')
                return
            if not session:
                self.json({'error': 'Please sign in'}, 401)
                return
            if url.path == '/api/session':
                self.json({'csrf': session['csrf'], 'demo': self.desk.demo, 'auth_required': bool(self.desk.token)})
            elif url.path == '/api/settings':
                from configuration import describe
                from taxonomy import policy
                self.json(dict(describe(self.desk.settings), taxonomy=policy(self.desk.settings)))
            elif url.path == '/api/taxonomy':
                from taxonomy import policy
                self.json(policy(self.desk.settings))
            elif url.path == '/api/staging':
                from staging import statistics
                self.json(statistics(self.desk.settings))
            elif url.path == '/api/settings/paths.env':
                from configuration import export_paths
                content = export_paths(self.desk.settings).encode()
                self.send_response(200)
                self.headers_common()
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename=".env.paths"')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            elif url.path == '/api/snapshot':
                params = parse_qs(url.query)
                self.json(self.desk.snapshot(max(0, int(params.get('offset', ['0'])[0])),
                                             min(500, max(1, int(params.get('limit', ['200'])[0]))),
                                             params.get('q', [''])[0], params.get('status', [''])[0],
                                             params.get('job_q', [''])[0], params.get('job_filter', [''])[0],
                                             max(0, int(params.get('job_offset', ['0'])[0])),
                                             min(200, max(1, int(params.get('job_limit', ['50'])[0]))),
                                             params.get('job_sort', ['updated'])[0], int(params.get('job_direction', ['-1'])[0]),
                                             params.get('track_sort', ['updated'])[0]))
            elif url.path.startswith('/api/jobs/'):
                self.json(self.desk.detail(unquote(url.path[len('/api/jobs/'):])) )
            elif url.path == '/api/source':
                from source_browser import browse
                params=parse_qs(url.query)
                self.json(browse(self.desk.settings,params.get('path',[''])[0],int(params.get('offset',['0'])[0]),params.get('q',[''])[0]))
            elif url.path == '/api/source/import':
                from source_browser import state
                self.json(state(self.desk.settings,parse_qs(url.query)['id'][0]))
            elif url.path == '/api/media':
                params = parse_qs(url.query)
                if 'job' in params:
                    detail = self.desk.detail(params['job'][0])
                    folder = self.desk.job_folder(detail['job'])
                    file = safe_child(folder / 'audio', params['file'][0])
                    if not detail['review'] or params['file'][0] not in detail['review']['sha256']:
                        raise ValueError('Track is not part of the review')
                else:
                    with connect(self.desk.settings) as db:
                        row = db.execute('SELECT path FROM tracks WHERE id=?', (params['track'][0],)).fetchone()
                    if not row:
                        raise ValueError('Track not found')
                    file = safe_child(self.desk.settings.incoming, row['path'])
                if params.get('art', ['0'])[0] == '1':
                    from mediafile import MediaFile
                    from artwork import usable_image
                    image = usable_image(MediaFile(file))
                    if image is None:
                        self.json({'error': 'No embedded cover'}, 404)
                    else:
                        self.send_response(200)
                        self.headers_common()
                        self.send_header('Content-Type', image.mime_type)
                        self.send_header('Content-Length', str(len(image.data)))
                        self.end_headers()
                        self.wfile.write(image.data)
                else:
                    self.send_file(file, ranges=True)
            else:
                self.json({'error': 'Not found'}, 404)
        except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
            self.json({'error': str(error)}, 400)

    def do_POST(self):
        try:
            self.check_origin()
            if self.path.startswith('/api/uploads/file?'):
                session = self.session()
                if not session or not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), session['csrf']):
                    self.json({'error': 'Please sign in and verify the request'}, 403)
                    return
                if self.headers.get('Content-Type') != 'application/octet-stream' or self.headers.get('Transfer-Encoding'):
                    raise ValueError('A binary upload with Content-Length is required')
                from uploads import receive
                query = parse_qs(urlparse(self.path).query)
                self.connection.settimeout(120)
                self.json(receive(self.desk.settings, query['batch'][0], query['path'][0], self.rfile, int(self.headers.get('Content-Length', '0'))))
                return
            data = self.body()
            if self.path == '/api/login':
                ip = self.client_address[0]
                attempts, last = self.desk.login_attempts.get(ip, (0, 0))
                if attempts >= 10 and time.time() - last < 60:
                    self.json({'error': 'Too many attempts. Wait one minute.'}, 429)
                    return
                if not hmac.compare_digest(str(data.get('token', '')), self.desk.token):
                    self.desk.login_attempts[ip] = (attempts + 1, time.time())
                    self.json({'error': 'Incorrect access token'}, 401)
                    return
                self.desk.login_attempts.pop(ip, None)
                session_id, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                with self.desk.session_lock:
                    self.desk.sessions = {k: v for k, v in self.desk.sessions.items() if v['expires'] > time.time()}
                    self.desk.save_session(session_id, csrf)
                secure = '; Secure' if os.environ.get('CURATOR_SECURE_COOKIE') == '1' else ''
                self.json({'csrf': csrf, 'demo': self.desk.demo}, cookie=f'rhythm_session={session_id}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200{secure}')
                return
            session = self.session()
            if not session:
                self.json({'error': 'Please sign in'}, 401)
                return
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), session['csrf']):
                self.json({'error': 'Request verification failed'}, 403)
                return
            if self.path == '/api/source/import':
                from source_browser import start
                self.json(start(self.desk.settings,data['paths']))
            elif self.path == '/api/settings/source':
                from source_browser import save
                self.json(save(self.desk.settings,data['subdirectory']))
            elif self.path == '/api/uploads/complete':
                from uploads import complete
                self.json(complete(self.desk.settings, data['batch'], data['count']))
            elif self.path == '/api/group':
                job_id = create_job(self.desk.settings, data['track_ids'], data.get('artist', ''), data.get('album', ''), int(data.get('year') or 0), data.get('release_id', ''),data.get('mode','auto'))
                self.json({'job_id': job_id})
            elif self.path == '/api/intake/scan':
                from intake import detect
                if not detect(self.desk.settings)['ready']:
                    raise ValueError('No new or changed files to scan.')
                with connect(self.desk.settings) as db:
                    db.execute("INSERT OR REPLACE INTO meta VALUES('scan_requested','true')")
                self.json({'queued': True})
            elif self.path == '/api/staging/purge':
                from staging import purge
                self.json(purge(self.desk.settings, data.get('confirmation', '')))
            elif self.path == '/api/taxonomy/allow':
                from taxonomy import allow_label
                self.json(allow_label(self.desk.settings, data['kind'], data['value']))
                event(self.desk.settings, 'Explicitly allowed ' + str(data['kind']) + ': ' + str(data['value']))
            elif self.path == '/api/taxonomy':
                from taxonomy import save_policy
                self.json(save_policy(self.desk.settings, data))
                event(self.desk.settings, 'Genre/tag allow lists updated')
            elif self.path in ('/api/settings/runtime', '/api/settings/paths'):
                from configuration import describe, save_paths, save_runtime
                action = save_runtime if self.path.endswith('/runtime') else save_paths
                action(self.desk.settings, data)
                self.json(describe(self.desk.settings))
            elif self.path == '/api/stats/refresh':
                self.json({'started': self.desk.refresh_stats()})
            elif self.path == '/api/logout':
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
                with self.desk.session_lock:
                    self.desk.revoke_session(cookie['rhythm_session'].value)
                self.json({'ok': True}, cookie='rhythm_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            elif self.path.startswith('/api/jobs/'):
                parts = self.path.split('/')
                job_id, action = unquote(parts[3]), parts[4]
                if action == 'manual':
                    from manual import queue
                    queue(self.desk.settings,job_id,data)
                    self.json({'ok':True})
                elif action == 'retry':
                    self.desk.retry(job_id, data.get('release_id', ''), data.get('force_artwork', False),data.get('mode'),data.get('selected_candidate',''))
                    self.json({'ok': True})
                elif action == 'ungroup':
                    self.desk.ungroup(job_id)
                    self.json({'ok': True})
                elif action == 'delete-preview':
                    from staging import preview_release
                    self.json(preview_release(self.desk.settings,job_id))
                elif action == 'delete':
                    from staging import delete_release
                    self.json(delete_release(self.desk.settings,job_id,data.get('confirmation'),data.get('token')))
                elif action == 'edit':
                    self.desk.edit(job_id, data)
                    self.json({'ok': True})
                elif action == 'approve':
                    self.json({'receipt': self.desk.approve(job_id, data['revision'])})
                else:
                    self.json({'error': 'Unknown action'}, 404)
            else:
                self.json({'error': 'Not found'}, 404)
        except (ValueError, OSError, KeyError, IndexError, json.JSONDecodeError) as error:
            event(self.desk.settings, f'Web action refused: {error}', level='warning')
            self.json({'error': str(error)}, 409)

    def send_file(self, file, ranges=False):
        size = file.stat().st_size
        start, end, code = 0, size - 1, 200
        header = self.headers.get('Range') if ranges else None
        if header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
            valid = bool(match and any(match.groups()) and size)
            if valid:
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last) if last else size - 1, size - 1)
                    valid = start <= end and start < size
                else:
                    suffix = int(last)
                    valid = suffix > 0
                    start = max(0, size - suffix)
            if not valid:
                self.send_response(416)
                self.headers_common()
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.headers_common()
        self.send_header('Content-Type', mimetypes.guess_type(str(file))[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(end - start + 1))
        if ranges:
            self.send_header('Accept-Ranges', 'bytes')
        if code == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        with file.open('rb') as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = stream.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root')
    parser.add_argument('--bind', default=os.environ.get('CURATOR_BIND', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('CURATOR_PORT', '8765')))
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    token = os.environ.get('CURATOR_UI_TOKEN', '') if os.environ.get('CURATOR_AUTH_REQUIRED', '1') == '1' else ''
    if os.environ.get('CURATOR_AUTH_REQUIRED', '1') == '1' and (len(token) < 24 or token.startswith('replace-with-')):
        parser.error('Set CURATOR_UI_TOKEN to a private random value of at least 24 characters')
    settings = Settings(args.root)
    settings.initialize()
    from source_browser import recover
    recover(settings)
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.daemon_threads = True
    server.desk = Desk(settings, token, args.demo)
    server.desk.refresh_stats()
    print(f'Rhythm Desk: http://{args.bind}:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
