"""Persisted intake controls and non-operative Docker directory plans."""
import json
import os
from pathlib import PurePosixPath
from common import connect, event


def runtime(settings):
    defaults = dict(stable_seconds=settings.stable_seconds, scan_interval=15,
                    automatic_grouping=True, paused=False)
    with connect(settings) as db:
        row = db.execute("SELECT value FROM meta WHERE key='runtime_settings'").fetchone()
    return defaults | (json.loads(row['value']) if row else {})


def save_runtime(settings, values):
    if set(values) != {'stable_seconds', 'scan_interval', 'automatic_grouping', 'paused'}:
        raise ValueError('Supply all four intake settings; unknown settings are forbidden')
    for name, low, high in [('stable_seconds', 0, 3600), ('scan_interval', 5, 300)]:
        if type(values[name]) is not int or not low <= values[name] <= high:
            raise ValueError(f'{name} must be an integer between {low} and {high}')
    for name in ('automatic_grouping', 'paused'):
        if type(values[name]) is not bool:
            raise ValueError(f'{name} must be a boolean')
    with connect(settings) as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES('runtime_settings',?)", (json.dumps(values),))
    event(settings, 'Intake settings updated: ' + json.dumps(values))


def save_paths(settings, values):
    if set(values) != {'STAGING_PATH', 'STATE_PATH', 'LIBRARY_PATH'}:
        raise ValueError('Supply staging, state and library directories')
    paths = []
    for value in values.values():
        if not isinstance(value, str) or not value.startswith('/') or any(c in value for c in "\n\r\x00'$:#\\"):
            raise ValueError('Use absolute Linux directory paths without environment or mount syntax')
        path = PurePosixPath(value)
        if '..' in path.parts or len(path.parts) < 4 or str(path) != value:
            raise ValueError('Use normalized, specific directories, not broad system roots')
        paths.append(path)
    for i, path in enumerate(paths):
        for other in paths[i + 1:]:
            if path == other or path in other.parents or other in path.parents:
                raise ValueError('Staging, state and library directories must not overlap')
    with connect(settings) as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES('directory_plan',?)", (json.dumps(values),))
    event(settings, 'Directory plan saved; current mounts and publishing destination unchanged')


def describe(settings):
    mappings = [('STAGING_PATH', settings.incoming.parent, 'CURATOR_HOST_STAGING'),
                ('STATE_PATH', settings.state, 'CURATOR_HOST_STATE'),
                ('LIBRARY_PATH', settings.library, 'CURATOR_HOST_LIBRARY')]
    with connect(settings) as db:
        row = db.execute("SELECT value FROM meta WHERE key='directory_plan'").fetchone()
    from source_browser import describe as source_description
    return dict(source=source_description(settings), runtime=runtime(settings), mounts=[dict(key=key, host=os.environ.get(env, str(path)),
                internal=str(path), exists=path.is_dir(), readable=os.access(path, os.R_OK),
                writable=os.access(path, os.W_OK)) for key, path, env in mappings],
                plan=json.loads(row['value']) if row else None,
                incoming=str(settings.incoming), curated=str(settings.curated), review=str(settings.review))


def export_paths(settings):
    plan = describe(settings)['plan']
    if not plan:
        raise ValueError('Save a directory plan first')
    return '# Pending directory mappings. Stop services and prepare permissions before applying.\n' + ''.join(
        f"{key}='{value}'\n" for key, value in plan.items())
