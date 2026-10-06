"""Presentation counts are not authorization; editor operations preserve originals."""
import base64
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from common import Settings, connect, inventory, sha256
from fixtures import track
from manual import queue
from server import Desk
from taxonomy import save_policy
from worker import scan, create_job, process_job
from ux import readiness, recovery, release_identity, queue_page


class PresentationTests(unittest.TestCase):
    def job(self, status='Curated', **values):
        return dict(id='work.fixture', status=status, revision='reviewed', mode='album', **values)

    def test_readiness_separates_review_from_publication(self):
        job=self.job(); record=dict(revision='reviewed', tracks=[dict(title='Song')])
        result=readiness(job,record,{},False,False)
        self.assertTrue(result['can_review']);self.assertFalse(result['can_publish'])
        self.assertEqual(result['blockers'][0]['code'],'publisher')
        for unknown,exists in [({'genres':['Unknown']},False),({},True)]:
            result=readiness(job,record,unknown,exists,True)
            self.assertFalse(result['can_review']);self.assertFalse(result['can_publish'])
        self.assertFalse(readiness(job,dict(record,revision='old'),{},False,True)['can_review'])
        self.assertFalse(readiness(job,record,{},False,True,True)['can_publish'])

    def test_queue_counts_cover_more_than_one_thousand_jobs(self):
        jobs=[dict(id=str(i),status='Needs review',album='Album '+str(i),artist='Artist',updated=i,
                   readiness=dict(can_review=False)) for i in range(1100)]
        jobs.append(dict(id='ready',status='Curated',album='Ready',artist='Artist',updated=1101,readiness=dict(can_review=True)))
        jobs.append(dict(id='history',status='Approved',album='History',artist='Artist',updated=1102,readiness=dict(can_review=False)))
        page,total,counts=queue_page(jobs,group='all',offset=1099,limit=10,sort='updated',direction=1)
        self.assertEqual(total,1101);self.assertEqual(counts,dict(attention=1100,ready=1,processing=0,history=1))
        self.assertEqual([j['id'] for j in page],['1099','ready'])
        page,total,_=queue_page(jobs,query='Ready',group='ready');self.assertEqual(total,1)

    def test_recovery_does_not_invent_match_counts(self):
        result=recovery(self.job('Needs review',reason='No audio output'))
        self.assertEqual(result['code'],'match');self.assertNotIn('matched_tracks',result)
        self.assertIn('No matched subset',result['action'])

    def test_provenance_only_links_valid_identifiers(self):
        job=self.job(release_id='not-a-link')
        self.assertEqual(release_identity(job,None)['release_ids'],[])
        self.assertEqual(release_identity(dict(job,mode='manual'),None)['provenance'],'User supplied')


class WorkingCopyUXTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Settings(self.temp.name);self.s.initialize();self.s.library.mkdir()

    def tearDown(self):self.temp.cleanup()

    def curate(self):
        for i in (1,2):track(self.s.incoming/f'{i}.flac',number=i,title=f'Song {i}')
        self.s.stable_seconds=0;scan(self.s)
        with connect(self.s) as db:sources=[dict(r) for r in db.execute('SELECT * FROM tracks ORDER BY track')]
        job=create_job(self.s,[s['id'] for s in sources],mode='manual')
        queue(self.s,job,dict(artist='Northbound',album='Signals',year=2024,
              tracks=[dict(id=s['id'],title=s['title'],artist=s['artist'],disc=1,track=s['track'],genres=['Rock'],tags=[]) for s in sources]))
        process_job(self.s,job)
        return Desk(self.s,'local-test-token'),job

    def test_artwork_edit_updates_every_track_and_invalidates_approval(self):
        from PIL import Image
        from mediafile import MediaFile
        desk,job=self.curate();d=desk.detail(job);before={s['path']:sha256(self.s.incoming/s['path']) for s in d['sources']}
        image=Image.new('RGB',(24,24),'orange');data=BytesIO();image.save(data,format='PNG')
        payload=dict(revision=d['review']['revision'],artist='Northbound',album='Signals',year=2024,
                     tracks=d['review']['tracks'],artwork=base64.b64encode(data.getvalue()).decode())
        desk.edit(job,payload);new=desk.detail(job)
        self.assertNotEqual(d['review']['revision'],new['review']['revision'])
        self.assertEqual(new['review']['mode'],'manual')
        images=[MediaFile(self.s.curated/job/'audio'/t['file']).images[0].data for t in new['review']['tracks']]
        self.assertEqual(images[0],images[1])
        self.assertEqual(before,{s['path']:sha256(self.s.incoming/s['path']) for s in d['sources']})
        self.assertEqual(list(self.s.library.iterdir()),[])
        with self.assertRaises(ValueError):desk.edit(job,payload)

    def test_unknown_genres_and_destinations_affect_counts(self):
        desk,job=self.curate();s=desk.snapshot(job_filter='all')
        self.assertEqual(s['review_counts']['attention'],1)
        save_policy(self.s,dict(genres=['Rock'],tags=[]))
        s=desk.snapshot(job_filter='ready');self.assertEqual(s['review_counts']['ready'],1)
        (self.s.library/s['jobs'][0]['destination']).mkdir(parents=True)
        s=desk.snapshot(job_filter='attention');self.assertEqual(s['review_counts']['ready'],0)
        self.assertEqual(s['review_counts']['attention'],1)

    def test_manual_grouping_accepts_untagged_sources_without_inventing_metadata(self):
        from mediafile import MediaFile
        path=track(self.s.incoming/'untagged.flac')
        media=MediaFile(path);media.albumartist='';media.album='';media.save()
        self.s.stable_seconds=0;scan(self.s)
        with connect(self.s) as db: source=dict(db.execute('SELECT * FROM tracks').fetchone())
        before=sha256(path);job=create_job(self.s,[source['id']],mode='manual')
        detail=Desk(self.s,'local-test-token').detail(job)
        self.assertEqual(detail['job']['status'],'Needs review')
        self.assertEqual(detail['job']['album'],'')
        self.assertFalse(detail['readiness']['can_publish'])
        with self.assertRaises(ValueError):queue(self.s,job,dict(artist='',album='',year=2024,tracks=[]))
        self.assertEqual(sha256(path),before);self.assertEqual(list(self.s.library.iterdir()),[])

    def test_stats_breakdown_and_coverage_are_read_only(self):
        from staging import statistics
        from library import scan_library
        from mediafile import MediaFile
        source=track(self.s.library/'Northbound/Signals (2024)/01 - Song.flac')
        media=MediaFile(source);media.genres=['Rock'];media.grouping='Workout';media.save()
        before=sha256(source);result=scan_library(self.s)
        self.assertEqual((result['with_genres'],result['with_tags'],result['audio_files']),(1,1,1))
        self.assertEqual(result['releases'][0]['genres'],['Rock']);self.assertEqual(sha256(source),before)
        (self.s.incoming/'file.txt').write_text('abc');(self.s.curated/'data.txt').write_text('defg')
        stats=statistics(self.s);self.assertEqual(stats['breakdown']['Incoming originals']['bytes'],3)
        self.assertEqual(stats['breakdown']['Curated copies']['bytes'],4)
        self.assertEqual(sum(v['bytes'] for v in stats['breakdown'].values()),stats['bytes'])


if __name__=='__main__':unittest.main()
