"""Administrator-only fresh storage transition and guarded disposal of legacy test data.

Run the storage switch first, redeploy and verify, then run --purge-test --apply.
Only the fixed curator-test directory is removable. Real music is never migrated.
"""
import argparse
import copy
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess

import configure_library as base

TEST = PurePosixPath('/srv/media/music/curator-test')
STAGING = '/srv/media/music/staging'
STATE = '/var/lib/rhythm-desk'
VOLUME = 'rhythm-desk-state'
LABEL = 'com.rhythmdesk.role'


def mounts(service, target):
    matches = [v for v in service.get('volumes', []) if v.get('target') == target]
    if len(matches) != 1:
        raise ValueError('Expected exactly one mount at ' + target)
    return matches[0]


def is_current(config):
    try:
        services = config['services']
        root = mounts(services['web'], STATE)
        return (root['type'] == 'volume' and root['source'] == VOLUME and
                mounts(services['web'], base.STAGING_TARGET)['source'] == STAGING)
    except (KeyError, ValueError):
        return False


def state_mount(target, readonly=False, subpath=None):
    options = {'nocopy': True}
    if subpath:
        options['subpath'] = subpath
    result = dict(type='volume', source=VOLUME, target=target, volume=options)
    if readonly:
        result['read_only'] = True
    return result


def storage_plan(config):
    services = config.get('services', {})
    if set(services) != {'worker', 'web', 'publisher'} or any(
            s.get('image') != base.IMAGE for s in services.values()):
        raise ValueError('Unexpected services or image')
    ids = {name: base.service_uid(service) for name, service in services.items()}
    if len(set(ids.values())) != 3:
        raise ValueError('Service identities must be distinct')
    if is_current(config):
        validate_current(config)
        return copy.deepcopy(config), ids
    # Deliberately limited to disposal of this known test workspace, not general migration.
    for name in ('worker', 'web'):
        for target, suffix in ((base.STAGING_TARGET, 'staging'), (base.STATE_TARGET, 'state')):
            mount = base.binding(services[name], target)
            if mount['source'] != str(TEST / suffix):
                raise ValueError('Expected the known curator-test workspace; no other data may be reset')
        approval = base.binding(services[name], base.STATE_TARGET + '/approvals')
        if approval['source'] != str(TEST / 'state/approvals') or not approval.get('read_only'):
            raise ValueError('Unexpected approval protection')
    pub = services['publisher']
    for target, source, ro in (
            (base.STAGING_TARGET + '/Curated', TEST / 'staging/Curated', True),
            (base.STATE_TARGET + '/approvals', TEST / 'state/approvals', False),
            (base.STATE_TARGET + '/taxonomy', TEST / 'state/taxonomy', True)):
        mount = base.binding(pub, target)
        if mount['source'] != str(source) or bool(mount.get('read_only')) != ro:
            raise ValueError('Unexpected publisher scope')
    validate_library(services)
    result = copy.deepcopy(config)
    for name in ('worker', 'web'):
        service = result['services'][name]
        service['volumes'] = [v for v in service['volumes'] if not v['target'].startswith(base.STATE_TARGET)]
        mount = mounts(service, base.STAGING_TARGET)
        mount.update(source=STAGING, bind={'create_host_path': False})
        service['volumes'] += [state_mount(STATE), state_mount(STATE + '/approvals', True, 'approvals')]
    pub = result['services']['publisher']
    pub['volumes'] = [v for v in pub['volumes'] if not v['target'].startswith(base.STATE_TARGET)]
    mounts(pub, base.STAGING_TARGET + '/Curated').update(source=STAGING + '/Curated', bind={'create_host_path': False})
    pub['volumes'] += [state_mount(STATE + '/approvals', subpath='approvals'),
                       state_mount(STATE + '/taxonomy', True, 'taxonomy')]
    for service in result['services'].values():
        service.setdefault('environment', {})['CURATOR_STATE_ROOT'] = STATE
    result['services']['web']['environment'].update(CURATOR_HOST_STAGING=STAGING,
        CURATOR_HOST_STATE='Docker volume: ' + VOLUME, CURATOR_STATE_VOLUME=VOLUME)
    result.setdefault('volumes', {})[VOLUME] = {'name': VOLUME, 'external': True}
    validate_current(result)
    return result, ids


