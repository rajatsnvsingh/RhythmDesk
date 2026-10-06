"""Read-only Rhythm Attic inventory and statistics, cached for the UI."""
import collections
import json
import time

from common import AUDIO, connect, safe_child
from worker import inspect_audio


def scan_library(settings):
    started = time.time()
    result = {'available': settings.library.is_dir(), 'scanned_at': started,
              'artists': 0, 'albums': 0, 'tracks': 0, 'bytes': 0, 'seconds': 0,
              'lossless': 0, 'lossy': 0, 'missing_art': 0, 'unreadable': 0,
              'genres': 0, 'tags': 0, 'formats': {}, 'releases': [], 'errors': []}
    if not result['available']:
        result['errors'] = ['Library is not mounted or readable by the UI account']
        return result
    albums = {}
    formats = collections.Counter()
    genre_values, tag_values = set(), set()
    previous = {}
    with connect(settings) as db:
        row = db.execute("SELECT value FROM meta WHERE key='library_file_cache'").fetchone()
        if row:
            previous = json.loads(row['value'])
    cache = {}
    from mediafile import MediaFile
    for path in settings.library.rglob('*'):
        if any(part.startswith('.') for part in path.relative_to(settings.library).parts):
            continue
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in AUDIO:
            continue
        try:
            relative = path.relative_to(settings.library).as_posix()
            safe_child(settings.library, relative)
            stat = path.stat()
            signature = f'{stat.st_size}:{stat.st_mtime_ns}'
            prior = previous.get(relative)
            if prior and prior['signature'] == signature and 'genres' in prior['metadata']:
                metadata = prior['metadata']
            else:
                metadata = inspect_audio(path)
                metadata['artwork'] = bool(MediaFile(path).images)
                from taxonomy import read_labels
                metadata.update(read_labels(MediaFile(path)))
            genre_values.update(v.casefold() for v in metadata['genres'])
            tag_values.update(v.casefold() for v in metadata['tags'])
            cache[relative] = {'signature': signature, 'metadata': metadata}
            folder = path.parent.relative_to(settings.library).as_posix()
            album = albums.setdefault(folder, {'path': folder, 'artist': metadata['albumartist'] or metadata['artist'],
                                              'album': metadata['album'] or path.parent.name,
                                              'year': metadata['year'], 'tracks': 0, 'bytes': 0,
                                              'seconds': 0, 'formats': set(), 'missing_art': 0})
            album['tracks'] += 1
            album['bytes'] += stat.st_size
            album['seconds'] += metadata['seconds']
            album['formats'].add(path.suffix[1:].upper())
            album['missing_art'] += not metadata['artwork']
            result['tracks'] += 1
            result['bytes'] += stat.st_size
            result['seconds'] += metadata['seconds']
            result['missing_art'] += not metadata['artwork']
            key = 'lossless' if metadata['codec'] in ('flac', 'alac', 'wavpack') or metadata['codec'].startswith('pcm_') else 'lossy'
            result[key] += 1
            formats[path.suffix[1:].upper()] += 1
        except Exception as error:
            result['unreadable'] += 1
            if len(result['errors']) < 20:
                result['errors'].append(f'{path.name}: {error}')
    result['releases'] = [{**value, 'formats': sorted(value['formats'])} for value in albums.values()]
    result['artists'] = len({album['artist'] for album in albums.values()})
    result['albums'] = len(albums)
    result['formats'] = dict(formats)
    result['genres'], result['tags'] = len(genre_values), len(tag_values)
    result['scan_seconds'] = time.time() - started
    with connect(settings) as db:
        db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', ('library_file_cache', json.dumps(cache)))
        db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', ('library_stats', json.dumps(result)))
    return result
