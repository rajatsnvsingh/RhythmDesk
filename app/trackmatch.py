"""Resolve singleton-matched recordings to a verified release, never supplied tags."""
import time
import uuid
import requests


def lookup(entity, identifier, includes):
    identifier = str(uuid.UUID(identifier))
    time.sleep(1.05)
    response = requests.get(f'https://musicbrainz.org/ws/2/{entity}/{identifier}',
                            params={'fmt':'json','inc':includes},
                            headers={'User-Agent':'RhythmDesk/1.0 (private music curator)'}, timeout=60)
    response.raise_for_status()
    return response.json()


def credit(values):
    return ''.join(v.get('name',v.get('artist',{}).get('name','')) + v.get('joinphrase','') for v in values)


def resolve_release(files, album, release_id='', fetch=lookup):
    from mediafile import MediaFile
    media = [MediaFile(path) for path in files]
    if not media or any(not m.mb_trackid for m in media):
        raise ValueError('Recording match skipped or ambiguous; inspect singleton matching log')
    if not release_id:
        options = None
        for m in media:
            recording = fetch('recording',m.mb_trackid,'releases')
            choices = {r['id'] for r in recording.get('releases',[]) if r.get('title','').casefold()==album.casefold()}
            options = choices if options is None else options & choices
        if not options or len(options)!=1:
            raise ValueError('Recording matched, but release edition is ambiguous or unavailable. Select an exact MusicBrainz release ID and retry.')
        release_id = next(iter(options))
    release = fetch('release',release_id,'recordings+artist-credits')
    year = release.get('date','')[:4]
    if not year.isdigit():
        raise ValueError('Verified release has no year; needs review')
    for m in media:
        positions = [(disc,track) for disc in release.get('media',[]) for track in disc.get('tracks',[])
                     if track.get('recording',{}).get('id')==m.mb_trackid]
        if len(positions)!=1:
            raise ValueError('Recording absent or repeated in selected release; needs review')
        disc, track = positions[0]
        m.album=release['title'];m.albumartist=credit(release.get('artist-credit',[]))
        m.year=int(year);m.track=int(track['position']);m.disc=int(disc['position'])
        m.title=track.get('title') or track['recording']['title'];m.mb_albumid=release_id
        m.save()
    return release_id
