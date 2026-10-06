"""Build an isolated demo with real synthetic audio, never real library paths."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

from common import Settings, atomic_json, connect, event, review_record
from worker import scan, group_tagged


def seed(root):
    root = Path(root).resolve()
    if 'demo' not in root.name.lower():
        raise ValueError('Demo root directory name must include demo')
    if (root / 'curator-state/curator.sqlite3').exists():
        print('Existing demo kept unchanged')
        return
    sys.path.insert(0, str(Path(__file__).parents[1] / 'tests'))
    from fixtures import track
    settings = Settings(root)
    settings.stable_seconds = 0
    settings.initialize()
    track(settings.incoming / 'First Light.flac', number=1)
    track(settings.incoming / 'mixed/nested/Coastline.flac', title='Coastline', number=2)
    track(settings.incoming / 'loose-untagged.flac', tagged=False)
    track(settings.incoming / 'extras/intro.flac', seconds=6, title='Intro', number=3)
    (settings.incoming / 'extras/booklet.txt').write_text('Original non-audio retained', encoding='utf-8')
    shutil.copy2(settings.incoming / 'First Light.flac', settings.incoming / 'duplicate.flac')
    scan(settings)
    group_tagged(settings)
    with connect(settings) as db:
        job = db.execute('SELECT * FROM jobs LIMIT 1').fetchone()
        sources = db.execute('SELECT * FROM tracks WHERE job_id=?', (job['id'],)).fetchall()
    folder = settings.curated / job['id']
    audio = folder / 'audio/Northbound/Signals (2024)'
    audio.mkdir(parents=True)
    originals = folder / 'originals'
    originals.mkdir()
    for index, source in enumerate(sorted(sources, key=lambda s: s['track']), 1):
        shutil.copy2(settings.incoming / source['path'], audio / f'{index:02d} - {source["title"]}.flac')
        shutil.copy2(settings.incoming / source['path'], originals / f'{index}.flac')
    record = review_record(folder / 'audio', len(sources))
    atomic_json(folder / 'REVIEW.json', record)
    with connect(settings) as db:
        db.execute("UPDATE jobs SET status='Curated',destination=?,revision=?,path=?,updated=? WHERE id=?", (record['destination'], record['revision'], str(folder), time.time(), job['id']))
        now = time.time()
        db.execute('INSERT INTO jobs(id,status,artist,album,year,reason,created,updated,track_count) VALUES(?,?,?,?,?,?,?,?,?)', ('work.demo-review', 'Needs review', 'Various Artists', 'Night Drive Collection', 2020, 'Multiple release editions. Choose the intended MusicBrainz release.', now, now, 8))
        db.execute('INSERT INTO jobs(id,status,artist,album,year,reason,created,updated,track_count) VALUES(?,?,?,?,?,?,?,?,?)', ('work.demo-queued', 'Queued', 'The Satellites', 'Quiet Orbit', 2022, '', now, now, 6))
    for i, (artist, album) in enumerate([('The Satellites', 'Quiet Orbit'), ('Harbour Lines', 'After Hours'), ('Northbound', 'Low Tide')]):
        for index in range(1, 3):
            track(settings.library / artist / f'{album} (2024)' / f'{index:02d} - Track {index}.flac', artist=artist, album=album, title=f'Track {index}', number=index, art=i != 2)
    event(settings, 'Demo seeded with tagged, loose, mixed, short and duplicate audio')
    event(settings, 'Signals is Curated and waiting for explicit approval', job['id'])
    event(settings, 'Night Drive Collection needs an edition decision', 'work.demo-review', 'warning')
    print(f'Demo created in {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    seed(parser.parse_args().root)
