import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
import configure_library as switch


def configuration():
    def bind(source, target, readonly=False):
        return dict(type='bind', source=source, target=target, read_only=readonly)
    staging = '/srv/media/music/curator-test/staging'
    state = '/srv/media/music/curator-test/state'
    library = '/srv/media/music/curator-test/rhythm-attic'
    workspace = [bind(staging, switch.STAGING_TARGET), bind(state, switch.STATE_TARGET)]
    services = {
        'worker': dict(user='10001:10000', volumes=copy.deepcopy(workspace)),
        'web': dict(user='10002:10000', ports=['10.0.0.136:8765:8765'],
                    environment={'CURATOR_UI_TOKEN': 'synthetic-$token',
                                 'CURATOR_HOST_LIBRARY': library, 'CURATOR_HOST_STATE': state},
                    volumes=copy.deepcopy(workspace) + [
                        bind(library, switch.LIBRARY_TARGET, True),
                        bind('/srv/media/music', '/mnt/music-source', True)]),
        'publisher': dict(user='10003:10000', network_mode='none', volumes=[
            bind(staging + '/Curated', switch.STAGING_TARGET + '/Curated', True),
            bind(state + '/approvals', switch.STATE_TARGET + '/approvals'),
            bind(library, switch.LIBRARY_TARGET)]),
    }
    for service in services.values():
        service.update(image=switch.IMAGE, read_only=True, cap_drop=['ALL'])
    return dict(name='rhythm-curator', services=services, volumes={'publisher-socket': {'driver': 'local'}})


