import sys
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from common import Settings,connect,sha256
from staging import statistics, purge,preview_release,delete_release

class StagingTests(unittest.TestCase):
    def test_purge_scope_confirmation_and_worker_lock(self):
        with tempfile.TemporaryDirectory() as root:
            s=Settings(root);s.initialize();s.library.mkdir()
            (s.incoming/'source.txt').write_text('abc')
            (s.curated/'work').mkdir();(s.curated/'work'/'data').write_text('defg')
            (s.library/'sacred').write_text('keep')
            (s.archive/'retained').write_text('keep')
            self.assertEqual(statistics(s)['files'],2)
            self.assertEqual(statistics(s)['bytes'],7)
            with self.assertRaises(ValueError):purge(s,'wrong')
            def blocked(*args):raise BlockingIOError()
            with patch.dict(sys.modules,{'fcntl':SimpleNamespace(LOCK_EX=1,LOCK_NB=2,flock=blocked)}):
                with self.assertRaisesRegex(ValueError,'already in progress'):purge(s,True)
            with patch.dict(sys.modules,{'fcntl':SimpleNamespace(LOCK_EX=1,LOCK_NB=2,flock=lambda *args:None)}):
                purge(s,True)
            self.assertEqual(statistics(s)['files'],0)
            self.assertTrue(s.incoming.is_dir())
            self.assertEqual((s.library/'sacred').read_text(),'keep')
            self.assertEqual((s.archive/'retained').read_text(),'keep')
            self.assertFalse((s.state/'staging-maintenance').exists())


class ReleaseDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Settings(self.temp.name);self.s.initialize()
        self.s.library.mkdir();(self.s.library/'sacred.mp3').write_text('published')
        (self.s.archive/'history').write_text('retain')
        self.source=self.s.incoming/'album'/'track.mp3';self.source.parent.mkdir();self.source.write_text('original')
        (self.source.parent/'cover.jpg').write_text('unrelated attachment')
        (self.s.incoming/'other.mp3').write_text('other release')
        for directory in (self.s.curated/'work.test',self.s.review/'work.test',self.s.review/'work.test.previous-123456ab'):
            directory.mkdir();(directory/'copy.mp3').write_text('working')
        with connect(self.s) as db:
            db.execute("INSERT INTO jobs(id,status,artist,album,track_count,revision) VALUES('work.test','Needs review','Artist','Album',1,'r1')")
            db.execute("INSERT INTO tracks(id,path,sha256,status,job_id) VALUES('track','album/track.mp3',?,'Assigned','work.test')",(sha256(self.source),))
        self.lock=patch.dict(sys.modules,{'fcntl':SimpleNamespace(LOCK_EX=1,LOCK_NB=2,flock=lambda *args:None)});self.lock.start()

    def tearDown(self):self.lock.stop();self.temp.cleanup()

    def test_preview_and_delete_only_this_unpublished_release(self):
        plan=preview_release(self.s,'work.test');self.assertEqual(plan['files'],4);self.assertEqual(plan['originals'],1)
        self.assertTrue(self.source.exists())
        delete_release(self.s,'work.test',True,plan['token'])
        self.assertFalse(self.source.exists());self.assertFalse((self.s.curated/'work.test').exists())
        self.assertFalse((self.s.review/'work.test.previous-123456ab').exists())
        self.assertTrue((self.source.parent/'cover.jpg').exists());self.assertTrue((self.s.incoming/'other.mp3').exists())
        self.assertEqual((self.s.library/'sacred.mp3').read_text(),'published');self.assertTrue((self.s.archive/'history').exists())
        with connect(self.s) as db:
            self.assertEqual(db.execute("SELECT status FROM jobs WHERE id='work.test'").fetchone()[0],'Purged')
            self.assertEqual(db.execute("SELECT count(*) FROM tracks WHERE job_id='work.test'").fetchone()[0],0)
        self.assertFalse((self.s.state/'staging-maintenance').exists())

    def test_confirmation_and_changed_plan_refuse_without_deletion(self):
        plan=preview_release(self.s,'work.test')
        with self.assertRaisesRegex(ValueError,'Confirm'):delete_release(self.s,'work.test',False,plan['token'])
        (self.s.curated/'work.test'/'new.txt').write_text('new')
        with self.assertRaisesRegex(ValueError,'changed since confirmation'):delete_release(self.s,'work.test',True,plan['token'])
        self.assertTrue(self.source.exists());self.assertTrue((self.s.review/'work.test').exists())

    def test_changed_original_refuses_even_after_new_preview(self):
        self.source.write_text('replaced original')
        plan=preview_release(self.s,'work.test')
        with self.assertRaisesRegex(ValueError,'changed since scan'):delete_release(self.s,'work.test',True,plan['token'])
        self.assertTrue(self.source.exists());self.assertTrue((self.s.curated/'work.test').exists())

    def test_published_and_active_jobs_cannot_be_deleted(self):
        for status in ('Approved','Publishing','Editing','Processing','Purged','Regrouped'):
            with connect(self.s) as db:db.execute("UPDATE jobs SET status=? WHERE id='work.test'",(status,))
            with self.assertRaisesRegex(ValueError,'unpublished, idle'):preview_release(self.s,'work.test')
        self.assertTrue(self.source.exists())

    def test_shared_sources_and_protected_directory_refused(self):
        with connect(self.s) as db:
            db.execute("INSERT INTO jobs(id,status) VALUES('other','Curated')")
            db.execute("INSERT INTO tracks(id,path,job_id) VALUES('othertrack','album/track.mp3','other')")
        with self.assertRaisesRegex(ValueError,'shared'):preview_release(self.s,'work.test')
        with connect(self.s) as db:db.execute("DELETE FROM tracks WHERE id='othertrack'")
        self.s.curated=self.s.library
        with self.assertRaisesRegex(ValueError,'Unsafe'):preview_release(self.s,'work.test')
        self.assertTrue((self.s.library/'sacred.mp3').exists())

    def test_traversal_and_nested_mount_refused(self):
        with self.assertRaisesRegex(ValueError,'Invalid'):preview_release(self.s,'../library')
        actual=Path.is_mount
        with patch.object(Path,'is_mount',lambda path:path.name=='copy.mp3' or actual(path)):
            with self.assertRaisesRegex(ValueError,'nested mount'):preview_release(self.s,'work.test')
        self.assertTrue(self.source.exists())
