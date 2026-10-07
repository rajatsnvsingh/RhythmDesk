"""Keep every submitted source, with explicit per-track catalogue evidence."""
import json
from pathlib import Path
import shutil
import tempfile
import requests

from common import AUDIO, clean_name, safe_child


def prepare(folder,job,rows,resolve,force_artwork=False):
    from mediafile import MediaFile
    from artwork import usable_image
    audio=folder/'audio'
    manifest=folder/'PARTIAL-MAP.json'
    mapping=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
    prepared=Path(tempfile.mkdtemp(prefix='partial-audio-',dir=folder))
    entries=[]
    for index,row in enumerate(rows,1):
        name=f'{index:04d}{Path(row["path"]).suffix.lower()}'
        evidence=mapping.get(name)
        source=folder/'originals'/name
        matched=False;reason='No catalogue mapping; original track metadata retained.'
        if evidence and evidence['matched']:
            output=Path(evidence['file']).resolve()
            output=safe_child(audio,output.relative_to(audio.resolve()))
            if not output.is_file():raise ValueError('Matched working copy is missing')
            source=output;matched=True
        target=prepared/name;shutil.copy2(source,target)
        media=MediaFile(target)
        if matched:
            try:
                if job['release_id']:
                    if media.mb_albumid!=job['release_id']:raise ValueError('Output edition differs from the selected release')
                else:resolve([target],job['album'],'')
            except (ValueError,requests.RequestException) as error:
                matched=False;reason='Recording/release membership needs review: '+str(error)
        media=MediaFile(target)
        if not matched:
            # Restore original track-level values even after an uncertain recording match.
            original=MediaFile(folder/'originals'/name)
            media.delete();media=MediaFile(target)
            media.artist=original.artist or row['artist'] or job['artist']
            media.title=original.title or row['title'] or Path(row['path']).stem
            media.track=original.track or row['track'] or index;media.disc=original.disc or row['disc'] or 1
            media.genres=original.genres;media.grouping=original.grouping
            image=usable_image(original);media.images=[image] if image else []
            for field in ('mb_albumid','mb_trackid','mb_releasetrackid','mb_artistid','mb_albumartistid'):setattr(media,field,'')
            media.save()
        entries.append(dict(path=target,source_id=row['id'],match_status='catalogue' if matched else 'unverified',match_note='' if matched else reason))
    verified=next((MediaFile(e['path']) for e in entries if e['match_status']=='catalogue'),None)
    replacement=usable_image(verified) if verified else None
    replace_art=force_artwork or any(not usable_image(MediaFile(folder/'originals'/f'{i:04d}{Path(row["path"]).suffix.lower()}')) for i,row in enumerate(rows,1))
    artist=(verified.albumartist or verified.artist) if verified else job['artist']
    album=verified.album if verified else job['album'];year=verified.year if verified else job['year']
    destination=Path(clean_name(artist))/clean_name(f'{album} ({year or 0})')
    used=set();identities={}
    # Reserve catalogue positions first; tentative numbers must never displace them.
    for entry in sorted(entries,key=lambda e:e['match_status']!='catalogue'):
        media=MediaFile(entry['path']);position=(media.disc or 1,media.track or 1)
        if position in used:
            entry['match_status']='unverified';entry['match_note']+=' Duplicate numbering; a provisional free position was assigned. Verify it.'
            number=1
            while (position[0],number) in used:number+=1
            position=(position[0],number)
        used.add(position)
        media.albumartist=artist;media.album=album;media.year=year;media.disc,media.track=position
        if replace_art and replacement:media.images=[replacement]
        if entry['match_status']=='unverified':media.mb_albumid='';media.mb_releasetrackid='';media.mb_trackid=''
        media.save()
        identities[position]={k:entry[k] for k in ('source_id','match_status','match_note')}
        target=prepared/destination/entry['path'].name;target.parent.mkdir(parents=True,exist_ok=True);entry['path'].rename(target)
    # Reject unexpected outputs rather than silently masking an inconsistent manifest.
    mapped={Path(e['file']).resolve() for e in mapping.values()}
    actual={p.resolve() for p in audio.rglob('*') if p.is_file() and p.suffix.lower() in AUDIO}
    if actual!=mapped:raise ValueError('Partial output evidence is incomplete; inspect the matching log')
    shutil.rmtree(audio);prepared.rename(audio)
    return identities
