import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from common import Settings,connect
from intake import detect

class IntakeTests(unittest.TestCase):
    def test_new_changed_and_known_files(self):
        with tempfile.TemporaryDirectory() as root:
            s=Settings(root);s.stable_seconds=0;s.initialize()
            self.assertFalse(detect(s)['ready'])
            p=s.incoming/'song.mp3';p.write_bytes(b'test')
            self.assertTrue(detect(s)['ready'])
            sig=f'{p.stat().st_size}:{p.stat().st_mtime_ns}'
            with connect(s) as db:
                db.execute('INSERT INTO tracks(id,path,signature) VALUES(?,?,?)',('test','song.mp3',sig))
            self.assertFalse(detect(s)['ready'])
            p.write_bytes(b'changed')
            self.assertEqual(detect(s)['changed_files'],1)
