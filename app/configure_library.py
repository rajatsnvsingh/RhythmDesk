"""Administrator-only switch of a pinned deployment to a fresh final library.

This is not an API endpoint or a command available to the deployment SSH key.
It never migrates, deletes, tags, or publishes music.
"""
import argparse
import copy
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time

CONTROL = Path('/etc/rhythm-deploy')
WORK = Path('/var/lib/rhythm-deploy')
LIBRARY_TARGET = '/srv/media/music/rhythm-attic'
STAGING_TARGET = '/srv/media/music/staging'
STATE_TARGET = '/srv/media/music/curator-state'
IMAGE = 'nexus/rhythm-curator:local'


def host_path(value):
    path = PurePosixPath(value)
    if (not path.is_absolute() or len(path.parts) < 4 or
            '..' in path.parts or any(ord(c) < 32 for c in str(value))):
        raise ValueError('Use an absolute, specific directory without traversal')
    return path


def overlaps(a, b):
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def binding(service, target):
    matches = [v for v in service.get('volumes', []) if v.get('target') == target]
    if len(matches) != 1 or matches[0].get('type') != 'bind':
        raise ValueError('Expected exactly one bind mount at ' + target)
    host_path(matches[0]['source'])
    return matches[0]


def service_uid(service):
    match = re.fullmatch(r'([0-9]+):10000', str(service.get('user', '')))
    if not match or int(match[1]) < 1000:
        raise ValueError('Expected non-root numeric service identities in group 10000')
    return int(match[1])


def library_plan(config, library):
    """Change only the two library binds and the displayed host mapping."""
    library = host_path(library)
    services = config.get('services', {})
    if set(services) != {'worker', 'web', 'publisher'} or any(
            s.get('image') != IMAGE for s in services.values()):
        raise ValueError('Unexpected services or image; administrator review required')
    ids = {name: service_uid(service) for name, service in services.items()}
    if len(set(ids.values())) != 3:
        raise ValueError('Worker, UI and publisher must have distinct identities')
    web, worker, publisher = (services[n] for n in ('web', 'worker', 'publisher'))
    old_web = binding(web, LIBRARY_TARGET)
    old_publisher = binding(publisher, LIBRARY_TARGET)
    if (old_web['source'] != old_publisher['source'] or
            not old_web.get('read_only') or old_publisher.get('read_only', False)):
        raise ValueError('Expected a shared library, UI read-only and publisher writable')
    if any(v.get('target') == LIBRARY_TARGET for v in worker.get('volumes', [])):
        raise ValueError('Worker must not have a library mount')
    roots = []
    for target in (STAGING_TARGET, STATE_TARGET):
        web_mount, worker_mount = binding(web, target), binding(worker, target)
        if web_mount['source'] != worker_mount['source']:
            raise ValueError('Worker and UI workspace mappings disagree')
        roots.append(host_path(web_mount['source']))
    old_library = host_path(old_web['source'])
    if any(overlaps(library, root) for root in roots) or (
            library != old_library and overlaps(library, old_library)):
        raise ValueError('Library must not overlap staging, state or the old library')
    result = copy.deepcopy(config)
    for name, readonly in (('web', True), ('publisher', False)):
        mount = binding(result['services'][name], LIBRARY_TARGET)
        mount.update(source=str(library), read_only=readonly)
        mount.setdefault('bind', {})['create_host_path'] = False
    result['services']['web'].setdefault('environment', {})['CURATOR_HOST_LIBRARY'] = str(library)
    return result, str(old_library), ids


def no_symlinks(path):
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('Directory and its ancestors must not be symlinks: ' + str(path))


def fresh_library(path):
    """Existing music requires a separate permission review, never recursive ACL changes."""
    no_symlinks(path)
    if not path.parent.is_dir():
        raise ValueError('The library parent directory must already exist')
    if path.exists():
        if not path.is_dir():
            raise ValueError('Choose a new or empty library; existing contents will not be modified')
        for entry in path.iterdir():
            # A failed plan validation can leave our empty transfer directory.
            if (entry.name != '.curator-publish' or entry.is_symlink() or
                    not entry.is_dir() or list(entry.iterdir())):
                raise ValueError('Choose a new or empty library; existing contents will not be modified')


