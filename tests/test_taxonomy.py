import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from common import Settings
from taxonomy import save_policy, validate_audio
from fixtures import track
from mediafile import MediaFile
from library import scan_library

class TaxonomyTests(unittest.TestCase):
    def test_unknown_labels_block_and_can_be_allowed_or_removed(self):
        with tempfile.TemporaryDirectory() as root:
            s=Settings(root);s.initialize();s.library.mkdir()
            audio=s.curated/'audio';p=audio/'Artist/Album (2024)/01 - Track.flac'
            track(p)
            m=MediaFile(p);m.genres=['Rock','Unknown'];m.grouping='Workout; New';m.save()
            with self.assertRaises(ValueError):validate_audio(s,audio)
            from common import review_record, atomic_json
            from publisher import publish
            job=s.curated/'work.test'
            job.mkdir()
            import shutil
            shutil.copytree(audio,job/'audio')
            record=review_record(job/'audio')
            atomic_json(job/'REVIEW.json',record)
            with self.assertRaisesRegex(ValueError,'Unapproved'):
                publish(s,'work.test',record['revision'])
            self.assertEqual(list(s.library.iterdir()),[])
            save_policy(s,dict(genres=['rock','Unknown'],tags=['Workout','New']))
            validate_audio(s,audio)
            save_policy(s,dict(genres=['Rock'],tags=['Workout']))
            with self.assertRaises(ValueError):validate_audio(s,audio)
            m=MediaFile(p);m.genres=['Rock'];m.grouping='Workout';m.save()
            validate_audio(s,audio)
            import shutil
            shutil.copytree(audio,s.library,dirs_exist_ok=True)
            stats=scan_library(s)
            self.assertEqual((stats['genres'],stats['tags']),(1,1))
