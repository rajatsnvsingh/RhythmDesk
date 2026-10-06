import io
import tempfile
import unittest
from pathlib import Path
from common import Settings
from uploads import receive, complete

class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s=Settings(self.tmp.name);self.s.initialize();self.batch='a'*32
    def test_batch_only_visible_after_commit(self):
        receive(self.s,self.batch,'Album/01.mp3',io.BytesIO(b'music'),5)
        self.assertFalse(list(self.s.incoming.rglob('*')))
        result=complete(self.s,self.batch,1)
        self.assertEqual(result['files'],1)
        self.assertEqual((self.s.incoming/result['folder']/'Album/01.mp3').read_bytes(),b'music')
        self.assertFalse(self.s.library.exists())
    def test_reject_paths_duplicates_and_short_uploads(self):
        for name in ['../bad','/bad','x/../bad','x\\bad','C:/bad','x//bad']:
            with self.assertRaises(ValueError):receive(self.s,self.batch,name,io.BytesIO(b'x'),1)
        receive(self.s,self.batch,'song.mp3',io.BytesIO(b'x'),1)
        with self.assertRaises(ValueError):receive(self.s,self.batch,'song.mp3',io.BytesIO(b'y'),1)
        with self.assertRaises(ValueError):receive(self.s,self.batch,'short.mp3',io.BytesIO(b'x'),2)
        self.assertFalse((self.s.incoming.parent/'.uploads'/self.batch/'short.mp3').exists())
        with self.assertRaises(ValueError):complete(self.s,self.batch,2)