def prepare_library(path, ui_uid, publisher_uid):
    import grp
    import pwd
    if grp.getgrgid(10000).gr_name != 'rhythm-curation':
        raise ValueError('Group 10000 is not rhythm-curation')
    for uid, name in ((ui_uid, 'rhythm-ui'), (publisher_uid, 'rhythm-publisher')):
        account = pwd.getpwuid(uid)
        if account.pw_name != name or not account.pw_shell.endswith('nologin'):
            raise ValueError('Unexpected or interactive service account')
    if shutil.which('setfacl') is None:
        raise ValueError('Install the ACL tools before switching the library')
    fresh_library(path)
    if not path.exists():
        path.mkdir(mode=0o750)
        os.chown(path, 0, 10000)
    subprocess.run(['setfacl', '-m', f'u:{ui_uid}:r-x,u:{publisher_uid}:rwx', str(path)], check=True)
    transfer = path / '.curator-publish'
    transfer.mkdir(mode=0o700, exist_ok=True)
    os.chown(transfer, publisher_uid, 10000)
    transfer.chmod(0o2700)


def compose(project, filename, *args):
    # Captured output may contain credentials; never print rendered configuration.
    return subprocess.run(['/usr/bin/docker', 'compose', '--project-directory', str(project),
                           '--env-file', str(CONTROL / '.env'), '-f', str(filename), *args],
                          check=True, capture_output=True, text=True)


def compose_literals(value):
    """Protect already-rendered literal dollars from a second Compose interpolation."""
    if isinstance(value, str):
        return value.replace('$', '$$')
    if isinstance(value, list):
        return [compose_literals(item) for item in value]
    if isinstance(value, dict):
        return {key: compose_literals(item) for key, item in value.items()}
    return value


def install_plan(project, config):
    path = CONTROL / 'compose.yaml'
    backup = path.with_name('compose.before-library-' + str(time.time_ns()) + '.yaml')
    shutil.copyfile(path, backup)
    backup.chmod(0o600)
    # Keep the candidate root-only, validate it before touching the active snapshot.
    descriptor, filename = tempfile.mkstemp(prefix='compose.library.', suffix='.yaml', dir=CONTROL)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(compose_literals(config), output, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        rendered = json.loads(compose(project, temporary, 'config', '--format', 'json').stdout)
        if rendered != config:
            raise ValueError('Re-rendered plan differs; protected configuration was not replaced')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Prepare empty library and save the mount plan')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as administrator; the SSH deployment key cannot change mounts')
    os.umask(0o077)
    no_symlinks(args.project)
    project = args.project.resolve(strict=True)
    if project != Path((CONTROL / 'project-path').read_text().strip()).resolve(strict=True):
        parser.error('Project differs from the pinned deployment')
    no_symlinks(CONTROL)
    for filename in ('compose.yaml', '.env', 'project-path'):
        if (CONTROL / filename).is_symlink():
            parser.error('Protected deployment files must not be symlinks')
    library = Path(str(host_path(str(args.library))))
    no_symlinks(library)
    import fcntl
    with (WORK / 'deployment.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = json.loads(compose(project, CONTROL / 'compose.yaml', 'config', '--format', 'json').stdout)
        updated, previous, ids = library_plan(config, str(library))
        print('Current library:', previous)
        print('New library:    ', library)
        print('Staging, state, read-only drawer, authentication and approval rules stay unchanged.')
        print('No test albums or other music will be copied, edited or deleted.')
        if previous == str(library) and config == updated:
            print('This library is already configured. No changes made.')
            return
        fresh_library(library)
        if not args.apply:
            print('Dry run only. Re-run with --apply to prepare the empty library and save the plan.')
            return
        prepare_library(library, ids['web'], ids['publisher'])
        backup = install_plan(project, updated)
        print('Protected configuration backup:', backup)
        print('Mount plan saved. Existing containers still use the old library until normal re-deployment.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        # Do not echo captured Compose output: it can contain the UI token.
        raise SystemExit('Library switch refused or failed: ' + str(error))
