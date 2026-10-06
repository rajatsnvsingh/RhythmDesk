import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from common import Settings,connect
from fixtures import track
from worker import scan,create_job
from trackmatch import resolve_release
from mediafile import MediaFile

class ModeTests(unittest.TestCase):
    def test_auto_single(self):
        with tempfile.TemporaryDirectory() as root:
            s=Settings(root);s.initialize();track(s.incoming/'one.flac');scan(s)
            with connect(s) as db:ids=[r['id'] for r in db.execute('SELECT id FROM tracks')]
            job=create_job(s,ids)
            with connect(s) as db:self.assertEqual(db.execute('SELECT mode FROM jobs WHERE id=?',(job,)).fetchone()['mode'],'single')

    def test_recording_release_resolution_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as root:
            p=track(Path(root)/'one.flac');m=MediaFile(p);m.mb_trackid='11111111-1111-4111-8111-111111111111';m.save()
            release={'id':'22222222-2222-4222-8222-222222222222','title':'Signals','date':'2024-01-01','artist-credit':[{'name':'Northbound'}], 'media':[{'position':1,'tracks':[{'position':3,'title':'Matched Title','recording':{'id':m.mb_trackid}}]}]}
            def fetch(kind,*args):return {'releases':[release]} if kind=='recording' else release
            resolve_release([p],'Signals',fetch=fetch)
            self.assertEqual(MediaFile(p).title,'Matched Title');self.assertEqual(MediaFile(p).track,3)
            def ambiguous(kind,*args):return {'releases':[release,dict(release,id='33333333-3333-4333-8333-333333333333')]}
            with self.assertRaisesRegex(ValueError,'ambiguous'):resolve_release([p],'Signals',fetch=ambiguous)
