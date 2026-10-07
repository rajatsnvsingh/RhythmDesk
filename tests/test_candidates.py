import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from rhythm_candidates import RhythmCandidatesPlugin, read_candidates
from common import Settings, connect, atomic_json
from server import Desk
from worker import import_arguments

ID='65085f39-6482-44fd-8c34-a266475bedeb'


class CandidatesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.folder=Path(self.temp.name)
        info=SimpleNamespace(album_id=ID,artist='Eminem',album='Recovery',year=2010,
            country='US',media='CD',label='Label',catalognum='123',albumdisambig='',
            tracks=[SimpleNamespace(title='Cold Wind Blows',medium=1,medium_index=1,index=1,length=304)])
        self.match=SimpleNamespace(info=info,distance=.04,extra_items=[],extra_tracks=[])
        self.plugin=RhythmCandidatesPlugin()
        self.plugin.config['output']=str(self.folder/'CANDIDATES.json')
        self.plugin.config['selected']=''
        self.plugin.config['mode']='album'
        self.task=SimpleNamespace(is_album=True,candidates=[self.match],choice=None)
        self.task.set_choice=lambda choice:setattr(self.task,'choice',choice)

    def tearDown(self):
        self.temp.cleanup()

    def test_capture_does_not_override_confidence(self):
        self.plugin.choose(None,self.task)
        self.assertIsNone(self.task.choice)
        record=read_candidates(self.folder)[0]
        self.assertEqual(record['distance'],.04)
        self.assertEqual(record['tracks'][0]['title'],'Cold Wind Blows')

    def test_explicit_selection_accepts_only_exact_complete_candidate(self):
        self.plugin.config['selected']=ID
        self.plugin.choose(None,self.task)
        self.assertIs(self.task.choice,self.match)
        self.match.extra_tracks=[object()]
        with self.assertRaisesRegex(ValueError,'incomplete'):
            self.plugin.choose(None,self.task)
        self.match.extra_tracks=[]
        self.plugin.config['selected']='absent'
        with self.assertRaisesRegex(ValueError,'not returned'):
            self.plugin.choose(None,self.task)

    def test_legacy_log_candidates(self):
        records=read_candidates(self.folder,f'Candidate: Eminem - Recovery ({ID}) from MusicBrainz\nComputing...\nSuccess. Distance: 0.04')
        self.assertEqual(records[0]['release_id'],ID)
        self.assertIsNone(records[0]['missing'])

    def test_exact_release_uses_beets_search_id_not_move_flag(self):
        from beets.ui.commands.import_ import import_cmd
        arguments=import_arguments('album',ID,self.folder/'input')
        options,paths=import_cmd.parser.parse_args(arguments[1:])
        self.assertEqual(options.search_ids,[ID])
        self.assertTrue(options.copy)
        self.assertFalse(options.move)
        self.assertEqual(paths,[str(self.folder/'input')])
        self.assertNotIn('-m',arguments)
        for mode in ('album','partial'):
            options,paths=import_cmd.parser.parse_args(import_arguments(mode,ID,self.folder/'input')[1:])
            self.assertFalse(options.singletons)
            self.assertEqual(options.search_ids,[ID])
            self.assertFalse(options.move)
        for mode,release_id in [('single',ID),('partial','')]:
            options,paths=import_cmd.parser.parse_args(import_arguments(mode,release_id,self.folder/'input')[1:])
            self.assertTrue(options.singletons)
            self.assertFalse(options.search_ids)
            self.assertFalse(options.move)

    def test_selected_partial_keeps_candidate_with_unmatched_sources_for_review(self):
        self.plugin.config['selected']=ID
        self.plugin.config['mode']='partial'
        self.match.extra_tracks=[object(),object()]
        self.plugin.choose(None,self.task)
        self.assertIs(self.task.choice,self.match)
        self.match.extra_items=[object()]
        self.plugin.choose(None,self.task)
        self.assertIs(self.task.choice,self.match)
        self.plugin.config['mode']='album'
        with self.assertRaisesRegex(ValueError,'unmatched source'):self.plugin.choose(None,self.task)

    def test_retry_selected_partial_keeps_mode_and_rejects_complete(self):
        settings=Settings(self.folder);settings.initialize()
        with connect(settings) as db:
            db.execute("INSERT INTO jobs(id,status,artist,album,year,track_count,mode,release_id,reason,updated) VALUES('job','Needs review','Eminem','Recovery',2010,1,'album','','',0)")
        folder=settings.review/'job';folder.mkdir()
        atomic_json(folder/'CANDIDATES.json',[dict(release_id=ID,missing=16,unmatched=0)])
        desk=Desk(settings,'test-token')
        with self.assertRaises(ValueError):desk.retry('job',ID,mode='album',selected_candidate=ID)
        desk.retry('job',ID,mode='partial',selected_candidate=ID)
        with connect(settings) as db:
            job=db.execute("SELECT * FROM jobs WHERE id='job'").fetchone()
            self.assertEqual(job['mode'],'partial');self.assertEqual(job['status'],'Queued')
        self.assertFalse(settings.library.exists())

    def test_partial_files_capture_source_identity_and_only_actual_match_evidence(self):
        self.plugin.config['mode']='partial'
        item=SimpleNamespace(path=str(self.folder/'audio'/'mapped.flac').encode())
        task=SimpleNamespace(old_paths=[str(self.folder/'input'/'0001.flac').encode()],match=self.match,imported_items=lambda:[item])
        self.plugin.files(None,task)
        evidence=json.loads((self.folder/'PARTIAL-MAP.json').read_text())
        self.assertTrue(evidence['0001.flac']['matched'])
        self.assertEqual(evidence['0001.flac']['file'],str(self.folder/'audio'/'mapped.flac'))
        task.match=None;self.plugin.files(None,task)
        self.assertFalse(json.loads((self.folder/'PARTIAL-MAP.json').read_text())['0001.flac']['matched'])

    def test_zero_mapped_partial_skips_empty_beets_album_for_original_retention(self):
        from beets.importer import Action
        self.plugin.config['mode']='partial';self.plugin.config['selected']=ID
        self.match.mapping={};self.match.extra_items=[object()]
        self.plugin.choose(None,self.task)
        self.assertEqual(self.task.choice,Action.SKIP)

    def test_retry_rejects_unlisted_candidate_and_only_queues(self):
        settings=Settings(self.folder);settings.initialize()
        with connect(settings) as db:
            db.execute("INSERT INTO jobs(id,status,artist,album,year,track_count,mode,release_id,reason,updated) VALUES('job','Needs review','Eminem','Recovery',2010,1,'album','','',0)")
        folder=settings.review/'job';folder.mkdir()
        atomic_json(folder/'CANDIDATES.json',[dict(release_id=ID,missing=0,unmatched=0)])
        desk=Desk(settings,'test-token')
        with self.assertRaisesRegex(ValueError,'unavailable'):
            desk.retry('job','262dc55e-d4fe-487f-bc31-50c040c098d9',mode='album',selected_candidate='262dc55e-d4fe-487f-bc31-50c040c098d9')
        desk.retry('job',ID,mode='album',selected_candidate=ID)
        with connect(settings) as db:
            self.assertEqual(db.execute("SELECT status FROM jobs WHERE id='job'").fetchone()[0],'Queued')
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='candidate:job'").fetchone()[0],ID)
        self.assertFalse(settings.library.exists())
