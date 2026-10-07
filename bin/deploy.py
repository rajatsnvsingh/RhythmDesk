"""Send source-only update over the restricted SSH command, preserving binary stdin."""
import argparse
import io
from pathlib import Path
import subprocess
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--key', required=True)
parser.add_argument('--host', required=True, help='Restricted SSH account, e.g. rhythm-deploy@music-server')
parser.add_argument('--status', action='store_true')
args = parser.parse_args()
command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-i', args.key, args.host,
           'status' if args.status else 'deploy']
if args.status:
    subprocess.run(command, check=True)
else:
    root = Path(__file__).resolve().parents[1]
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((root / 'app').rglob('*')):
            if path.is_symlink():
                raise ValueError('Symlinks may not be deployed')
            if path.is_file() and path.suffix in ('.py', '.html', '.css', '.js'):
                archive.write(path, path.relative_to(root).as_posix())
    subprocess.run(command, input=payload.getvalue(), check=True)
