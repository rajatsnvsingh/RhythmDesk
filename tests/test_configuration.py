import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from common import Settings
from configuration import describe, runtime, save_runtime, save_paths, export_paths
from worker import tick


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(self.temp.name)
        self.settings.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_runtime_persists_and_pause_prevents_work(self):
        values = dict(stable_seconds=42, scan_interval=20, automatic_grouping=False, paused=True)
        save_runtime(self.settings, values)
        self.assertEqual(runtime(Settings(self.temp.name)), values)
        with patch('worker.scan') as scan:
            tick(self.settings)
            scan.assert_not_called()
        values['paused'] = False
        values['stable_seconds'] = 0
        save_runtime(self.settings, values)
        from common import connect
        with connect(self.settings) as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES('scan_requested','true')")
        with patch('worker.scan') as scan, patch('worker.group_tagged') as group:
            tick(self.settings)
            scan.assert_called_once()
            group.assert_not_called()
        self.assertEqual(self.settings.stable_seconds, 0)

    def test_validation_and_directory_plan_cannot_redirect_library(self):
        paths = dict(STAGING_PATH='/srv/music/staging', STATE_PATH='/srv/music/state', LIBRARY_PATH='/srv/music/attic')
        current = self.settings.library
        save_paths(self.settings, paths)
        self.assertEqual(describe(self.settings)['plan'], paths)
        self.assertEqual(self.settings.library, current)
        self.assertIn("LIBRARY_PATH='/srv/music/attic'", export_paths(self.settings))
        for value in ['/srv', '/srv/music/staging/sub', '/srv/music/$evil', '/srv/music/../attic', '/srv/music/x\nBAD=y']:
            with self.assertRaises(ValueError):
                save_paths(self.settings, paths | {'LIBRARY_PATH': value})
        for values in [runtime(self.settings) | {'scan_interval': 0}, runtime(self.settings) | {'paused': 'yes'}, runtime(self.settings) | {'automatic_publish': True}]:
            with self.assertRaises(ValueError):
                save_runtime(self.settings, values)
