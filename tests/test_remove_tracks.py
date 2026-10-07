import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from fixtures import track
from common import Settings, atomic_json, connect, inventory, review_record, revision
from server import Desk
from publisher import publish

JOB = 'work.1234567890abcdef'


class SongRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(self.temp.name)
        self.settings.initialize()
        self.settings.library.mkdir()
        self.folder = self.settings.curated / JOB
        self.audio = self.folder / 'audio'
        for i in (1, 2, 3):
            track(self.audio / 'Northbound' / 'Signals (2024)' / f'{i:02} - Song {i}.flac', number=i, title=f'Song {i}')
        self.record = review_record(self.audio, 3)
        self.record['mode'] = 'album'
        for i, t in enumerate(self.record['tracks']):
            t.update(source_id=f'source-{i}', match_status='catalogue')
        self.record['revision'] = revision(self.record)
        atomic_json(self.folder / 'REVIEW.json', self.record)
        self.original = self.settings.incoming / 'original.flac'
        track(self.original)
        self.before = inventory(self.audio)
        with connect(self.settings) as db:
            db.execute("INSERT INTO jobs(id,status,artist,album,year,track_count,mode,reason,revision,destination,updated) VALUES(?,'Curated','Northbound','Signals',2024,3,'album','',?,?,0)",
                       (JOB, self.record['revision'], self.record['destination']))
        self.desk = Desk(self.settings, 'token')

    def tearDown(self):
        self.temp.cleanup()

    def remove(self, files=None, **overrides):
        data = dict(revision=self.record['revision'], files=files or [self.record['tracks'][1]['file']], confirmation=True)
        data.update(overrides)
        self.desk.remove_tracks(JOB, data)

    def test_removes_only_prepared_song_preserves_bytes_positions_and_history(self):
        self.remove()
        result = json.loads((self.folder / 'REVIEW.json').read_text())
        self.assertEqual([t['track'] for t in result['tracks']], [1, 3])
        self.assertEqual(result['mode'], 'partial')
        self.assertNotEqual(result['revision'], self.record['revision'])
        self.assertEqual(inventory(self.audio), {k: v for k, v in self.before.items() if k != self.record['tracks'][1]['file']})
        self.assertEqual(inventory(next(self.folder.glob('audio-before-*'))), self.before)
        self.assertTrue(self.original.exists())
        self.assertEqual(result['excluded_tracks'][0]['title'], 'Song 2')
        with connect(self.settings) as db:
            job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (JOB,)).fetchone())
        self.assertEqual((job['status'], job['track_count'], job['mode']), ('Curated', 2, 'partial'))
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.desk.approve(JOB, self.record['revision'])
        receipt = publish(self.settings, JOB, result['revision'])
        self.assertEqual(set(receipt['files']), {Path(f).name for f in result['sha256']})
        self.assertEqual(len(receipt['files']), 2)

    def test_batch_removal_keeps_one_song_and_does_not_auto_publish(self):
        self.remove([t['file'] for t in self.record['tracks'][1:]])
        result = json.loads((self.folder / 'REVIEW.json').read_text())
        self.assertEqual(len(result['tracks']), 1)
        self.assertEqual(len(result['excluded_tracks']), 2)
        self.assertFalse(list(self.settings.library.iterdir()))
        self.assertEqual(result['tracks'][0]['source_id'], 'source-0')

    def test_explicitly_removing_unverified_song_does_not_block_remaining_tracks(self):
        self.record['mode'] = 'partial'
        self.record['tracks'][1]['match_status'] = 'unverified'
        self.record['revision'] = revision(self.record)
        atomic_json(self.folder / 'REVIEW.json', self.record)
        with connect(self.settings) as db:
            db.execute('UPDATE jobs SET mode=?,revision=? WHERE id=?', ('partial', self.record['revision'], JOB))
        self.remove()
        result = json.loads((self.folder / 'REVIEW.json').read_text())
        self.assertEqual(result['excluded_tracks'][0]['match_status'], 'unverified')
        receipt = publish(self.settings, JOB, result['revision'])
        self.assertEqual(len(receipt['files']), 2)

    def test_subsequent_edit_retains_exclusions_and_unverified_evidence(self):
        self.record['mode'] = 'partial'
        self.record['tracks'][0]['match_status'] = 'unverified'
        self.record['revision'] = revision(self.record)
        atomic_json(self.folder / 'REVIEW.json', self.record)
        with connect(self.settings) as db:
            db.execute('UPDATE jobs SET mode=?,revision=? WHERE id=?', ('partial', self.record['revision'], JOB))
        self.remove()
        result = json.loads((self.folder / 'REVIEW.json').read_text())
        self.desk.edit(JOB, dict(revision=result['revision'], artist='Northbound', album='Signals', year=2024, tracks=result['tracks']))
        edited = json.loads((self.folder / 'REVIEW.json').read_text())
        self.assertEqual(edited['excluded_tracks'], result['excluded_tracks'])
        self.assertEqual(edited['tracks'][0]['match_status'], 'unverified')
        with self.assertRaisesRegex(ValueError, 'unresolved track'):
            publish(self.settings, JOB, edited['revision'])

    def test_rejects_all_tracks_unknown_duplicate_paths_stale_and_missing_consent(self):
        paths = [t['file'] for t in self.record['tracks']]
        for files, overrides in [(paths, {}), (['../escape.flac'], {}), ([paths[0], paths[0]], {}),
                                 ([paths[0]], {'revision': 'old'}), ([paths[0]], {'confirmation': False})]:
            with self.subTest(files=files, overrides=overrides), self.assertRaises(ValueError):
                self.remove(files, **overrides)
            self.assertEqual(inventory(self.audio), self.before)
            with connect(self.settings) as db:
                self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?', (JOB,)).fetchone()[0], 'Curated')

    def test_approved_release_cannot_be_modified(self):
        with connect(self.settings) as db:
            db.execute("UPDATE jobs SET status='Approved' WHERE id=?", (JOB,))
        with self.assertRaises(ValueError):
            self.remove()
        self.assertEqual(inventory(self.audio), self.before)

    def test_changed_audio_blocks_removal(self):
        path = self.audio / self.record['tracks'][0]['file']
        with path.open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'audio changed'):
            self.remove()

    def test_review_write_failure_restores_original_prepared_copy(self):
        with patch('server.atomic_json', side_effect=OSError('failed write')):
            with self.assertRaisesRegex(OSError, 'failed write'):
                self.remove()
        self.assertEqual(inventory(self.audio), self.before)
        self.assertEqual(json.loads((self.folder / 'REVIEW.json').read_text())['revision'], self.record['revision'])


if __name__ == '__main__':
    unittest.main()
