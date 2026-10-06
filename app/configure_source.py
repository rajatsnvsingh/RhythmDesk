#!/usr/bin/python3
"""One-time administrator addition of a read-only drawer mount to pinned deployment."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project',required=True)
    parser.add_argument('--source',required=True)
    args=parser.parse_args()
    if os.geteuid()!=0:raise ValueError('Run as administrator; the SSH deployment key cannot change mounts')
    source=Path(args.source)
    if source.is_symlink() or not source.is_absolute() or not source.is_dir() or len(source.resolve().parts)<4:
        raise ValueError('Choose an existing, specific directory, not a broad filesystem root')
    source=source.resolve();project=Path(args.project).resolve();control=Path('/etc/rhythm-deploy')
    path=control/'compose.yaml'
    rendered=subprocess.run(['/usr/bin/docker','compose','--project-directory',str(project),'--env-file',str(control/'.env'),'-f',str(path),'config','--format','json'],check=True,capture_output=True,text=True)
    config=json.loads(rendered.stdout)
    if set(config['services'])!={'web','worker','publisher'}:raise ValueError('Unexpected service configuration')
    web=config['services']['web']
    volumes=web.setdefault('volumes',[])
    volumes[:]=[v for v in volumes if v.get('target')!='/mnt/music-source']
    volumes.append(dict(type='bind',source=str(source),target='/mnt/music-source',read_only=True,bind={'create_host_path':False}))
    web.setdefault('environment',{}).update(CURATOR_SOURCE_ROOT='/mnt/music-source',CURATOR_HOST_SOURCE=str(source))
    os.umask(0o077)
    backup=path.with_name('compose.before-source-'+str(time.time_ns())+'.yaml')
    shutil.copyfile(path,backup);backup.chmod(0o600)
    temporary=path.with_name('compose.source.pending.yaml')
    temporary.write_text(json.dumps(config,indent=2)+'\n');temporary.chmod(0o600)
    subprocess.run(['/usr/bin/docker','compose','--project-directory',str(project),'--env-file',str(control/'.env'),'-f',str(temporary),'config','--quiet'],check=True)
    temporary.replace(path)
    print('Pinned deployment updated with a read-only music drawer. Recreate through the normal deploy command.')
    print('Protected configuration backup:',backup)
    print('Source permissions are unchanged; the UI account still needs read/traverse access.')

if __name__=='__main__':main()
