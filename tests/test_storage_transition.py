import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
import configure_storage as storage
from test_library_switch import configuration


def old_config():
    config = configuration()
    for name in ('web', 'worker'):
        config['services'][name]['volumes'].append(dict(type='bind',
            source=str(storage.TEST / 'state/approvals'), target=storage.base.STATE_TARGET + '/approvals', read_only=True))
    config['services']['publisher']['volumes'].append(dict(type='bind',
        source=str(storage.TEST / 'state/taxonomy'), target=storage.base.STATE_TARGET + '/taxonomy', read_only=True))
    for name in ('web', 'publisher'):
        storage.base.binding(config['services'][name], storage.base.LIBRARY_TARGET)['source'] = storage.base.LIBRARY_TARGET
    return config


class StorageTransitionTests(unittest.TestCase):
    def test_fresh_named_state_preserves_library_auth_ports_source_and_identities(self):
        before = old_config()
        original = copy.deepcopy(before)
        plan, ids = storage.storage_plan(before)
        self.assertEqual(before, original)
        self.assertEqual(ids, {'worker': 10001, 'web': 10002, 'publisher': 10003})
        self.assertEqual(plan['services']['web']['ports'], before['services']['web']['ports'])
        self.assertEqual(plan['services']['web']['environment']['CURATOR_UI_TOKEN'], 'synthetic-$token')
        for name in ('web', 'publisher'):
            self.assertEqual(storage.base.binding(plan['services'][name], storage.base.LIBRARY_TARGET),
                             storage.base.binding(before['services'][name], storage.base.LIBRARY_TARGET))
        self.assertEqual(storage.mounts(plan['services']['web'], '/mnt/music-source'),
                         storage.mounts(before['services']['web'], '/mnt/music-source'))
        storage.validate_current(plan)
        self.assertEqual(storage.storage_plan(plan)[0], plan)

    def test_publisher_only_gets_approval_and_readonly_taxonomy_subpaths(self):
        plan, _ = storage.storage_plan(old_config())
        pub = plan['services']['publisher']
        state = [v for v in pub['volumes'] if v['source'] == storage.VOLUME]
        self.assertEqual(len(state), 2)
        self.assertEqual({v['volume']['subpath'] for v in state}, {'approvals', 'taxonomy'})
        self.assertFalse(storage.mounts(pub, storage.STATE + '/approvals').get('read_only', False))
        self.assertTrue(storage.mounts(pub, storage.STATE + '/taxonomy')['read_only'])
        for name in ('worker', 'web'):
            self.assertTrue(storage.mounts(plan['services'][name], storage.STATE + '/approvals')['read_only'])
        self.assertTrue(plan['volumes'][storage.VOLUME]['external'])

    def test_library_switch_remains_compatible_with_named_state(self):
        plan, _ = storage.storage_plan(old_config())
        updated, old, _ = storage.base.library_plan(plan, '/srv/media/music/new-empty-library')
        self.assertEqual(old, '/srv/media/music/rhythm-attic')
        self.assertEqual(storage.mounts(updated['services']['web'], storage.STATE),
                         storage.mounts(plan['services']['web'], storage.STATE))

    def test_unknown_workspace_or_changed_library_cannot_be_reset(self):
        for fault in ('staging', 'state', 'library', 'approvals', 'publisher', 'identity'):
            config = old_config()
            if fault in ('staging', 'state'):
                target = storage.base.STAGING_TARGET if fault == 'staging' else storage.base.STATE_TARGET
                storage.mounts(config['services']['web'], target)['source'] = '/srv/media/music/other'
            elif fault == 'library':
                storage.mounts(config['services']['publisher'], storage.base.LIBRARY_TARGET)['source'] += '-other'
            elif fault == 'approvals':
                storage.mounts(config['services']['worker'], storage.base.STATE_TARGET + '/approvals')['read_only'] = False
            elif fault == 'publisher':
                storage.mounts(config['services']['publisher'], storage.base.STATE_TARGET + '/taxonomy')['read_only'] = False
            else:
                config['services']['web']['user'] = '0:10000'
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                storage.storage_plan(config)

    def test_unsafe_current_scope_blocks_cleanup_before_deletion(self):
        for fault in ('full-publisher', 'taxonomy-write', 'worker-library', 'test-mount', 'managed-volume'):
            plan, _ = storage.storage_plan(old_config())
            if fault == 'full-publisher':
                plan['services']['publisher']['volumes'].append(storage.state_mount(storage.STATE))
            elif fault == 'taxonomy-write':
                storage.mounts(plan['services']['publisher'], storage.STATE + '/taxonomy')['read_only'] = False
            elif fault == 'worker-library':
                plan['services']['worker']['volumes'].append(copy.deepcopy(storage.mounts(plan['services']['web'], storage.base.LIBRARY_TARGET)))
            elif fault == 'test-mount':
                plan['services']['worker']['volumes'].append(dict(type='bind', source=str(storage.TEST / 'state'), target='/unexpected'))
            else:
                plan['volumes'][storage.VOLUME]['external'] = False
            with self.subTest(fault=fault), patch.object(storage.shutil, 'rmtree') as delete, self.assertRaises(ValueError):
                storage.purge_test(plan)
                delete.assert_not_called()

    def test_direct_test_mounts_are_refused_even_in_stopped_or_other_containers(self):
        for source in (str(storage.TEST), str(storage.TEST / 'state'), str(storage.TEST / 'staging/incoming')):
            with self.subTest(source=source), self.assertRaises(ValueError):
                storage.assert_unused([{'Mounts': [{'Source': source, 'RW': False}]}], 'rhythm-curator')
        storage.assert_unused([{'Mounts': [{'Source': '/srv/media', 'RW': True}]}], 'rhythm-curator')
        own = {'Config': {'Labels': {'com.docker.compose.project': 'rhythm-curator'}},
               'Mounts': [{'Source': '/srv/media/music', 'RW': False}]}
        storage.assert_unused([own], 'rhythm-curator')
        own['Mounts'][0]['RW'] = True
        with self.assertRaises(ValueError):
            storage.assert_unused([own], 'rhythm-curator')

    def test_volume_initialization_is_group_writable_and_preserves_receipt_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'volumes' / storage.VOLUME / '_data'
            root.mkdir(parents=True)
            volume = dict(Name=storage.VOLUME, Driver='local', Options=None,
                          Labels={storage.LABEL: 'state'}, Mountpoint=str(root))
            outputs = [types.SimpleNamespace(returncode=0, stdout=json.dumps([volume])),
                       types.SimpleNamespace(stdout=folder)]
            with patch.object(storage, 'docker', side_effect=outputs), \
                    patch.object(storage.os, 'chown', create=True) as owner:
                storage.prepare_volume({'worker': 10001, 'web': 10002, 'publisher': 10003})
            owner.assert_any_call(root / 'approvals', 10003, 10000)
            owner.assert_any_call(root / 'taxonomy', 10001, 10000)
            self.assertEqual({p.name for p in root.iterdir()}, {'approvals', 'taxonomy', 'processed', 'cache', 'logs'})

    def test_existing_state_or_redirected_volume_is_never_overwritten(self):
        for fault in ('files', 'labels', 'options', 'driver', 'location'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as folder:
                root = Path(folder) / 'volumes' / storage.VOLUME / '_data'
                root.mkdir(parents=True)
                volume = dict(Name=storage.VOLUME, Driver='local', Options=None,
                              Labels={storage.LABEL: 'state'}, Mountpoint=str(root))
                if fault == 'files': (root / 'curator.sqlite3').write_bytes(b'keep')
                elif fault == 'labels': volume['Labels'] = {}
                elif fault == 'options': volume['Options'] = {'device': '/srv/media/music'}
                elif fault == 'driver': volume['Driver'] = 'other'
                else: volume['Mountpoint'] = folder
                outputs = [types.SimpleNamespace(returncode=0, stdout=json.dumps([volume])),
                           types.SimpleNamespace(stdout=folder)]
                with patch.object(storage, 'docker', side_effect=outputs), \
                        patch.object(storage.os, 'chown', create=True) as owner:
                    with self.assertRaises(ValueError):
                        storage.prepare_volume({'worker': 10001, 'web': 10002, 'publisher': 10003})
                    owner.assert_not_called()
                if fault == 'files': self.assertEqual((root / 'curator.sqlite3').read_bytes(), b'keep')

    def test_sample_compose_has_no_host_state_bind_and_external_state(self):
        import yaml
        config = yaml.safe_load((Path(__file__).parents[1] / 'compose.yaml').read_text())
        self.assertTrue(config['volumes']['state']['external'])
        for service in config['services'].values():
            self.assertEqual(service['environment']['CURATOR_STATE_ROOT'], storage.STATE)
            self.assertNotIn('STATE_PATH', json.dumps(service['volumes']))
        pub = config['services']['publisher']['volumes']
        self.assertEqual([v['volume']['subpath'] for v in pub if isinstance(v, dict)], ['approvals', 'taxonomy'])
