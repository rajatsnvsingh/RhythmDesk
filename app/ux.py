"""Read-only presentation evidence. Never an authorization to publish."""
import uuid

ACTIVE = {'Queued', 'Processing', 'Editing', 'Publishing'}
HISTORY = {'Approved', 'Regrouped', 'Purged'}


def readiness(job, record, unknown_labels, destination_exists, publisher, demo=False):
    blockers = []
    if job['status'] != 'Curated':
        blockers.append(dict(code='state', message='Prepare a Curated copy before publication.'))
    if not record or not record.get('tracks') or record.get('revision') != job.get('revision'):
        blockers.append(dict(code='record', message='No current, complete reviewed audio revision.'))
    for kind, values in unknown_labels.items():
        if values:
            blockers.append(dict(code=kind, message='Unapproved ' + kind + ': ' + '; '.join(values)))
    if destination_exists and job['status'] != 'Approved':
        blockers.append(dict(code='destination', message='Destination exists. No album will be replaced.'))
    prepared = not blockers
    unverified=[t['title'] for t in (record or {}).get('tracks',[]) if t.get('match_status')=='unverified']
    if unverified:
        blockers.append(dict(code='track-review',message='Check unresolved track metadata and numbering: '+ '; '.join(unverified)))
    if demo:
        blockers.append(dict(code='demo', message='Demo workspace: publication is disabled.'))
    elif not publisher:
        blockers.append(dict(code='publisher', message='Publisher unavailable. You can review, but cannot publish yet.'))
    return dict(can_review=prepared, can_publish=not blockers, blockers=blockers,
                revision=job.get('revision'), explicit_approval_required=True)


def recovery(job):
    reason = job.get('reason') or ''
    text = reason.casefold()
    if job['status'] != 'Needs review':
        return None
    if job.get('mode') == 'manual' or 'manual metadata' in text:
        code, title, action = 'manual', 'Manual metadata needed', 'Enter metadata and prepare all source tracks.'
    elif any(x in text for x in ('timeout', 'timed out', 'connection', 'http', 'network')):
        code, title, action = 'service', 'Lookup service or connection failed', 'Retry when the service is reachable; originals are retained.'
    elif any(x in text for x in ('ambiguous', 'edition', 'repeated in selected release')):
        code, title, action = 'edition', 'Release identity needs a decision', 'Choose an exact MusicBrainz release, or curate manually.'
    elif any(x in text for x in ('input changed', 'checksum', 'original copy')):
        code, title, action = 'source', 'Source changed or verification failed', 'Check the source files, return to Incoming and scan/regroup.'
    elif any(x in text for x in ('skipped', 'incomplete', 'no audio output', 'not all eligible')):
        code, title, action = 'match', 'No complete, validated output', 'Check the mode and intended release, or curate manually. No matched subset will publish.'
    elif 'artwork' in text:
        code, title, action = 'artwork', 'Artwork needs correction', 'Choose or paste a usable cover for all output tracks.'
    else:
        code, title, action = 'review', 'Curation needs attention', 'Inspect the explanation, adjust the mode or use manual metadata.'
    return dict(code=code, title=title, action=action, evidence=reason)


def release_identity(job, record):
    values = {t.get('release_id') for t in (record or {}).get('tracks', []) if t.get('release_id')}
    if not values and job.get('release_id'):
        values.add(job['release_id'])
    ids = []
    for value in sorted(values):
        try:
            ids.append(str(uuid.UUID(value)))
        except (ValueError, TypeError, AttributeError):
            continue
    return dict(provenance='User supplied' if job.get('mode') == 'manual' else
                'User-selected catalogue edition' if (record or {}).get('selected_candidate') else 'Catalogue matching',
                release_ids=ids, completeness='Complete-release validation' if job.get('mode') == 'album'
                else 'Completeness not certified')


def queue_group(job):
    if job['status'] in ACTIVE:
        return 'processing'
    if job['status'] in HISTORY:
        return 'history'
    if job['status'] == 'Curated' and job['readiness']['can_review']:
        return 'ready'
    return 'attention'


def queue_page(jobs, query='', group='', offset=0, limit=50, sort='updated', direction=-1):
    """Aggregate all jobs before paging; counts never inherit the page limit."""
    counts = {key: sum(queue_group(j) == key for j in jobs) for key in ('attention', 'ready', 'processing', 'history')}
    query = query.casefold()
    found = [j for j in jobs if (not group or (group == 'all' and queue_group(j) in ('attention', 'ready')) or queue_group(j) == group)
             and (not query or query in ' '.join(str(j.get(k) or '') for k in ('artist', 'album', 'year', 'reason')).casefold())]
    sort = sort if sort in ('album', 'artist', 'year', 'track_count', 'updated') else 'updated'
    found.sort(key=lambda j: (j.get(sort) or 0) if sort in ('year', 'track_count', 'updated')
               else str(j.get(sort) or '').casefold(), reverse=direction != 1)
    return found[offset:offset + limit], len(found), counts