def validate_library(services):
    for name, readonly in (('web', True), ('publisher', False)):
        mount = base.binding(services[name], base.LIBRARY_TARGET)
        if mount['source'] != base.LIBRARY_TARGET or bool(mount.get('read_only')) != readonly:
            raise ValueError('Real Rhythm Attic must remain in place with its current protections')
    if any(v['target'] == base.LIBRARY_TARGET for v in services['worker'].get('volumes', [])):
        raise ValueError('Worker must not have a library mount')


def validate_current(config):
    services = config['services']
    if set(services) != {'worker', 'web', 'publisher'} or any(s.get('image') != base.IMAGE for s in services.values()):
        raise ValueError('Unexpected services or image')
    validate_library(services)
    if config.get('volumes', {}).get(VOLUME) != {'name': VOLUME, 'external': True}:
        raise ValueError('Expected the independently managed named volume')
    for name in ('worker', 'web'):
        service = services[name]
        if mounts(service, base.STAGING_TARGET)['source'] != STAGING:
            raise ValueError('Staging is not detached from the test directory')
        root = mounts(service, STATE)
        if root['type'] != 'volume' or root['source'] != VOLUME or root.get('read_only') or root.get('volume', {}).get('subpath'):
            raise ValueError('Unexpected state root mount')
    for name, child, ro in (('worker', 'approvals', True), ('web', 'approvals', True),
                            ('publisher', 'approvals', False), ('publisher', 'taxonomy', True)):
        mount = mounts(services[name], STATE + '/' + child)
        if (mount['type'] != 'volume' or mount['source'] != VOLUME or
                mount.get('volume', {}).get('subpath') != child or bool(mount.get('read_only')) != ro):
            raise ValueError('Unexpected state subdirectory access')
    pub_state = [v for v in services['publisher']['volumes'] if v['target'].startswith(STATE)]
    if len(pub_state) != 2:
        raise ValueError('Publisher must not see the full state volume')
    for service in services.values():
        if service.get('environment', {}).get('CURATOR_STATE_ROOT') != STATE:
            raise ValueError('State root environment does not match its mount')
        if any(v.get('type') == 'bind' and PurePosixPath(v['source']).is_relative_to(TEST)
               for v in service.get('volumes', [])):
            raise ValueError('Test directory remains mounted')


def canonical(config):
    result = base.canonical_plan(config)
    for service in result['services'].values():
        for mount in service.get('volumes', []):
            mount.setdefault('read_only', False)
    return result


def docker(*args, check=True):
    return subprocess.run(['/usr/bin/docker', *args], check=check, capture_output=True, text=True)


def prepare_volume(ids):
    info = docker('volume', 'inspect', VOLUME, check=False)
    if info.returncode:
        docker('volume', 'create', '--label', LABEL + '=state', VOLUME)
        info = docker('volume', 'inspect', VOLUME)
    volume = json.loads(info.stdout)[0]
    if volume['Name'] != VOLUME or volume['Driver'] != 'local' or volume.get('Options') or volume.get('Labels', {}).get(LABEL) != 'state':
        raise ValueError('Refusing an unrelated, redirected or non-local volume')
    data_root = Path(docker('info', '--format', '{{.DockerRootDir}}').stdout.strip()).resolve(strict=True)
    root = Path(volume['Mountpoint'])
    base.no_symlinks(root)
    if root.resolve(strict=True) != data_root / 'volumes' / VOLUME / '_data':
        raise ValueError('Unexpected Docker-managed volume location')
    allowed = {'processed', 'cache', 'logs', 'taxonomy', 'approvals'}
    if any(p.is_symlink() or not p.is_dir() or p.name not in allowed or list(p.iterdir()) for p in root.iterdir()):
        raise ValueError('State volume is not empty; it will never be reset')
    os.chown(root, 0, 10000)
    root.chmod(0o2770)
    for name in allowed:
        child = root / name
        child.mkdir(exist_ok=True)
        os.chown(child, ids['publisher'] if name == 'approvals' else ids['worker'], 10000)
        child.chmod(0o2750 if name == 'approvals' else 0o2770)


