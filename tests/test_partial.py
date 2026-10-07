import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from fixtures import track
from mediafile import MediaFile
from common import Settings,atomic_json,connect,review_record,revision,sha256
from partial import prepare
from worker import normalize_output
from publisher import publish
from server import Desk
from ux import readiness

ID='65085f39-6482-44fd-8c34-a266475bedeb'
JOB='work.1234567890abcdef'


class PartialReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.settings=Settings(self.temp.name);self.settings.initialize();self.settings.library.mkdir()
        self.folder=self.settings.curated/JOB;self.folder.mkdir();(self.folder/'audio').mkdir()
        self.sources=[track(self.folder/'originals'/f'{i:04d}.flac',number=i,title=f'Original {i}') for i in (1,2)]
        self.hashes=[sha256(p) for p in self.sources]
        self.rows=[dict(id=f'source-{i}',path=f'{i}.flac',artist='Northbound',title=f'Original {i}',disc=1,track=i) for i in (1,2)]
        self.job=dict(artist='Northbound',album='Signals',year=2024,release_id=ID)

    def tearDown(self):self.temp.cleanup()

    def mixed(self):
        output=self.folder/'audio'/'matched.flac';shutil.copy2(self.sources[0],output)
        media=MediaFile(output);media.title='Catalogue title';media.mb_albumid=ID;media.track=2;media.save()
        atomic_json(self.folder/'PARTIAL-MAP.json',{'0001.flac':dict(file=str(output),matched=True)})
        identities=prepare(self.folder,self.job,self.rows,lambda *args:None)
        normalize_output(self.folder/'audio');record=review_record(self.folder/'audio',2)
        for t in record['tracks']:t.update(identities[(t['disc'],t['track'])])
        record['mode']='partial';record['revision']=revision(record)
        atomic_json(self.folder/'REVIEW.json',record)
        with connect(self.settings) as db:
            db.execute("INSERT INTO jobs(id,status,artist,album,year,track_count,mode,release_id,reason,revision,destination,updated) VALUES(?,'Curated','Northbound','Signals',2024,2,'partial',?,'',?,?,0)",(JOB,ID,record['revision'],record['destination']))
        return record

    def test_matches_survive_and_unmatched_original_has_no_false_release_identity(self):
        record=self.mixed();byid={t['source_id']:t for t in record['tracks']}
        self.assertEqual(byid['source-1']['title'],'Catalogue title');self.assertEqual(byid['source-1']['match_status'],'catalogue')
        self.assertEqual(byid['source-2']['title'],'Original 2');self.assertEqual(byid['source-2']['match_status'],'unverified')
        self.assertFalse(byid['source-2']['release_id']);self.assertEqual(len({(t['disc'],t['track']) for t in record['tracks']}),2)
        self.assertIn('provisional',byid['source-2']['match_note'])
        self.assertEqual([sha256(p) for p in self.sources],self.hashes)

    def test_zero_matches_still_produce_reviewable_tracks(self):
        identities=prepare(self.folder,self.job,self.rows,lambda *args:None)
        self.assertEqual(len(identities),2);self.assertTrue(all(e['match_status']=='unverified' for e in identities.values()))
        normalize_output(self.folder/'audio');self.assertEqual(len(review_record(self.folder/'audio',2)['tracks']),2)

    def test_web_and_isolated_publisher_block_until_explicit_track_confirmation(self):
        record=self.mixed();desk=Desk(self.settings,'token')
        with patch.object(desk,'schedule_publication') as schedule:
            with self.assertRaisesRegex(ValueError,'unresolved track'):desk.approve(JOB,record['revision'])
            schedule.assert_not_called()
        with self.assertRaisesRegex(ValueError,'unresolved track'):publish(self.settings,JOB,record['revision'])
        state=readiness(dict(status='Curated',revision=record['revision']),record,{},False,True)
        self.assertTrue(state['can_review']);self.assertFalse(state['can_publish'])
        payload=dict(artist='Northbound',album='Signals',year=2024,revision=record['revision'],tracks=[dict(t) for t in record['tracks']])
        # Saving unrelated corrections must not resolve catalogue uncertainty.
        desk.edit(JOB,payload)
        record=json.loads((self.folder/'REVIEW.json').read_text());self.assertTrue(any(t['match_status']=='unverified' for t in record['tracks']))
        payload['revision']=record['revision'];payload['tracks']=[dict(t,reviewed=True) for t in record['tracks']]
        desk.edit(JOB,payload)
        final=json.loads((self.folder/'REVIEW.json').read_text())
        self.assertEqual({t['match_status'] for t in final['tracks']},{'catalogue','user-confirmed'})
        self.assertEqual({t['source_id'] for t in final['tracks']},{'source-1','source-2'})
        self.assertNotEqual(final['revision'],record['revision'])
        receipt=publish(self.settings,JOB,final['revision']);self.assertEqual(len(receipt['files']),2)
