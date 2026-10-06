import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from mediafile import MediaFile
from common import Settings,connect,sha256
from fixtures import track
from manual import queue,cover
from metadata_clean import clean
from processing import read
from worker import scan,create_job,process_job
from taxonomy import unknown


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=Settings(self.temp.name);self.s.initialize();self.s.library.mkdir()
        self.sources=[track(self.s.incoming/'a.flac'),track(self.s.incoming/'b.flac',title='Second',number=2)]
        scan(self.s)
        with connect(self.s) as db:self.rows=[dict(r) for r in db.execute('SELECT * FROM tracks ORDER BY track')]
        self.job=create_job(self.s,[r['id'] for r in self.rows],mode='manual')
        self.payload=dict(artist='Various Artists',album='Film / Soundtrack',year=2013,
            tracks=[dict(id=r['id'],artist=r['artist'],title=r['title'],track=r['track'],disc=1,genres='Bollywood',tags='') for r in self.rows])

    def test_manual_preserves_originals_and_requires_approval(self):
        hashes=[sha256(p) for p in self.sources]
        output=BytesIO();Image.new('RGB',(80,80),'red').save(output,format='PNG')
        self.payload['artwork']=base64.b64encode(output.getvalue()).decode()
        queue(self.s,self.job,self.payload)
        with patch('worker.subprocess.Popen',side_effect=AssertionError('Must not run Beets')):
            process_job(self.s,self.job)
        with connect(self.s) as db:job=dict(db.execute('SELECT * FROM jobs WHERE id=?',(self.job,)).fetchone())
        self.assertEqual(job['status'],'Curated');self.assertEqual(job['mode'],'manual')
        self.assertEqual(hashes,[sha256(p) for p in self.sources]);self.assertEqual(list(self.s.library.iterdir()),[])
        record=json.loads((self.s.curated/self.job/'REVIEW.json').read_text())
        images=[MediaFile(self.s.curated/self.job/'audio'/t['file']).images[0].data for t in record['tracks']]
        self.assertEqual(images[0],images[1]);self.assertEqual(record['mode'],'manual')
        self.assertEqual(unknown(self.s,record['tracks'])['genres'],['Bollywood'])
        self.assertEqual(read(self.s,self.job)['phase'],'Curated')
        self.assertEqual(record['destination'],'Various Artists/Film _ Soundtrack (2013)')
        self.assertEqual({t['source_id'] for t in record['tracks']},{r['id'] for r in self.rows})

    def test_manual_keeps_existing_covers(self):
        originals=[MediaFile(p).images[0].data for p in self.sources]
        queue(self.s,self.job,self.payload);process_job(self.s,self.job)
        record=json.loads((self.s.curated/self.job/'REVIEW.json').read_text())
        self.assertEqual([MediaFile(self.s.curated/self.job/'audio'/t['file']).images[0].data for t in record['tracks']],originals)

    def test_invalid_art_and_duplicate_positions_refused(self):
        with self.assertRaises(ValueError):cover(base64.b64encode(b'not an image').decode())
        self.payload['tracks'][1]['track']=1
        with self.assertRaisesRegex(ValueError,'Duplicate'):queue(self.s,self.job,self.payload)

    def test_changed_source_blocks_manual_preparation(self):
        queue(self.s,self.job,self.payload)
        with self.sources[0].open('ab') as stream:stream.write(b'changed')
        process_job(self.s,self.job)
        with connect(self.s) as db:self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(self.job,)).fetchone()['status'],'Needs review')
        self.assertEqual(list(self.s.library.iterdir()),[])
        self.assertEqual(read(self.s,self.job)['phase'],'Needs review')

    def test_cannot_edit_approved_job(self):
        with connect(self.s) as db:db.execute("UPDATE jobs SET status='Approved' WHERE id=?",(self.job,))
        with self.assertRaises(ValueError):queue(self.s,self.job,self.payload)

    def test_manual_revision_and_edit_preserve_identity(self):
        from server import Desk
        queue(self.s,self.job,self.payload);process_job(self.s,self.job)
        record=json.loads((self.s.curated/self.job/'REVIEW.json').read_text())
        edits=[dict(t,title=t['title']+' corrected') for t in record['tracks']]
        Desk(self.s,'').edit(self.job,dict(revision=record['revision'],artist='Various Artists',album='Film',year=2013,tracks=edits))
        new=json.loads((self.s.curated/self.job/'REVIEW.json').read_text())
        self.assertEqual({t['source_id'] for t in new['tracks']},{r['id'] for r in self.rows})
        with self.assertRaisesRegex(ValueError,'changed'):queue(self.s,self.job,dict(self.payload,revision=record['revision']))


class MetadataCleanTests(unittest.TestCase):
    def test_website_watermarks(self):
        for value in ['Bezubaan - MP3Khan.Com','Bezubaan [songs.pk]','Bezubaan (www.djpunjab.com)','https://songs.pk Bezubaan','Bezubaan | djpunjab.com']:
            self.assertEqual(clean(value),'Bezubaan')
        self.assertEqual(clean('ABCD - songs.pk'),'ABCD')
        self.assertEqual(clean('will.i.am'),'will.i.am')
        self.assertEqual(clean('Mr. Brightside'),'Mr. Brightside')
        self.assertEqual(clean('AC/DC'),'AC/DC')
