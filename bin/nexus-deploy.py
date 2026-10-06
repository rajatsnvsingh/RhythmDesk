#!/usr/bin/python3
"""Root-owned forced-command deployment helper; never install it writable by deploy user."""
import ast
import fcntl
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile

CONTROL = Path('/etc/rhythm-deploy')
WORK = Path('/var/lib/rhythm-deploy')
PROJECT = Path((CONTROL / 'project-path').read_text().strip()) if (CONTROL / 'project-path').exists() else Path('/opt/rhythm-desk')
IMAGE = 'nexus/rhythm-curator:local'
SERVICES = ['worker', 'web', 'publisher']
LIMIT = 20 * 1024 * 1024

def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)

def compose(*args, **kwargs):
    return run(['/usr/bin/docker', 'compose', '--project-directory', str(PROJECT),
                '--env-file', str(CONTROL / '.env'), '-f', str(CONTROL / 'compose.yaml'), *args], **kwargs)

def unpack(payload, destination):
    """Only regular app sources; no links, binaries, build files, or traversal."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries) > 500 or sum(x.file_size for x in entries) > LIMIT:
            raise ValueError('Update is too large')
        seen = set()
        for entry in entries:
            name = entry.filename
            path = PurePosixPath(name)
            mode = entry.external_attr >> 16
            if (name in seen or '\\' in name or path.is_absolute() or
                any(p in ('', '.', '..') for p in name.split('/')) or
                not path.parts or path.parts[0] != 'app' or
                path.suffix not in ('.py', '.html', '.css', '.js') or
                len(path.parts) not in (2, 3) or
                (len(path.parts) == 3 and path.parts[1] != 'static') or
                (stat.S_IFMT(mode) not in (0, stat.S_IFREG))):
                raise ValueError('Unsafe or unsupported update entry')
            seen.add(name)
            data = archive.read(entry)
            if path.suffix == '.py':
                ast.parse(data.decode('utf-8'), filename=name)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            target.write_bytes(data)
            target.chmod(0o644)
        required = {'app/server.py', 'app/worker.py', 'app/publisher.py', 'app/common.py', 'app/static/index.html'}
        if not required <= seen:
            raise ValueError('Update is missing required application files')
        for directory in (destination / 'app', *(destination / 'app').rglob('*')):
            if directory.is_dir():
                directory.chmod(0o755)

def deploy():
    payload = sys.stdin.buffer.read(LIMIT + 1)
    if len(payload) > LIMIT:
        raise ValueError('Archive exceeds 20 MiB')
    # Control files are administrator snapshots, not uploaded or writable by the SSH account.
    config = json.loads(compose('config', '--format', 'json', capture_output=True, text=True).stdout)
    if set(config['services']) != set(SERVICES) or any(config['services'][s]['image'] != IMAGE for s in SERVICES):
        raise ValueError('Unexpected deployment services or image; administrator review required')
    if PROJECT.is_symlink() or (PROJECT / 'app').is_symlink():
        raise ValueError('Deployment target must not be a symlink')
    previous = run(['/usr/bin/docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'],
                   capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory(prefix='build-', dir=WORK) as folder:
        context = Path(folder)
        unpack(payload, context)
        for name in ('Dockerfile', 'requirements.txt'):
            shutil.copyfile(CONTROL / name, context / name)
        shutil.copytree(CONTROL / 'config', context / 'config')
        (context / 'config').chmod(0o755)
        (context / 'config/config.yaml').chmod(0o644)
        candidate = 'nexus/rhythm-curator:deploy-' + str(time.time_ns())
        run(['/usr/bin/docker', 'build', '-t', candidate, str(context)])
        backup = WORK / ('app-backup-' + str(time.time_ns()))
        shutil.copytree(PROJECT / 'app', backup, symlinks=False)
        # All instances use immutable image contents; the host source is synchronized for the owner.
        replacement = PROJECT / ('.app-deploy-' + str(time.time_ns()))
        shutil.copytree(context / 'app', replacement)
        old = PROJECT / ('.app-previous-' + str(time.time_ns()))
        (PROJECT / 'app').rename(old)
        replacement.rename(PROJECT / 'app')
        try:
            run(['/usr/bin/docker', 'tag', candidate, IMAGE])
            compose('up', '-d', '--no-build', *SERVICES)
            time.sleep(5)
            ids = compose('ps', '-q', *SERVICES, capture_output=True, text=True).stdout.split()
            states = [run(['/usr/bin/docker', 'inspect', '--format', '{{.State.Status}}', i],
                          capture_output=True, text=True).stdout.strip() for i in ids]
            if len(states) != 3 or any(s != 'running' for s in states):
                raise ValueError('A container did not remain running')
        except Exception:
            failed = PROJECT / ('.app-failed-' + str(time.time_ns()))
            (PROJECT / 'app').rename(failed)
            old.rename(PROJECT / 'app')
            run(['/usr/bin/docker', 'tag', previous, IMAGE])
            compose('up', '-d', '--no-build', *SERVICES)
            raise
        print('Deployment completed. Containers running; browser verification still required.', flush=True)
        print('Previous source retained at ' + str(backup), flush=True)
        compose('ps')

def main():
    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in ('deploy', 'status'):
        raise ValueError('Only deploy and status are allowed')
    os.umask(0o077)
    with (WORK / 'deployment.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if sys.argv[1] == 'status':
            compose('ps')
        else:
            deploy()

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Deployment refused or failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
