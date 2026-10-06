import sys
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from common import Settings
from staging import statistics, purge

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
