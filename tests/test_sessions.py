import sys
from pathlib import Path
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from common import Settings, connect
from server import Desk

class SessionTests(unittest.TestCase):
    def test_restart_logout_expiry_and_rotation(self):
        with tempfile.TemporaryDirectory() as root:
            s=Settings(root);s.initialize()
            first=Desk(s,'private-test-token')
            first.save_session('browser-secret','csrf-value')
            second=Desk(s,'private-test-token')
            self.assertEqual(second.get_session('browser-secret')['csrf'],'csrf-value')
            with connect(s) as db:
                row=db.execute('SELECT * FROM web_sessions').fetchone()
                self.assertNotEqual(row['id_hash'],'browser-secret')
                self.assertNotEqual(row['credential'],'private-test-token')
            second.revoke_session('browser-secret')
            self.assertIsNone(first.get_session('browser-secret'))
            first.save_session('expired','csrf')
            with connect(s) as db:db.execute('UPDATE web_sessions SET expires=?',(time.time()-1,))
            self.assertIsNone(first.get_session('expired'))
            first.save_session('rotation','csrf')
            rotated=Desk(s,'new-private-token')
            self.assertIsNone(rotated.get_session('rotation'))
