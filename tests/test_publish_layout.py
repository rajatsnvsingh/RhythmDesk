import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
import configure_layout as layout
from test_storage_transition import old_config


def current_config():
    config, _ = layout.storage.storage_plan(old_config())
    config['services']['publisher']['volumes'].append(dict(type='volume',
        source='publisher-socket', target='/run/rhythm-publisher'))
    return config


class PublishLayoutTests(unittest.TestCase):
    def test_plan_preserves_state_token_and_approval_boundary(self):
        original = current_config()
        before = copy.deepcopy(original)
        plan, current = layout.layout_plan(original)
        self.assertFalse(current)
        self.assertEqual(original, before)
        web = plan['services']['web']
        self.assertEqual(web['environment']['CURATOR_UI_TOKEN'], before['services']['web']['environment']['CURATOR_UI_TOKEN'])
        self.assertEqual(web['ports'], before['services']['web']['ports'])
        pub = plan['services']['publisher']
        self.assertEqual(layout.base.binding(pub, layout.base.LIBRARY_TARGET)['source'], layout.ATTIC.as_posix())
        self.assertTrue(layout.base.binding(pub, layout.base.LIBRARY_TARGET + '/staging')['read_only'])
        self.assertEqual(pub['environment']['CURATOR_LIBRARY_ROOT'], layout.LIBRARY)
        self.assertEqual(pub['environment']['CURATOR_PUBLISH_ROOT'], layout.PUBLISH)
        for name in ('worker', 'web', 'publisher'):
            state = lambda c: [v for v in c['services'][name]['volumes'] if v['target'].startswith(layout.storage.STATE)]
            self.assertEqual(state(plan), state(before))
        self.assertEqual(layout.layout_plan(plan), (plan, True))

    def test_scope_widening_and_separate_transfer_mount_refused(self):
        plan, _ = layout.layout_plan(current_config())
        for mutation in ('staging_rw', 'extra_mount', 'transfer_submount', 'worker_library'):
            bad = copy.deepcopy(plan)
            if mutation == 'staging_rw':
                layout.base.binding(bad['services']['publisher'], layout.base.LIBRARY_TARGET + '/staging')['read_only'] = False
            else:
                service = 'worker' if mutation == 'worker_library' else 'publisher'
                target = layout.PUBLISH if mutation == 'transfer_submount' else '/unrelated'
                if mutation == 'worker_library': target = layout.base.LIBRARY_TARGET
                bad['services'][service]['volumes'].append(dict(type='bind', source='/srv/other/files', target=target))
            with self.assertRaises(ValueError): layout.validate_layout(bad)

    def test_migration_moves_without_editing_and_rolls_back_on_plan_failure(self):
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                attic, staging, work = root / 'attic', root / 'staging', root / 'control'
                album = attic / 'Artist/Album'
                album.mkdir(parents=True)
                (album / '01.mp3').write_bytes(b'original-audio')
                (attic / '.curator-publish').mkdir()
                (staging / 'Curated').mkdir(parents=True)
                (staging / 'incoming').mkdir()
                (staging / 'incoming/original.mp3').write_bytes(b'original-intake')
                work.mkdir()
                plan, _ = layout.layout_plan(current_config())
                command = types.SimpleNamespace(stdout='user::rwx\ngroup::r-x\nother::---\n')
                with patch.object(layout, 'ATTIC', attic), patch.object(layout, 'OLD_STAGING', staging), \
                     patch.object(layout.base, 'WORK', work), patch.object(layout.os, 'chown', create=True), \
                     patch.object(layout.subprocess, 'run', return_value=command), \
                     patch.object(layout.base, 'install_plan', side_effect=ValueError('validation failed') if failure else None):
                    entries = layout.preflight()
                    if failure:
                        with self.assertRaises(ValueError): layout.migrate(root, plan, entries, 1000)
                        self.assertEqual((album / '01.mp3').read_bytes(), b'original-audio')
                        self.assertTrue((staging / 'incoming/original.mp3').exists())
                        self.assertFalse((attic / 'library').exists())
                        self.assertTrue((attic / '.curator-publish').is_dir())
                    else:
                        layout.migrate(root, plan, entries, 1000)
                        self.assertEqual((attic / 'library/Artist/Album/01.mp3').read_bytes(), b'original-audio')
                        self.assertEqual((attic / 'staging/incoming/original.mp3').read_bytes(), b'original-intake')
                        self.assertFalse((attic / '.curator-publish').exists())
                        self.assertTrue((attic / 'publish-staging').is_dir())
                        self.assertFalse(staging.exists())
                journal = json.loads(next(work.glob('layout-migration-*.json')).read_text())
                self.assertEqual(journal['status'], 'rolled-back' if failure else 'committed')

    def test_reserved_destination_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            attic, staging = root / 'attic', root / 'staging'
            (attic / 'library').mkdir(parents=True)
            (staging / 'Curated').mkdir(parents=True)
            with patch.object(layout, 'ATTIC', attic), patch.object(layout, 'OLD_STAGING', staging):
                with self.assertRaises(ValueError): layout.preflight()
