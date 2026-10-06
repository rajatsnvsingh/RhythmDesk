"""Genres and user-facing category tags (audio grouping field), not technical tags."""
import json
import threading
from common import atomic_json

POLICY_LOCK = threading.RLock()


def labels(value):
    values = value if isinstance(value, list) else str(value or '').split(';')
    result = []
    for item in values:
        if not isinstance(item, str) or len(item) > 100 or any(ord(c) < 32 for c in item):
            raise ValueError('Genre/tag labels must be text under 100 characters')
        item = item.strip()
        if item and item.casefold() not in {v.casefold() for v in result}:
            result.append(item)
    if len(result) > 100:
        raise ValueError('Too many labels')
    return result


def policy(settings):
    path = settings.state / 'taxonomy' / 'allow-list.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'genres': [], 'tags': []}


def save_policy(settings, data):
    if set(data) != {'genres', 'tags'}:
        raise ValueError('Supply genre and tag allow lists')
    data = {key: labels(value) for key, value in data.items()}
    directory = settings.state / 'taxonomy'
    directory.mkdir(exist_ok=True)
    with POLICY_LOCK:
        atomic_json(directory / 'allow-list.json', data)
    return data


def allow_label(settings, kind, value):
    if kind not in ('genres', 'tags'):
        raise ValueError('Choose genres or tags')
    values = labels(value)
    if len(values) != 1:
        raise ValueError('Allow one non-empty label at a time')
    with POLICY_LOCK:
        current = policy(settings)
        current[kind] = labels(current[kind] + values)
        return save_policy(settings, current)


def read_labels(media):
    return {'genres': labels(media.genres or []), 'tags': labels(media.grouping)}


def unknown(settings, tracks):
    allowed = policy(settings)
    return {key: sorted({v for track in tracks for v in track.get(key, [])
                        if v.casefold() not in {x.casefold() for x in allowed[key]}})
            for key in ('genres', 'tags')}


def validate_audio(settings, audio):
    from mediafile import MediaFile
    from common import inventory
    found = unknown(settings, [read_labels(MediaFile(audio / name)) for name in inventory(audio)])
    if any(found.values()):
        raise ValueError('Unapproved genres/tags: ' + json.dumps(found) + '. Remove them or add them to the allow list before publication.')
