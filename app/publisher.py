"""The sole library writer. Accepts explicit, versioned approvals from the UI."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import tempfile
import threading
import time

from common import Settings, atomic_json, inventory, revision, safe_child

LOCK = threading.Lock()


def rename_no_replace(source, target):
    if os.name == 'nt':
        os.rename(source, target)  # Windows refuses existing targets.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def publish(settings, job_id, expected_revision, approved_by='web'):
    if not job_id.startswith('work.') or Path(job_id).name != job_id:
        raise ValueError('Invalid job ID')
    if len(expected_revision) != 64:
        raise ValueError('Approval must name the reviewed revision')
    audit_dir = settings.state / 'approvals'
    audit_dir.mkdir(exist_ok=True)
    audit = safe_child(audit_dir, job_id + '.json')
    with LOCK:
        # A recorded successful approval is idempotent; no second library write.
        if audit.exists():
            receipt = json.loads(audit.read_text(encoding='utf-8'))
            if receipt['revision'] != expected_revision:
                raise ValueError('Approval revision differs from the recorded publication')
            target = safe_child(settings.library, receipt['destination'])
            expected_files = receipt['files']
            if inventory(target) != expected_files:
                raise ValueError('Published album has changed; administrator review required')
            return receipt
        job = safe_child(settings.curated, job_id)
        record = json.loads(safe_child(job, 'REVIEW.json').read_text(encoding='utf-8'))
        if record['status'] != 'Curated' or revision(record) != expected_revision or record['revision'] != expected_revision:
            raise ValueError('Review changed. Reload and inspect this album before approving.')
        destination = Path(record['destination'])
        if len(destination.parts) != 2:
            raise ValueError('Expected Artist/Album (Year) destination')
        audio = safe_child(job, 'audio')
        if inventory(audio) != record['sha256']:
            raise ValueError('Audio differs from the review; publication refused')
        from taxonomy import validate_audio
        validate_audio(settings, audio)
        if not settings.library.is_dir() or settings.library.is_symlink():
            raise ValueError('Final library does not exist or is symlinked')
        target = safe_child(settings.library, destination)
        if target.exists():
            raise ValueError('Album already exists. Replacement is forbidden.')
        artist = target.parent
        if not artist.exists():
            artist.mkdir(mode=0o755)
            artist.chmod(0o755)
        safe_child(settings.library, destination)
        # Hidden transfer lives outside the library until the atomic publication.
        # It must be on the same filesystem. EXDEV fails without partial albums.
        transfer_root = settings.library / '.curator-publish'
        transfer_root.mkdir(exist_ok=True)
        if transfer_root.is_symlink():
            raise ValueError('Unsafe publisher transfer root')
        temporary = Path(tempfile.mkdtemp(prefix='approval-', dir=transfer_root))
        try:
            copy_root = temporary / 'audio'
            shutil.copytree(audio, copy_root, symlinks=True)
            if inventory(copy_root) != record['sha256']:
                raise ValueError('Copy differs from the reviewed audio')
            validate_audio(settings, copy_root)
            album = safe_child(copy_root, destination)
            for file in album.iterdir():
                if not file.is_file():
                    raise ValueError('Album output must contain only audio files')
                file.chmod(0o644)
                with file.open('r+b') as stream:
                    os.fsync(stream.fileno())
            album.chmod(0o755)
            rename_no_replace(album, target)
            receipt = {'job_id': job_id, 'revision': expected_revision,
                       'destination': destination.as_posix(), 'approved_at': time.time(),
                       'approved_by': approved_by,
                       'files': {Path(name).name: value for name, value in record['sha256'].items()}}
            atomic_json(audit, receipt)
            audit.chmod(0o640)
            return receipt
        finally:
            shutil.rmtree(temporary)


def serve(settings):
    os.umask(0o007)
    import pwd
    allowed_uid = int(os.environ['CURATOR_UI_UID']) if os.environ.get('CURATOR_UI_UID') else pwd.getpwnam(os.environ.get('CURATOR_UI_USER', 'rhythm-ui')).pw_uid
    path = Path(settings.socket)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        os.chmod(path, 0o660)
        server.listen(8)
        while True:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(120)
                try:
                    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != allowed_uid:
                        raise ValueError('Only the authenticated web service can request publication')
                    data = b''
                    while b'\n' not in data:
                        chunk = connection.recv(4096)
                        if not chunk or len(data) + len(chunk) > 16384:
                            raise ValueError('Invalid approval request')
                        data += chunk
                    request = json.loads(data.split(b'\n', 1)[0])
                    if request.get('action') != 'approve':
                        raise ValueError('Only an explicit approve action is supported')
                    result = publish(settings, request['job_id'], request['revision'], 'web administrator')
                    response = {'ok': True, 'receipt': result}
                except Exception as error:
                    response = {'ok': False, 'error': str(error)}
                connection.sendall(json.dumps(response).encode() + b'\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root')
    args = parser.parse_args()
    serve(Settings(args.root))


if __name__ == '__main__':
    main()
