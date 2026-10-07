import sys
import os
import stat
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from common import Settings
from configuration import describe, runtime, save_runtime, save_paths, export_paths
from worker import tick


class ConfigurationTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX group permission bits')
    def test_database_and_wal_are_group_writable(self):
        from common import connect
        self.assertEqual(stat.S_IMODE(self.settings.db.stat().st_mode), 0o660)
        with connect(self.settings) as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES('permission_test','ok')")
            for suffix in ('-wal', '-shm'):
                mode = stat.S_IMODE(Path(str(self.settings.db) + suffix).stat().st_mode)
                self.assertEqual(mode, 0o660)

    def test_connection_precreation_is_exclusive_and_group_writable(self):
        from common import connect
        with patch('common.os.open', wraps=os.open) as opened:
            with connect(self.settings) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
            opened.assert_called_once_with(self.settings.db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o660)

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

    def test_named_state_volume_cannot_be_redirected_from_settings(self):
        paths = dict(STAGING_PATH='/srv/music/staging', STATE_PATH=str(self.settings.state), LIBRARY_PATH='/srv/music/attic')
        with patch.dict('os.environ', {'CURATOR_STATE_VOLUME': 'rhythm-desk-state',
                                     'CURATOR_HOST_STATE': 'Docker volume: rhythm-desk-state'}):
            save_paths(self.settings, paths)
            mount = next(m for m in describe(self.settings)['mounts'] if m['key'] == 'STATE_PATH')
            self.assertEqual(mount['volume'], 'rhythm-desk-state')
            self.assertIn("STATE_VOLUME='rhythm-desk-state'", export_paths(self.settings))
            self.assertNotIn('STATE_PATH=', export_paths(self.settings))
            with self.assertRaises(ValueError):
                save_paths(self.settings, paths | {'STATE_PATH': '/srv/media/music/state'})

    def test_explicit_state_root_is_outside_media_and_survives_reinitialization(self):
        state = Path(self.temp.name) / 'docker-state'
        with patch.dict('os.environ', {'CURATOR_STATE_ROOT': str(state)}):
            settings = Settings(Path(self.temp.name) / 'media')
            settings.initialize()
            save_runtime(settings, dict(stable_seconds=0, scan_interval=30, automatic_grouping=False, paused=True))
            again = Settings(Path(self.temp.name) / 'media')
            again.initialize()
            self.assertEqual(again.state, state.resolve())
            self.assertTrue(runtime(again)['paused'])
