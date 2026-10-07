"""Administrator-only, reversible migration to the dedicated three-folder attic.

Limited to the known flat production library and detached staging. No tagging,
publication, deletion, or copying across filesystems is performed.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import time

import configure_library as base
import configure_storage as storage
from common import atomic_json
from publisher import rename_no_replace

ATTIC = Path('/srv/media/music/rhythm-attic')
OLD_STAGING = Path('/srv/media/music/staging')
PUBLISH = base.LIBRARY_TARGET + '/publish-staging'
LIBRARY = base.LIBRARY_TARGET + '/library'


def layout_plan(config):
    services = config.get('services', {})
    if set(services) != {'worker', 'web', 'publisher'}:
        raise ValueError('Unexpected services')
    if services['publisher'].get('environment', {}).get('CURATOR_LIBRARY_ROOT') == LIBRARY:
        validate_layout(config)
        return copy.deepcopy(config), True
    storage.validate_current(config)
    result = copy.deepcopy(config)
    for name in ('worker', 'web'):
        base.binding(result['services'][name], base.STAGING_TARGET).update(
            source=(ATTIC / 'staging').as_posix(), bind={'create_host_path': False})
    web = result['services']['web']
    base.binding(web, base.LIBRARY_TARGET).update(source=(ATTIC / 'library').as_posix())
    web['environment'].update(CURATOR_HOST_STAGING=(ATTIC / 'staging').as_posix(),
        CURATOR_HOST_LIBRARY=(ATTIC / 'library').as_posix(), CURATOR_HOST_ATTIC=ATTIC.as_posix(),
        CURATOR_HOST_PUBLISH=(ATTIC / 'publish-staging').as_posix())
    pub = result['services']['publisher']
    base.binding(pub, base.STAGING_TARGET + '/Curated').update(
        source=(ATTIC / 'staging/Curated').as_posix(), bind={'create_host_path': False})
    # The existing writable library mount becomes the dedicated parent mount.
    # No broader media root is exposed. Parent permissions are read/traverse only.
    pub['environment'].update(CURATOR_LIBRARY_ROOT=LIBRARY, CURATOR_PUBLISH_ROOT=PUBLISH)
    pub['volumes'].append(dict(type='bind', source=(ATTIC / 'staging').as_posix(),
        target=base.LIBRARY_TARGET + '/staging', read_only=True,
        bind={'create_host_path': False}))
    validate_layout(result)
    return result, False


def validate_layout(config):
    services = config['services']
    if set(services) != {'worker', 'web', 'publisher'} or any(
            service.get('image') != base.IMAGE for service in services.values()):
        raise ValueError('Unexpected service image or identities')
    ids = [base.service_uid(service) for service in services.values()]
    if len(set(ids)) != 3:
        raise ValueError('Service identities must remain distinct')
    for name in ('worker', 'web'):
        mount = base.binding(services[name], base.STAGING_TARGET)
        if mount['source'] != (ATTIC / 'staging').as_posix() or mount.get('read_only'):
            raise ValueError('Unexpected staging scope')
    library = base.binding(services['web'], base.LIBRARY_TARGET)
    if library['source'] != (ATTIC / 'library').as_posix() or not library.get('read_only'):
        raise ValueError('UI library must be read-only and point to library child')
    if any(v['target'].startswith(base.LIBRARY_TARGET) for v in services['worker']['volumes']):
        raise ValueError('Worker must not receive the attic or library')
    pub = services['publisher']
    parent = base.binding(pub, base.LIBRARY_TARGET)
    overlay = base.binding(pub, base.LIBRARY_TARGET + '/staging')
    curated = base.binding(pub, base.STAGING_TARGET + '/Curated')
    if curated['source'] != (ATTIC / 'staging/Curated').as_posix() or not curated.get('read_only'):
        raise ValueError('Publisher Curated input must remain read-only')
    allowed = {base.LIBRARY_TARGET, base.LIBRARY_TARGET + '/staging',
        base.STAGING_TARGET + '/Curated', storage.STATE + '/approvals',
        storage.STATE + '/taxonomy', '/run/rhythm-publisher'}
    if len(pub['volumes']) != len(allowed) or {v['target'] for v in pub['volumes']} != allowed:
        raise ValueError('Unexpected additional publisher mounts')
    if parent['source'] != ATTIC.as_posix() or parent.get('read_only'):
        raise ValueError('Publisher requires the dedicated parent mount')
    if overlay['source'] != (ATTIC / 'staging').as_posix() or not overlay.get('read_only'):
        raise ValueError('Publisher staging overlay must remain read-only')
    if pub['environment'].get('CURATOR_LIBRARY_ROOT') != LIBRARY or pub['environment'].get('CURATOR_PUBLISH_ROOT') != PUBLISH:
        raise ValueError('Publisher paths do not match the shared mount')
    # Forbid a separate library/transfer submount: same physical disk alone is
    # insufficient for rename across Docker bind mount boundaries.
    if any(v['target'] in (LIBRARY, PUBLISH) for v in pub['volumes']):
        raise ValueError('Library and publish-staging must share one mount')
    # Reuse existing named-state isolation validation with only the layout delta
    # normalized back to the known previous layout.
    old = copy.deepcopy(config)
    for name in ('worker', 'web'):
        base.binding(old['services'][name], base.STAGING_TARGET)['source'] = OLD_STAGING.as_posix()
    base.binding(old['services']['web'], base.LIBRARY_TARGET)['source'] = ATTIC.as_posix()
    storage.validate_current(old)


def preflight():
    for root in (ATTIC, OLD_STAGING):
        base.no_symlinks(root)
        if not root.is_dir() or root.is_mount():
            raise ValueError('Expected ordinary existing directories: ' + str(root))
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                path = Path(directory) / name
                if path.is_symlink() or path.is_mount():
                    raise ValueError('Symlink or nested mount requires administrator review: ' + str(path))
    if ATTIC.stat().st_dev != OLD_STAGING.stat().st_dev:
        raise ValueError('Staging migration must stay on the same filesystem')
    entries = list(ATTIC.iterdir())
    if any(p.name in {'library', 'staging', 'publish-staging'} for p in entries):
        raise ValueError('Reserved destination name already exists; nothing will be overwritten')
    if any(not p.is_dir() or (p.name.startswith('.') and p.name != '.curator-publish') for p in entries):
        raise ValueError('Flat library must contain artist directories and optional legacy transfer directory only')
    if not (OLD_STAGING / 'Curated').is_dir():
        raise ValueError('Expected the existing staging workspace')
    return entries


def migrate(project, plan, entries, staging_uid):
    pub_uid = base.service_uid(plan['services']['publisher'])
    ui_uid = base.service_uid(plan['services']['web'])
    journal = base.WORK / ('layout-migration-' + str(time.time_ns()) + '.json')
    acl = subprocess.run(['getfacl', '-p', str(ATTIC)], check=True, capture_output=True, text=True).stdout
    data = dict(status='prepared', parent_acl=acl, moves=[], created=[])
    atomic_json(journal, data)
    committed = False
    try:
        library = ATTIC / 'library'
        library.mkdir(mode=0o750)
        data['created'].append(str(library))
        atomic_json(journal, data)
        # Preserve original library access on the new library root, without
        # touching tags, files or artist/album permissions.
        owner = ATTIC.stat()
        os.chown(library, owner.st_uid, owner.st_gid)
        subprocess.run(['setfacl', '--set-file=-', str(library)], input=acl, text=True, check=True)
        moves = [(entry, ATTIC / 'publish-staging' if entry.name == '.curator-publish'
                  else library / entry.name) for entry in entries]
        moves.append((OLD_STAGING, ATTIC / 'staging'))
        for source, target in moves:
            # Persist intent before mutation for power-loss/manual recovery.
            data['pending'] = [str(source), str(target)]
            atomic_json(journal, data)
            rename_no_replace(source, target)
            data['moves'].append([str(source), str(target)])
            data.pop('pending', None)
            atomic_json(journal, data)
        transfer = ATTIC / 'publish-staging'
        if not transfer.exists():
            transfer.mkdir(mode=0o700)
            data['created'].append(str(transfer))
            atomic_json(journal, data)
        os.chown(transfer, pub_uid, 10000)
        subprocess.run(['setfacl', '-b', '-k', str(transfer)], check=True)
        transfer.chmod(0o2700)
        # No write permission on the parent: publisher cannot remove siblings or
        # bypass the read-only staging overlay by renaming its mountpoint.
        subprocess.run(['setfacl', '-b', '-k', str(ATTIC)], check=True)
        os.chown(ATTIC, 0, 10000)
        ATTIC.chmod(0o750)
        subprocess.run(['setfacl', '-m', f'u:{staging_uid}:r-x', str(ATTIC)], check=True)
        subprocess.run(['setfacl', '-m', f'u:{ui_uid}:r-x,u:{pub_uid}:rwx', str(library)], check=True)
        backup = base.install_plan(project, plan, kind='layout', normalizer=storage.canonical)
        committed = True
        data['status'] = 'committed'
        atomic_json(journal, data)
        print('Protected configuration backup:', backup)
        print('Migration journal:', journal)
    except Exception:
        if not committed:
            for source, target in reversed(data['moves']):
                rename_no_replace(Path(target), Path(source))
            for path in reversed(data['created']):
                Path(path).rmdir()  # Empty created folders only; never recursive deletion.
            subprocess.run(['setfacl', '--restore=-'], input=acl, text=True, check=True)
            data['status'] = 'rolled-back'
            atomic_json(journal, data)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--staging-user', required=True, help='Existing Linux/SMB account that should traverse the attic parent')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as administrator; the restricted deployment key cannot move music or change mounts')
    os.umask(0o077)
    import pwd
    staging_uid = pwd.getpwnam(args.staging_user).pw_uid
    project = base.project_directory(args.project)
    for name in ('compose.yaml', '.env'):
        if (base.CONTROL / name).is_symlink():
            raise ValueError('Protected configuration must not be symlinked')
    import fcntl
    with (base.WORK / 'deployment.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = json.loads(base.compose(project, base.CONTROL / 'compose.yaml', 'config', '--format', 'json').stdout)
        plan, current = layout_plan(config)
        if current:
            print('Three-folder attic is already configured. No files moved.')
            return
        entries = preflight()
        base.install_plan(project, plan, kind='layout', normalizer=storage.canonical, validate_only=True)
        print('New layout:', ATTIC, '-> staging / publish-staging / library')
        print('Move existing artist directories and staging without editing or replacing music.')
        print('Docker state, authentication, source drawer and approval rules are unchanged.')
        if not args.apply:
            print('Dry run only. Stop Navidrome before running again with --apply.')
            return
        images = storage.docker('ps', '--format', '{{.Image}}').stdout.splitlines()
        if any('navidrome' in image.lower() for image in images):
            raise ValueError('Stop the running Navidrome container before moving its music directories')
        base.compose(project, base.CONTROL / 'compose.yaml', 'stop', 'web', 'worker', 'publisher')
        entries = preflight()
        migrate(project, plan, entries, staging_uid)
        print('Saved. Keep Navidrome stopped; set its music path to', ATTIC / 'library')
        print('Re-deploy Rhythm Desk normally. Update any SMB staging share to', ATTIC / 'staging/incoming')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit('Layout migration refused or failed: ' + str(error) + '. Leave services stopped and inspect the migration journal before retrying.')
