import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import Settings
from source_browser import browse,copy_batch,resolve,save,state,recover,write_state

class SourceBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=Settings(Path(self.tmp.name)/'workspace');self.s.initialize()
        self.source=Path(self.tmp.name)/'drawer';self.source.mkdir()
        self.env=patch.dict(os.environ,{'CURATOR_SOURCE_ROOT':str(self.source),'CURATOR_HOST_SOURCE':str(self.source)})
        self.env.start();self.addCleanup(self.env.stop)
        (self.source/'Artist/Album').mkdir(parents=True)
        (self.source/'Artist/Album/song.mp3').write_bytes(b'original audio')
        (self.source/'rhythm-attic').mkdir();(self.source/'rhythm-attic/private.mp3').write_bytes(b'protected')
    def test_browse_and_copy_preserve_originals(self):
        self.assertEqual([x['name'] for x in browse(self.s)['items']],['Artist'])
        job='a'*32;copy_batch(self.s,job,[self.source/'Artist'])
        result=state(self.s,job);self.assertEqual(result['status'],'Complete')
        self.assertEqual((self.s.incoming/result['folder']/'Artist/Album/song.mp3').read_bytes(),b'original audio')
        self.assertEqual((self.source/'Artist/Album/song.mp3').read_bytes(),b'original audio')
    def test_protected_and_traversal_rejected(self):
        for path in ['../escape','rhythm-attic','Artist/../../escape','C:\\escape']:
            with self.assertRaises(ValueError):resolve(self.s,path)
        save(self.s,'Artist');self.assertEqual([x['name'] for x in browse(self.s)['items']],['Album'])
    def test_restart_marks_unfinished_copy(self):
        job='b'*32;write_state(self.s,job,{'status':'Copying'})
        recover(self.s);self.assertEqual(state(self.s,job)['status'],'Interrupted')
    def test_host_mapped_operational_folders_excluded(self):
        folder=self.source/'curator-test/state';folder.mkdir(parents=True)
        with patch.dict(os.environ,{'CURATOR_HOST_STATE':str(folder)}):
            self.assertEqual(browse(self.s,'curator-test')['items'],[])
            with self.assertRaises(ValueError):resolve(self.s,'curator-test/state')
    def test_failed_copy_not_visible_in_incoming(self):
        job='c'*32
        with patch('source_browser.shutil.copyfileobj',side_effect=OSError('Simulated read failure')):
            copy_batch(self.s,job,[self.source/'Artist'])
        self.assertEqual(state(self.s,job)['status'],'Failed')
        self.assertFalse((self.s.incoming/('Import-'+job)).exists())
        self.assertEqual((self.source/'Artist/Album/song.mp3').read_bytes(),b'original audio')
