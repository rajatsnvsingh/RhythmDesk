"""Shared paths, database and immutable review records."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

AUDIO = {'.flac', '.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.aiff', '.alac', '.aif'}


class Settings:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('CURATOR_ROOT', '/srv/media/music')).resolve()
        self.incoming = self.root / 'staging/incoming'
        self.curated = self.root / 'staging/Curated'
        self.review = self.root / 'staging/needs-review'
        self.state = Path(os.environ.get('CURATOR_STATE_ROOT', str(self.root / 'curator-state'))).resolve()
        self.archive = self.state / 'processed'
        self.library = self.root / 'rhythm-attic'
        self.db = self.state / 'curator.sqlite3'
        self.config = Path(os.environ.get('BEETS_CONFIG', '/etc/rhythm-curator/config.yaml'))
        self.socket = os.environ.get('CURATOR_PUBLISH_SOCKET', '/run/rhythm-publisher/publish.sock')
        self.stable_seconds = int(os.environ.get('STABLE_SECONDS', '180'))

    def initialize(self):
        os.umask(0o007)
        for directory in (self.incoming, self.curated, self.review, self.state, self.archive, self.state / 'taxonomy'):
            directory.mkdir(parents=True, exist_ok=True)
        with connect(self) as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS tracks (
              id TEXT PRIMARY KEY, path TEXT, signature TEXT, sha256 TEXT,
              status TEXT, reason TEXT, artist TEXT, albumartist TEXT, album TEXT,
              title TEXT, year INTEGER, track INTEGER, disc INTEGER, release_id TEXT,
              seconds REAL, bitrate INTEGER, sample_rate INTEGER, bitdepth INTEGER,
              codec TEXT, bytes INTEGER, job_id TEXT, updated REAL,
              UNIQUE(path,signature));
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, status TEXT, artist TEXT, album TEXT, year INTEGER,
              release_id TEXT, reason TEXT, created REAL, updated REAL, revision TEXT,
              path TEXT, track_count INTEGER, destination TEXT);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, level TEXT,
              job_id TEXT, message TEXT);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            ''')
            if 'mode' not in {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute("ALTER TABLE jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'album'")


@contextlib.contextmanager
def connect(settings):
    db = sqlite3.connect(settings.db, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def event(settings, message, job_id='', level='info'):
    with connect(settings) as db:
        db.execute('INSERT INTO events(at,level,job_id,message) VALUES(?,?,?,?)',
                   (time.time(), level, job_id, message))


def atomic_json(path, data):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_child(root, relative):
    root = root.resolve()
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Invalid relative path')
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError('Symlinks are not permitted')
        current = current.parent
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('Path is outside the configured directory')
    return path


def inventory(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Missing or symlinked audio directory')
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink in audio output')
        if path.is_file():
            if path.suffix.lower() not in AUDIO:
                raise ValueError(f'Non-audio output: {path.name}')
            result[path.relative_to(root).as_posix()] = sha256(path)
    if not result:
        raise ValueError('No audio output')
    return result


def clean_name(value):
    if value is None:
        raise ValueError('Missing artist, album or title')
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')
    if not value or value in ('.', '..'):
        raise ValueError('Empty artist, album or title')
    return value[:180]


def revision(record):
    values = {key: record[key] for key in ('destination', 'sha256', 'tracks')}
    if 'mode' in record:
        values['mode'] = record['mode']
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def review_record(audio, expected_count=None):
    from mediafile import MediaFile
    from taxonomy import read_labels
    files = inventory(audio)
    if expected_count is not None and len(files) != expected_count:
        raise ValueError('Not all eligible tracks were imported; review the release')
    albums = {Path(name).parent.as_posix() for name in files}
    if len(albums) != 1 or len(Path(next(iter(albums))).parts) != 2:
        raise ValueError('Expected exactly one Artist/Album (Year) release')
    tracks = []
    for name in files:
        media = MediaFile(audio / name)
        if media.length < 10:
            raise ValueError('Output contains a track shorter than 10 seconds')
        if not all((media.title, media.album, media.artist, media.track)):
            raise ValueError('Missing required title, artist, album or track tags')
        tracks.append({'file': name, 'title': media.title, 'artist': media.artist,
                       'albumartist': media.albumartist or media.artist,
                       'album': media.album, 'year': media.year, 'track': media.track,
                       'disc': media.disc or 1, 'seconds': media.length,
                       'bitrate': media.bitrate, 'sample_rate': media.samplerate,
                       'bitdepth': media.bitdepth, 'format': media.format,
                       'artwork': bool(media.images), 'release_id': media.mb_albumid,
                       **read_labels(media)})
    record = {'status': 'Curated', 'destination': next(iter(albums)),
              'sha256': files, 'tracks': tracks, 'prepared_at': time.time()}
    record['revision'] = revision(record)
    return record