def prepare_staging(user):
    import pwd
    uid = pwd.getpwnam(user).pw_uid
    root = Path(STAGING)
    base.no_symlinks(root)
    allowed = {'incoming', 'Curated', 'needs-review'}
    if not root.parent.is_dir() or (root.exists() and any(
            p.is_symlink() or not p.is_dir() or p.name not in allowed or list(p.iterdir()) for p in root.iterdir())):
        raise ValueError('New staging must be absent or empty; existing contents will not be erased')
    root.mkdir(exist_ok=True)
    os.chown(root, 0, 10000)
    root.chmod(0o2770)
    acl = f'u:{uid}:rwx,g:10000:rwx,d:u:{uid}:rwx,d:g:10000:rwx,d:m:rwx,d:o:---'
    subprocess.run(['setfacl', '-m', acl, str(root)], check=True)
    for name in ('incoming', 'Curated', 'needs-review'):
        folder = root / name
        folder.mkdir(exist_ok=True)
        os.chown(folder, 0, 10000)
        folder.chmod(0o2770)


def assert_unused(containers, project_name):
    for container in containers:
        for mount in container.get('Mounts', []):
            source = PurePosixPath(mount.get('Source', '/'))
            own_stack = container.get('Config', {}).get('Labels', {}).get('com.docker.compose.project') == project_name
            if source.is_relative_to(TEST) or (own_stack and TEST.is_relative_to(source) and mount.get('RW')):
                raise ValueError('A container still mounts test data; recreate it before deletion')


def purge_test(config):
    validate_current(config)
    root = Path(str(TEST))
    base.no_symlinks(root)
    if root.resolve() != Path('/srv/media/music/curator-test'):
        raise ValueError('Only the exact curator-test directory can be deleted')
    ids = docker('ps', '-aq').stdout.split()
    if ids:
        assert_unused(json.loads(docker('inspect', *ids).stdout), config.get('name'))
    if root.exists():
        for directory, children, _ in os.walk(root, followlinks=False):
            if os.path.ismount(directory) or any(os.path.ismount(Path(directory) / name) for name in children):
                raise ValueError('Nested filesystem mount found; deletion refused')
        shutil.rmtree(root)  # Does not follow symlinks; Linux uses fd-based protection.
        print('Deleted /srv/media/music/curator-test and all disposable test contents. No undo.')
    else:
        print('Test directory is already absent. No deletion performed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--staging-user', help='Linux account to grant staging access (required for a fresh storage switch)')
    parser.add_argument('--purge-test', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as administrator; the deployment key cannot change storage or delete test data')
    os.umask(0o077)
    base.no_symlinks(base.CONTROL)
    for name in ('compose.yaml', '.env'):
        if (base.CONTROL / name).is_symlink():
            raise ValueError('Protected configuration must not be symlinked')
    project = base.project_directory(args.project)
    import fcntl
    with (base.WORK / 'deployment.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = json.loads(base.compose(project, base.CONTROL / 'compose.yaml', 'config', '--format', 'json').stdout)
        if args.purge_test:
            if not args.apply:
                print('Dry run: after storage verification, --purge-test --apply deletes only', TEST)
            else:
                purge_test(config)
            return
        plan, ids = storage_plan(config)
        print('State: Docker volume', VOLUME, '->', STATE)
        print('Staging:', STAGING)
        print('Real library, source drawer, authentication token and approval protections stay unchanged.')
        if is_current(config):
            print('Storage is already configured. No data was reset.')
            return
        if not args.staging_user:
            parser.error('Pass --staging-user with your Linux account for a fresh storage switch')
        base.install_plan(project, plan, kind='storage', normalizer=canonical, validate_only=True)
        if not args.apply:
            print('Dry run only. --apply stops the stack and prepares fresh storage; test data stays until --purge-test.')
            return
        # All services stop before switching storage. The scoped deploy command restarts them.
        base.compose(project, base.CONTROL / 'compose.yaml', 'stop', 'web', 'worker', 'publisher')
        prepare_volume(ids)
        prepare_staging(args.staging_user)
        backup = base.install_plan(project, plan, kind='storage', normalizer=canonical)
        print('Protected configuration backup:', backup)
        print('Fresh storage saved. Re-deploy now; then verify and run --purge-test --apply.')
        print('Test state/settings/history are not migrated. Sign in again with the existing token.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit('Storage switch refused or failed: ' + str(error) + '. If stopped, re-deploy the current protected plan to restart.')