class LibrarySwitchTests(unittest.TestCase):
    def test_explicit_administrator_project_works_without_optional_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            control = Path(folder) / 'control'
            project = Path(folder) / 'project'
            control.mkdir()
            (project / 'app').mkdir(parents=True)
            for name in ('server.py', 'worker.py', 'publisher.py'):
                (project / 'app' / name).write_text('# fixture\n')
            with patch.object(switch, 'CONTROL', control):
                self.assertEqual(switch.project_directory(project), project.resolve())
            self.assertFalse((control / 'project-path').exists())

    def test_existing_project_metadata_still_requires_an_exact_match(self):
        with tempfile.TemporaryDirectory() as folder:
            control = Path(folder)
            project = control / 'project'
            other = control / 'other'
            project.mkdir()
            other.mkdir()
            (control / 'project-path').write_text(str(project))
            with patch.object(switch, 'CONTROL', control):
                self.assertEqual(switch.project_directory(project), project.resolve())
                with self.assertRaises(ValueError):
                    switch.project_directory(other)

    def test_missing_metadata_does_not_accept_unrelated_or_incomplete_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / 'project'
            (project / 'app').mkdir(parents=True)
            with patch.object(switch, 'CONTROL', Path(folder)):
                with self.assertRaises(ValueError):
                    switch.project_directory(project)
                (project / 'app' / 'server.py').write_text('# fixture\n')
                with self.assertRaises(ValueError):
                    switch.project_directory(project)

    def test_symlinked_project_metadata_is_not_treated_as_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            metadata = project / 'project-path'
            with patch.object(switch, 'CONTROL', project), \
                    patch.object(Path, 'is_symlink', lambda p: p == metadata):
                with self.assertRaises(ValueError):
                    switch.project_directory(project)

    def test_only_library_mounts_and_display_mapping_change(self):
        before = configuration()
        original = copy.deepcopy(before)
        updated, old, ids = switch.library_plan(before, '/srv/media/music/rhythm-attic')
        self.assertEqual(before, original)
        self.assertEqual(old, '/srv/media/music/curator-test/rhythm-attic')
        self.assertEqual(ids, {'worker': 10001, 'web': 10002, 'publisher': 10003})
        expected = copy.deepcopy(before)
        for name, ro in (('web', True), ('publisher', False)):
            mount = switch.binding(expected['services'][name], switch.LIBRARY_TARGET)
            mount.update(source='/srv/media/music/rhythm-attic', read_only=ro,
                         bind={'create_host_path': False})
        expected['services']['web']['environment']['CURATOR_HOST_LIBRARY'] = '/srv/media/music/rhythm-attic'
        self.assertEqual(updated, expected)
        self.assertEqual(switch.library_plan(updated, '/srv/media/music/rhythm-attic')[0], updated)

    def test_overlap_or_traversal_refused(self):
        for library in ('/', '/srv/media', 'relative/library', '/srv/media/music/../state',
                        '/srv/media/music/curator-test/staging/new',
                        '/srv/media/music/curator-test/state',
                        '/srv/media/music/curator-test',
                        '/srv/media/music/curator-test/rhythm-attic/nested'):
            with self.subTest(library=library), self.assertRaises(ValueError):
                switch.library_plan(configuration(), library)

    def test_broken_service_isolation_refused(self):
        for fault in ('web-write', 'publisher-readonly', 'different-library', 'worker-library',
                      'root', 'same-uid', 'wrong-group', 'workspace-mismatch', 'duplicate', 'image', 'service'):
            config = configuration()
            services = config['services']
            if fault == 'web-write':
                switch.binding(services['web'], switch.LIBRARY_TARGET)['read_only'] = False
            elif fault == 'publisher-readonly':
                switch.binding(services['publisher'], switch.LIBRARY_TARGET)['read_only'] = True
            elif fault == 'different-library':
                switch.binding(services['publisher'], switch.LIBRARY_TARGET)['source'] += '-other'
            elif fault == 'worker-library':
                services['worker']['volumes'].append(copy.deepcopy(switch.binding(services['web'], switch.LIBRARY_TARGET)))
            elif fault in ('root', 'same-uid', 'wrong-group'):
                services['web']['user'] = {'root': '0:10000', 'same-uid': '10001:10000', 'wrong-group': '10002:0'}[fault]
            elif fault == 'workspace-mismatch':
                switch.binding(services['worker'], switch.STATE_TARGET)['source'] += '-other'
            elif fault == 'duplicate':
                services['web']['volumes'].append(copy.deepcopy(switch.binding(services['web'], switch.LIBRARY_TARGET)))
            elif fault == 'image':
                services['worker']['image'] = 'other:image'
            else:
                services['extra'] = copy.deepcopy(services['web'])
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                switch.library_plan(config, '/srv/media/music/rhythm-attic')

    def test_existing_music_is_not_touched(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Path(folder) / 'rhythm-attic'
            switch.fresh_library(library)
            self.assertFalse(library.exists())
            library.mkdir()
            switch.fresh_library(library)
            track = library / 'existing.mp3'
            track.write_bytes(b'original')
            with self.assertRaises(ValueError):
                switch.fresh_library(library)
            self.assertEqual(track.read_bytes(), b'original')

    def test_empty_transfer_directory_allows_safe_retry_but_files_do_not(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Path(folder) / 'rhythm-attic'
            transfer = library / '.curator-publish'
            transfer.mkdir(parents=True)
            switch.fresh_library(library)
            (transfer / 'unfinished.mp3').write_bytes(b'original')
            with self.assertRaises(ValueError):
                switch.fresh_library(library)

    def test_symlink_in_ancestor_is_refused(self):
        library = Path('/srv/media/music/rhythm-attic')
        with patch.object(Path, 'is_symlink', lambda p: p == library.parent):
            with self.assertRaises(ValueError):
                switch.no_symlinks(library)

    def test_rendered_dollars_remain_literal_when_saved(self):
        escaped = switch.compose_literals({'environment': {'TOKEN': '$thing-$$'}, 'command': ['$HOME'], 'read_only': True})
        self.assertEqual(escaped, {'environment': {'TOKEN': '$$thing-$$$$'}, 'command': ['$$HOME'], 'read_only': True})

    def test_failed_validation_keeps_active_config_and_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            control = Path(folder)
            active = control / 'compose.yaml'
            active.write_text('previous protected configuration')
            with patch.object(switch, 'CONTROL', control), patch.object(switch, 'compose', side_effect=ValueError('invalid plan')):
                with self.assertRaises(ValueError):
                    switch.install_plan(Path(folder), configuration())
            self.assertEqual(active.read_text(), 'previous protected configuration')
            self.assertEqual(len(list(control.glob('compose.before-library-*.yaml'))), 1)
            self.assertEqual(list(control.glob('compose.library.*.yaml')), [])

    def test_successful_install_has_recoverable_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            control = Path(folder)
            active = control / 'compose.yaml'
            active.write_text('previous protected configuration')
            with patch.object(switch, 'CONTROL', control), patch.object(
                    switch, 'compose', return_value=types.SimpleNamespace(stdout=json.dumps(configuration()))) as validate:
                backup = switch.install_plan(Path(folder), configuration())
            self.assertEqual(backup.read_text(), 'previous protected configuration')
            self.assertEqual(json.loads(active.read_text()), switch.compose_literals(configuration()))
            self.assertEqual(validate.call_args.args[-3:], ('config', '--format', 'json'))
            self.assertEqual(list(control.glob('compose.library.*.yaml')), [])

    def test_second_interpolation_or_other_config_change_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            control = Path(folder)
            active = control / 'compose.yaml'
            active.write_text('previous protected configuration')
            altered = configuration()
            altered['services']['web']['environment']['CURATOR_UI_TOKEN'] = 'changed'
            with patch.object(switch, 'CONTROL', control), patch.object(
                    switch, 'compose', return_value=types.SimpleNamespace(stdout=json.dumps(altered))):
                with self.assertRaises(ValueError):
                    switch.install_plan(Path(folder), configuration())
            self.assertEqual(active.read_text(), 'previous protected configuration')

    def test_compose_may_omit_false_library_flags_without_changing_the_plan(self):
        expected, _, _ = switch.library_plan(configuration(), '/srv/media/music/rhythm-attic')
        rendered = copy.deepcopy(expected)
        switch.binding(rendered['services']['publisher'], switch.LIBRARY_TARGET).pop('read_only')
        for name in ('web', 'publisher'):
            switch.binding(rendered['services'][name], switch.LIBRARY_TARGET).pop('bind')
        self.assertEqual(switch.canonical_plan(rendered), switch.canonical_plan(expected))
        switch.binding(rendered['services']['publisher'], switch.LIBRARY_TARGET)['read_only'] = True
        self.assertNotEqual(switch.canonical_plan(rendered), switch.canonical_plan(expected))

    def test_main_dry_run_never_creates_library_or_installs_plan(self):
        for has_metadata in (True, False):
            with self.subTest(has_metadata=has_metadata):
                self.check_dry_run(has_metadata)

    def check_dry_run(self, has_metadata):
        with tempfile.TemporaryDirectory() as folder:
            control = Path(folder)
            if has_metadata:
                (control / 'project-path').write_text(folder)
            else:
                (control / 'app').mkdir()
                for name in ('server.py', 'worker.py', 'publisher.py'):
                    (control / 'app' / name).write_text('# fixture\n')
            fcntl = types.SimpleNamespace(flock=lambda *args: None, LOCK_EX=1, LOCK_NB=2)
            plan = switch.library_plan(configuration(), '/srv/media/music/rhythm-attic')
            with patch.object(switch, 'CONTROL', control), patch.object(switch, 'WORK', control), \
                    patch.object(switch.os, 'geteuid', return_value=0, create=True), \
                    patch.object(switch.os, 'umask'), patch.dict(sys.modules, {'fcntl': fcntl}), \
                    patch.object(switch.argparse.ArgumentParser, 'parse_args', return_value=types.SimpleNamespace(
                        project=control, library='/srv/media/music/rhythm-attic', apply=False)), \
                    patch.object(switch, 'library_plan', return_value=plan), \
                    patch.object(switch, 'compose', return_value=types.SimpleNamespace(stdout=json.dumps(configuration()))), \
                    patch.object(switch, 'fresh_library'), patch.object(switch, 'prepare_library') as prepare, \
                    patch.object(switch, 'install_plan') as install:
                switch.main()
                prepare.assert_not_called()
                install.assert_not_called()

    def test_main_rejects_non_administrator_before_any_docker_call(self):
        with patch.object(switch.os, 'geteuid', return_value=10001, create=True), \
                patch.object(sys, 'argv', ['configure_library.py', '--project', '/home/raj/apps/rhythm-curator',
                                         '--library', '/srv/media/music/rhythm-attic', '--apply']), \
                patch.object(switch, 'compose') as docker:
            with self.assertRaises(SystemExit):
                switch.main()
            docker.assert_not_called()
