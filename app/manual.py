"""Explicit user metadata, never a catalogue match or publication authorization."""
import base64
import binascii
from io import BytesIO
import json
import time
from PIL import Image as PILImage
from common import clean_name, connect, event
from taxonomy import labels


def cover(value):
    if not value:return None
    if not isinstance(value,str) or len(value)>14*1024**2:
        raise ValueError('Artwork must be an image smaller than 10 MiB')
    try:
        data=base64.b64decode(value,validate=True)
        if len(data)>10*1024**2:raise ValueError('Artwork exceeds 10 MiB')
        with PILImage.open(BytesIO(data)) as image:
            if image.width*image.height>20_000_000:raise ValueError('Artwork exceeds 20 megapixels')
            image.load()
            image=image.convert('RGB');image.thumbnail((1600,1600))
            output=BytesIO();image.save(output,format='JPEG',quality=92)
        return output.getvalue()
    except (OSError,SyntaxError,binascii.Error,PILImage.DecompressionBombError) as error:
        raise ValueError('Unreadable artwork; paste or choose a valid image') from error


def validate(data, sources):
    artist=str(data.get('artist','')).strip();album=str(data.get('album','')).strip()
    clean_name(artist);clean_name(album)
    year=int(data.get('year') or 0)
    if not 1000<=year<=9999:raise ValueError('Release year must have four digits')
    edits=data.get('tracks',[])
    excluded=data.get('excluded_source_ids',[])
    if not isinstance(excluded,list) or any(not isinstance(x,str) for x in excluded) or len(set(excluded))!=len(excluded):raise ValueError('Invalid excluded source selection')
    if excluded and data.get('exclusions_confirmed') is not True:raise ValueError('Confirm song exclusions before preparation')
    if not isinstance(edits,list) or not edits or any(not isinstance(x,dict) or not isinstance(x.get('id'),str) for x in edits):raise ValueError('Keep at least one source track')
    ids=[x['id'] for x in edits]
    if len(set(ids))!=len(ids) or set(ids)&set(excluded) or set(ids)|set(excluded)!={s['id'] for s in sources}:raise ValueError('Source tracks changed; include or explicitly exclude every source track')
    tracks=[];positions=set()
    for edit in edits:
        title=str(edit.get('title','')).strip();track_artist=str(edit.get('artist','')).strip()
        clean_name(title);clean_name(track_artist)
        number=int(edit.get('track') or 0);disc=int(edit.get('disc') or 0)
        if not 1<=number<=9999 or not 1<=disc<=999:raise ValueError('Positive disc and track numbers are required')
        if (disc,number) in positions:raise ValueError('Duplicate disc/track position; correct numbering')
        positions.add((disc,number))
        tracks.append(dict(id=edit['id'],title=title,artist=track_artist,track=number,disc=disc,
                           genres=labels(edit.get('genres',[])),tags=labels(edit.get('tags',[]))))
    image=cover(data.get('artwork'))
    return dict(artist=artist,album=album,year=year,tracks=tracks,
                excluded_source_ids=excluded,exclusions_confirmed=bool(excluded),
                artwork=base64.b64encode(image).decode() if image else '')


def queue(settings, job_id, data):
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        job=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not job or job['status'] not in ('Needs review','Curated','Queued'):
            raise ValueError('Only idle unpublished releases can be manually curated')
        if job['status']=='Curated' and data.get('revision')!=job['revision']:
            raise ValueError('Reviewed version changed; reload before manual curation')
        sources=db.execute('SELECT * FROM tracks WHERE job_id=?',(job_id,)).fetchall()
        payload=validate(data,sources)
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('manual:'+job_id,json.dumps(payload)))
        db.execute('DELETE FROM meta WHERE key=?',('progress:'+job_id,))
        db.execute("UPDATE jobs SET status='Queued',mode='manual',artist=?,album=?,year=?,track_count=?,release_id='',reason='',updated=? WHERE id=?",
                   (payload['artist'],payload['album'],payload['year'],len(payload['tracks']),time.time(),job_id))
    event(settings,'Manual metadata submitted; prepare working copies without catalogue matching. Approval still required.',job_id)


def apply(path, payload, edit):
    from mediafile import MediaFile, Image, ImageType
    from artwork import usable_image
    media=MediaFile(path)
    existing=usable_image(media)
    image=Image(base64.b64decode(payload['artwork']),type=ImageType.front) if payload['artwork'] else existing
    # Scrub only the disposable working copy, preserving valid cover artwork.
    media.delete();media=MediaFile(path)
    media.albumartist=payload['artist'];media.album=payload['album'];media.year=payload['year']
    media.artist=edit['artist'];media.title=edit['title'];media.track=edit['track'];media.disc=edit['disc']
    media.genres=edit['genres'];media.grouping='; '.join(edit['tags'])
    media.images=[image] if image else []
    media.save()
