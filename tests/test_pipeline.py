import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from common import Settings, connect, inventory, revision
from fixtures import track
from library import scan_library
from publisher import publish
from server import Desk, Handler, ThreadingHTTPServer
from worker import scan, group_tagged, create_job, process_job


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(self.temp.name)
        self.settings.stable_seconds = 0
        self.settings.initialize()
        self.settings.library.mkdir()
        # Offline integration fixture: real Beets import, no external match.
        # Production config still enables autotag and strict matching.
        self.settings.config = Path(self.temp.name) / 'offline.yaml'
        self.settings.config.write_text('plugins: []\nimport:\n  autotag: no\n  copy: yes\n  move: no\n  write: yes\n  quiet: yes\n  resume: no\npaths:\n  default: $albumartist/$album ($year)/$track - $title\n', encoding='utf-8')
        self.env = patch.dict(os.environ, {'BEET': str(Path(sys.executable).parent / ('beet.exe' if os.name == 'nt' else 'beet'))})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_partial_exact_edition_preserves_gaps_without_singleton_resolution(self):
        from mediafile import MediaFile
        edition='65085f39-6482-44fd-8c34-a266475bedeb'
        for position in (2,7):
            source=track(self.settings.incoming/f'{position}.flac',number=position,title=f'Track {position}')
            media=MediaFile(source);media.mb_albumid=edition;media.tracktotal=17;media.save()
        scan(self.settings)
        with connect(self.settings) as db:ids=[r['id'] for r in db.execute('SELECT id FROM tracks')]
        job=create_job(self.settings,ids,mode='partial',release_id=edition)
        with patch('trackmatch.resolve_release',side_effect=AssertionError('Must not rematch edition as singletons')):
            process_job(self.settings,job)
        with connect(self.settings) as db:self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(job,)).fetchone()[0],'Curated')
        record=json.loads((self.settings.curated/job/'REVIEW.json').read_text())
        self.assertEqual(sorted(t['track'] for t in record['tracks']),[2,7])
        self.assertEqual(record['mode'],'partial')
        self.assertEqual(len(list(self.settings.incoming.glob('*.flac'))),2)
        self.assertEqual(list(self.settings.library.iterdir()),[])

    def test_matching_cleans_website_tags_only_in_copies(self):
        from mediafile import MediaFile
        source=track(self.settings.incoming/'vendor.flac',album='Signals [songs.pk]',title='First Light - MP3Khan.Com')
        from common import sha256
        original=sha256(source)
        scan(self.settings)
        with connect(self.settings) as db:ids=[r['id'] for r in db.execute('SELECT id FROM tracks')]
        job=create_job(self.settings,ids,mode='album');process_job(self.settings,job)
        with connect(self.settings) as db:self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(job,)).fetchone()['status'],'Curated')
        record=json.loads((self.settings.curated/job/'REVIEW.json').read_text())
        self.assertEqual(record['tracks'][0]['title'],'First Light')
        self.assertEqual(record['tracks'][0]['album'],'Signals')
        self.assertEqual(sha256(source),original)
        self.assertEqual(MediaFile(source).title,'First Light - MP3Khan.Com')

    def test_artwork_policy_complete_and_partial(self):
        from worker import artwork_config
        work = Path(self.temp.name) / 'art-input'
        track(work / 'one.flac', art=True)
        two = track(work / 'two.flac', number=2, art=True)
        config, complete = artwork_config(self.settings, Path(self.temp.name), work)
        self.assertTrue(complete)
        values = json.loads(config.read_text())
        self.assertFalse(values['fetchart']['auto'])
        self.assertFalse(values['embedart']['auto'])
        from mediafile import MediaFile
        media = MediaFile(two)
        media.images = []
        media.save()
        config, complete = artwork_config(self.settings, Path(self.temp.name), work)
        self.assertFalse(complete)
        values = json.loads(config.read_text())
        self.assertTrue(values['fetchart']['auto'])
        self.assertTrue(values['embedart']['auto'])
        self.assertFalse(values['embedart']['ifempty'])

    def test_pinned_import_log_is_overridden_by_live_state(self):
        from worker import artwork_config
        self.settings.config.write_text('import:\n  log: /srv/media/music/curator-state/logs/beets-import.log\n  quiet_fallback: skip\nmatch:\n  strong_rec_thresh: 0.03\n', encoding='utf-8')
        work = Path(self.temp.name) / 'log-input'
        work.mkdir()
        config, _ = artwork_config(self.settings, Path(self.temp.name), work)
        values = json.loads(config.read_text())
        self.assertEqual(values['import']['log'], str(self.settings.state / 'logs/beets-import.log'))
        self.assertTrue((self.settings.state / 'logs').is_dir())
        self.assertEqual(values['import']['quiet_fallback'], 'skip')
        self.assertEqual(values['match']['strong_rec_thresh'], 0.03)

    def test_source_art_visible_without_curated_output(self):
        track(self.settings.incoming / 'source.flac', art=True)
        scan(self.settings)
        job_id = group_tagged(self.settings)
        with connect(self.settings) as db:
            row = db.execute('SELECT id FROM jobs').fetchone()
        detail = Desk(self.settings, 'test-token-not-used').detail(row['id'])
        self.assertIsNone(detail['review'])
        self.assertTrue(detail['sources'][0]['artwork'])

    def curate(self):
        track(self.settings.incoming / 'song.flac', number=1)
        track(self.settings.incoming / 'mixed/deep/song2.flac', number=2, title='Second Light')
        scan(self.settings)
        group_tagged(self.settings)
        with connect(self.settings) as db:
            job_id = db.execute('SELECT id FROM jobs').fetchone()['id']
        process_job(self.settings, job_id)
        desk = Desk(self.settings, 'test-access-token-is-long-enough')
        detail = desk.detail(job_id)
        self.assertEqual(detail['job']['status'], 'Curated', detail['job']['reason'] + '\n' + detail['log'])
        return desk, detail

    def test_archive_across_bind_mounts_retains_recovery_copy(self):
        import errno
        desk, detail = self.curate()
        job_id = detail['job']['id']
        receipt = publish(self.settings, job_id, detail['review']['revision'])
        self.assertFalse((self.settings.library / '.curator-publish').exists())
        self.assertTrue(self.settings.publish_root.is_dir())
        self.assertEqual(list(self.settings.publish_root.iterdir()), [])
        source = self.settings.curated / job_id
        original_rename = Path.rename
        def rename(path, target):
            if path == source:
                raise OSError(errno.EXDEV, 'Cross-device link')
            return original_rename(path, target)
        with patch('publisher.rename_no_replace',side_effect=rename):
            desk.finish_approval(job_id, receipt)
        self.assertTrue(source.exists())
        self.assertTrue((self.settings.archive / job_id / 'REVIEW.json').exists())
        self.assertEqual(desk.detail(job_id)['job']['status'], 'Approved')

    def test_concurrent_archive_finalization_is_idempotent(self):
        import errno
        desk,detail=self.curate();job=detail['job']['id']
        receipt=publish(self.settings,job,detail['review']['revision'])
        source=self.settings.curated/job;rename=Path.rename;copytree=shutil.copytree
        def move(path,target):
            if path==source:raise OSError(errno.EXDEV,'Cross-device link')
            return rename(path,target)
        def copy(*args,**kwargs):
            time.sleep(.03);return copytree(*args,**kwargs)
        errors=[]
        def finish():
            try:desk.finish_approval(job,receipt)
            except Exception as error:errors.append(error)
        with patch('publisher.rename_no_replace',side_effect=move),patch('server.shutil.copytree',side_effect=copy):
            threads=[threading.Thread(target=finish) for _ in range(2)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(10);self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[]);self.assertTrue(source.exists())
        self.assertEqual(list(self.settings.archive.glob('.archive-*')),[])
        with connect(self.settings) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE job_id=? AND message='Explicitly approved and published to Rhythm Attic'",(job,)).fetchone()[0],1)

    def test_existing_different_archive_is_not_replaced(self):
        desk,detail=self.curate();job=detail['job']['id']
        receipt=publish(self.settings,job,detail['review']['revision'])
        archived=self.settings.archive/job;shutil.copytree(self.settings.curated/job,archived)
        file=next((archived/'audio').rglob('*.flac'));file.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'Archive audio differs'):desk.finish_approval(job,receipt)
        self.assertEqual(file.read_bytes(),b'corrupt');self.assertTrue((self.settings.curated/job).exists())

    def test_approval_returns_before_background_work_finishes(self):
        desk,detail=self.curate();job=detail['job']['id']
        started=threading.Event();release=threading.Event();finished=threading.Event()
        def slow(*args):started.set();release.wait(5);finished.set()
        with patch.object(desk,'_publish_background',side_effect=slow):
            result=desk.approve(job,detail['review']['revision'])
            self.assertTrue(result['queued']);self.assertTrue(started.wait(2));self.assertFalse(finished.is_set())
            self.assertEqual(desk.detail(job)['job']['status'],'Publishing')
            self.assertFalse(desk.schedule_publication(job,detail['review']['revision']))
            with self.assertRaises(ValueError):desk.approve(job,detail['review']['revision'])
            release.set();self.assertTrue(finished.wait(2))

    def test_background_progress_frames_finish_publication(self):
        from unittest.mock import MagicMock
        from processing import read
        desk,detail=self.curate();job=detail['job']['id'];rev=detail['review']['revision']
        with connect(self.settings) as db:db.execute("UPDATE jobs SET status='Publishing' WHERE id=?",(job,))
        connection=MagicMock();connection.__enter__.return_value=connection
        def receive(*args):
            frames=[]
            receipt=publish(self.settings,job,rev,progress=lambda value:frames.append({'progress':value}))
            frames.append({'ok':True,'receipt':receipt})
            return b''.join(json.dumps(frame).encode()+b'\n' for frame in frames)
        connection.recv.side_effect=receive
        with patch('server.socket.socket',return_value=connection),patch('server.socket.AF_UNIX',1,create=True),patch.object(desk,'refresh_stats'):
            desk._publish_background(job,rev)
        self.assertEqual(desk.detail(job)['job']['status'],'Approved',str(read(self.settings,job)))
        self.assertEqual(read(self.settings,job)['phase'],'Published')

    def test_snapshot_only_schedules_archive_recovery(self):
        desk,detail=self.curate();job=detail['job']['id'];rev=detail['review']['revision']
        publish(self.settings,job,rev)
        with patch.object(desk,'schedule_publication') as schedule,patch.object(desk,'finish_approval') as finish:
            desk.snapshot();schedule.assert_called_once_with(job,rev);finish.assert_not_called()

    def test_ambiguous_publication_disconnect_keeps_approval_claimed(self):
        from unittest.mock import MagicMock
        desk,detail=self.curate();job=detail['job']['id'];rev=detail['review']['revision']
        with connect(self.settings) as db:db.execute("UPDATE jobs SET status='Publishing' WHERE id=?",(job,))
        connection=MagicMock();connection.__enter__.return_value=connection;connection.recv.return_value=b''
        with patch('server.socket.socket',return_value=connection),patch('server.socket.AF_UNIX',1,create=True):
            desk._publish_background(job,rev)
        detail=desk.detail(job)
        self.assertEqual(detail['job']['status'],'Publishing')
        self.assertIn('outcome needs attention',detail['job']['reason'])
        self.assertFalse(detail['readiness']['can_publish'])
        self.assertEqual(list(self.settings.library.iterdir()),[])

    def test_prepublication_failure_is_visible_and_retains_curated_copy(self):
        from unittest.mock import MagicMock
        desk,detail=self.curate();job=detail['job']['id'];rev=detail['review']['revision']
        with connect(self.settings) as db:db.execute("UPDATE jobs SET status='Publishing' WHERE id=?",(job,))
        connection=MagicMock();connection.__enter__.return_value=connection;connection.connect.side_effect=OSError('Publisher unavailable')
        with patch('server.socket.socket',return_value=connection),patch('server.socket.AF_UNIX',1,create=True):
            desk._publish_background(job,rev)
        detail=desk.detail(job)
        self.assertEqual(detail['job']['status'],'Curated')
        self.assertIn('Publication failed: Publisher unavailable',detail['job']['reason'])
        self.assertTrue((self.settings.curated/job/'audio').exists())

    def test_startup_resumes_only_previously_authorized_publications(self):
        desk,detail=self.curate();job=detail['job']['id'];rev=detail['review']['revision']
        with patch.object(desk,'schedule_publication') as schedule:
            desk.resume_publications();schedule.assert_not_called()
            with connect(self.settings) as db:db.execute("UPDATE jobs SET status='Publishing' WHERE id=?",(job,))
            desk.resume_publications();schedule.assert_called_once_with(job,rev)

    def test_flexible_intake_short_boundary_duplicates_and_idempotency(self):
        incoming = self.settings.incoming
        first = track(incoming / 'loose.flac', number=1)
        track(incoming / 'mixed/nested/second.flac', number=2)
        track(incoming / 'unknown.flac', tagged=False)
        track(incoming / 'short.flac', seconds=9.5, number=3)
        track(incoming / 'exact-ten.flac', seconds=10, number=4)
        (incoming / 'full-cover.txt').write_text('ignore non-audio')
        shutil.copy2(first, incoming / 'duplicate.flac')
        self.assertEqual(scan(self.settings), 7)
        self.assertEqual(scan(self.settings), 0)
        os.utime(first, (time.time(), time.time()))
        self.assertEqual(scan(self.settings), 0)
        with connect(self.settings) as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM tracks').fetchone()['n'], 7)
        with connect(self.settings) as db:
            statuses = {r['path']: r['status'] for r in db.execute('SELECT * FROM tracks')}
        self.assertEqual(statuses['short.flac'], 'Ignored')
        self.assertEqual(statuses['full-cover.txt'], 'Ignored')
        self.assertEqual(statuses['unknown.flac'], 'Unresolved')
        self.assertEqual(statuses['exact-ten.flac'], 'Incoming')
        self.assertEqual(list(statuses.values()).count('Duplicate'), 1)
        group_tagged(self.settings)
        with connect(self.settings) as db:
            job = db.execute('SELECT * FROM jobs').fetchone()
        self.assertEqual(job['track_count'], 3)
        self.assertEqual(list(self.settings.library.iterdir()), [])

    def test_real_beets_curation_never_writes_library(self):
        _, detail = self.curate()
        self.assertEqual(list(self.settings.library.iterdir()), [])
        self.assertEqual(len(detail['review']['tracks']), 2)
        self.assertTrue(all(t['artwork'] for t in detail['review']['tracks']))
        self.assertTrue((self.settings.incoming / 'song.flac').exists())

    def test_explicit_approval_and_existing_album_protection(self):
        desk, detail = self.curate()
        job_id = detail['job']['id']
        reviewed = detail['review']['revision']
        with self.assertRaises(ValueError):
            publish(self.settings, job_id, '0' * 64)
        self.assertEqual(list(self.settings.library.iterdir()), [])
        receipt = publish(self.settings, job_id, reviewed)
        target = self.settings.library / receipt['destination']
        before = inventory(target)
        # Repeat approvals reconcile the same successful receipt.
        self.assertEqual(publish(self.settings, job_id, reviewed), receipt)
        self.assertEqual(inventory(target), before)
        # A different job aimed at that album is refused, even with same bytes.
        clone_id = 'work.another-job'
        shutil.copytree(self.settings.curated / job_id, self.settings.curated / clone_id)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            publish(self.settings, clone_id, reviewed)
        self.assertEqual(inventory(target), before)
        with patch.object(desk,'refresh_stats'):
            desk.snapshot()
            deadline=time.monotonic()+5
            while desk.publications and time.monotonic()<deadline:time.sleep(.02)
            self.assertFalse(desk.publications)
            snapshot = desk.snapshot()
        self.assertEqual(snapshot['job_counts']['Approved'], 1)
        self.assertTrue((self.settings.archive / job_id / 'originals').is_dir())
        self.assertEqual(inventory(target), before)

    def test_changed_audio_cannot_be_published(self):
        _, detail = self.curate()
        record = detail['review']
        path = self.settings.curated / detail['job']['id'] / 'audio' / record['tracks'][0]['file']
        with path.open('ab') as stream:
            stream.write(b'tampered')
        with self.assertRaisesRegex(ValueError, 'differs'):
            publish(self.settings, detail['job']['id'], record['revision'])
        self.assertEqual(list(self.settings.library.iterdir()), [])

    def test_tag_edit_creates_new_review_revision(self):
        desk, detail = self.curate()
        job_id = detail['job']['id']
        old = detail['review']['revision']
        desk.edit(job_id, {'revision': old, 'artist': 'Northbound', 'album': 'Corrected Signals',
                          'year': 2024, 'tracks': detail['review']['tracks']})
        new = desk.detail(job_id)['review']
        self.assertNotEqual(new['revision'], old)
        self.assertIn('Corrected Signals', new['destination'])
        with self.assertRaises(ValueError):
            publish(self.settings, job_id, old)
        self.assertEqual(list(self.settings.library.iterdir()), [])

    def test_unidentified_tracks_can_be_grouped_in_ui(self):
        track(self.settings.incoming / 'unknown.flac', tagged=False)
        scan(self.settings)
        with connect(self.settings) as db:
            row = db.execute('SELECT * FROM tracks').fetchone()
        job_id = create_job(self.settings, [row['id']], 'Chosen Artist', 'Chosen Album', 2024)
        with connect(self.settings) as db:
            job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        self.assertEqual(job['status'], 'Queued')
        self.assertEqual(job['artist'], 'Chosen Artist')

    def test_library_stats_are_read_only_and_ignore_transfer_files(self):
        file = track(self.settings.library / 'Northbound/Signals (2024)/01 - Song.flac')
        before = inventory(file.parent)
        track(self.settings.library / '.curator-publish/transfer/not-approved.flac')
        stats = scan_library(self.settings)
        self.assertEqual((stats['albums'], stats['tracks'], stats['artists']), (1, 1, 1))
        self.assertEqual(stats['lossless'], 1)
        self.assertEqual(stats['missing_art'], 0)
        self.assertEqual(inventory(file.parent), before)

    def test_http_auth_csrf_and_audio_range(self):
        desk, detail = self.curate()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.desk = desk
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        def request(path, data=None, headers=None):
            headers = headers or {}
            if data is not None:
                headers['Content-Type'] = 'application/json'
            return urllib.request.urlopen(urllib.request.Request(base + path, data=None if data is None else json.dumps(data).encode(), headers=headers))
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                request('/api/snapshot')
            self.assertEqual(caught.exception.code, 401)
            response = request('/api/login', {'token': desk.token})
            cookie = response.headers['Set-Cookie'].split(';')[0]
            csrf = json.load(response)['csrf']
            batch = 'b' * 32
            upload_url = base + '/api/uploads/file?batch=' + batch + '&path=Album/song.mp3'
            binary = urllib.request.Request(upload_url, data=b'music', headers={
                'Cookie': cookie, 'X-CSRF-Token': csrf, 'Content-Type': 'application/octet-stream'})
            self.assertEqual(json.load(urllib.request.urlopen(binary))['bytes'], 5)
            result = json.load(request('/api/uploads/complete', {'batch': batch, 'count': 1},
                                       {'Cookie': cookie, 'X-CSRF-Token': csrf}))
            self.assertEqual((self.settings.incoming / result['folder'] / 'Album/song.mp3').read_bytes(), b'music')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                request('/api/stats/refresh', {}, {'Cookie': cookie})
            self.assertEqual(caught.exception.code, 403)
            snapshot = json.load(request('/api/snapshot', headers={'Cookie': cookie}))
            self.assertEqual(snapshot['job_counts']['Curated'], 1)
            source_id = detail['sources'][0]['id']
            response = request('/api/media?track=' + source_id, headers={'Cookie': cookie, 'Range': 'bytes=0-15'})
            self.assertEqual(response.status, 206)
            self.assertEqual(len(response.read()), 16)
            media_path = '/api/media?track=' + source_id
            full = request(media_path, headers={'Cookie': cookie}).read()
            for byte_range, expected in [('bytes=16-31', full[16:32]),
                                         ('bytes=32-', full[32:]),
                                         ('bytes=-16', full[-16:]),
                                         ('bytes=-999999999', full)]:
                response = request(media_path, headers={'Cookie': cookie, 'Range': byte_range})
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), expected)
            with self.assertRaises(urllib.error.HTTPError) as invalid:
                request(media_path, headers={'Cookie': cookie, 'Range': 'bytes=999999999-'})
            self.assertEqual(invalid.exception.code, 416)
            self.assertEqual(invalid.exception.headers['Content-Range'], f'bytes */{len(full)}')
            with self.assertRaises(urllib.error.HTTPError):
                request('/api/stats/refresh', {}, {'Cookie': cookie, 'X-CSRF-Token': csrf, 'Origin': 'https://evil.example'})
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
