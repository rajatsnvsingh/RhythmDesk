"""Prepare only explicitly named Linux music directories for Docker service IDs."""
import argparse
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staging', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--worker-uid', type=int, default=10001)
    parser.add_argument('--ui-uid', type=int, default=10002)
    parser.add_argument('--publisher-uid', type=int, default=10003)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run with sudo to grant the service IDs directory access')
    for path in (args.staging, args.state, args.library):
        if not path.is_absolute() or path.is_symlink() or len(path.parts) < 4:
            parser.error('Pass absolute, specific music directories without symlinks')
    paths = [p.resolve() for p in (args.staging, args.state, args.library)]
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(paths) for b in paths[i+1:]):
        parser.error('Staging, state and library must be separate non-overlapping directories')
    if not args.library.is_dir():
        parser.error('The final library must already exist')
    import grp
    import pwd
    identities = [('rhythm-curator', args.worker_uid), ('rhythm-ui', args.ui_uid), ('rhythm-publisher', args.publisher_uid)]
    if len({uid for _, uid in identities}) != 3:
        parser.error('The three services require distinct UIDs')
    for name, uid in identities:
        if uid < 1000:
            parser.error('Choose unused service UIDs of at least 1000')
        try:
            existing = pwd.getpwuid(uid)
            if existing.pw_name != name or not existing.pw_shell.endswith('nologin'):
                parser.error(f'UID {uid} already belongs to another or interactive account')
        except KeyError:
            pass
        try:
            if pwd.getpwnam(name).pw_uid != uid:
                parser.error(f'{name} already exists with a different UID; set its UID explicitly')
        except KeyError:
            pass
    try:
        if grp.getgrgid(10000).gr_name != 'rhythm-curation':
            parser.error('GID 10000 is already used by another group')
    except KeyError:
        subprocess.run(['groupadd', '--gid', '10000', 'rhythm-curation'], check=True)
    for name, uid in identities:
        try:
            pwd.getpwnam(name)
        except KeyError:
            subprocess.run(['useradd', '--system', '--uid', str(uid), '--gid', '10000',
                            '--no-create-home', '--home-dir', '/nonexistent',
                            '--shell', '/usr/sbin/nologin', name], check=True)
    for root in (args.staging, args.state):
        root.mkdir(parents=True, exist_ok=True)
        os.chown(root, -1, 10000)
        root.chmod(0o2770)
        # Named account ACLs and default ACLs preserve existing owners.
        subprocess.run(['setfacl', '-R', '-m', f'u:{args.worker_uid}:rwX,u:{args.ui_uid}:rwX,u:{args.publisher_uid}:rwX,g:10000:rwX', str(root)], check=True)
        subprocess.run(['setfacl', '-m', 'd:g:10000:rwx,d:m:rwx', str(root)], check=True)
    for relative in ('incoming', 'Curated', 'needs-review'):
        folder = args.staging / relative
        folder.mkdir(exist_ok=True)
        os.chown(folder, -1, 10000)
        folder.chmod(0o2770)
    for relative in ('processed', 'approvals', 'cache', 'logs', 'taxonomy'):
        folder = args.state / relative
        folder.mkdir(exist_ok=True)
        os.chown(folder, -1, 10000)
        folder.chmod(0o2770)
    # Protect receipts; worker/web additionally mount this directory read-only.
    approvals = args.state / 'approvals'
    subprocess.run(['setfacl', '-b', '-k', str(approvals)], check=True)
    os.chown(approvals, args.publisher_uid, 10000)
    approvals.chmod(0o2750)
    # UI read-only library ACLs. Publisher writes root/artist folders only;
    # existing album contents are not granted write access.
    subprocess.run(['setfacl', '-R', '-m', f'u:{args.ui_uid}:rX,u:{args.publisher_uid}:rX', str(args.library)], check=True)
    for folder in [args.library, *(p for p in args.library.iterdir() if p.is_dir() and not p.is_symlink())]:
        subprocess.run(['setfacl', '-m', f'u:{args.publisher_uid}:rwx', str(folder)], check=True)
    transfer = args.library / '.curator-publish'
    transfer.mkdir(exist_ok=True)
    os.chown(transfer, args.publisher_uid, 10000)
    transfer.chmod(0o2770)
    print('Prepared staging/state and scoped library ACLs. Existing music was not edited.')


if __name__ == '__main__':
    main()
