"""Beets choice hook: expose candidates; only accept an explicitly selected ID."""
import json
import os
from pathlib import Path
import re

from beets.plugins import BeetsPlugin


def describe(match):
    info = match.info
    return dict(release_id=info.album_id, artist=info.artist, album=info.album,
                year=info.year, country=info.country, media=info.media,
                label=info.label, catalognum=info.catalognum,
                disambiguation=info.albumdisambig, distance=float(match.distance),
                missing=len(match.extra_tracks), unmatched=len(match.extra_items),
                tracks=[dict(title=t.title, disc=t.medium, track=t.medium_index or t.index,
                             seconds=t.length) for t in info.tracks])


def read_candidates(folder, log=''):
    path = folder / 'CANDIDATES.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    # Older jobs already contain candidate IDs and scores in their matching log.
    result = []
    pattern = r'Candidate: (.*?) - (.*?) \(([0-9a-f-]{36})\) from MusicBrainz.*?Success\. Distance: ([0-9.]+)'
    for artist, album, release_id, distance in re.findall(pattern, log, re.S):
        result.append(dict(artist=artist, album=album, release_id=release_id,
                           distance=float(distance), tracks=[], missing=None, unmatched=None))
    return result


class RhythmCandidatesPlugin(BeetsPlugin):
    def __init__(self):
        super().__init__()
        self.config.add({'mode':'album'})
        self.register_listener('import_task_choice', self.choose)
        self.register_listener('import_task_files', self.files)

    def files(self,session,task):
        if self.config['mode'].get(str)!='partial':return
        path=Path(self.config['output'].as_str()).with_name('PARTIAL-MAP.json')
        records=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        for source,item in zip(task.old_paths,task.imported_items()):
            records[Path(os.fsdecode(source)).name]=dict(file=os.fsdecode(item.path),matched=bool(task.match))
        temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(records),encoding='utf-8');temporary.replace(path)

    def choose(self, session, task):
        if not task.is_album:
            return
        candidates = list(task.candidates or [])
        path = Path(self.config['output'].as_str())
        records = [describe(match) for match in candidates]
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(records), encoding='utf-8')
        temporary.replace(path)
        selected = self.config['selected'].as_str()
        if not selected:
            return  # Preserve Beets' normal confidence decision.
        match = next((m for m in candidates if m.info.album_id == selected), None)
        if match is None:
            raise ValueError('Chosen candidate was not returned by MusicBrainz; retry the search')
        mode=self.config['mode'].get(str)
        if mode not in ('album','partial'):
            raise ValueError('Candidate selection requires album or partial mode')
        if mode=='album' and (match.extra_items or match.extra_tracks):
            raise ValueError('Chosen edition has unmatched source tracks or is incomplete in complete-album mode; use partial or manual mode')
        if mode=='partial' and not getattr(match,'mapping',True):
            from beets.importer import Action
            task.set_choice(Action.SKIP)  # Worker retains originals instead of importing an empty album.
        else:task.set_choice(match)
        self._log.info('User selected MusicBrainz edition {}; preparing review only', selected)
