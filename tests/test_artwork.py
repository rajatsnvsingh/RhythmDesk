import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from artwork import usable_image
from mediafile import MediaFile, Image
from fixtures import track
from common import Settings
from worker import artwork_config

class ArtworkTests(unittest.TestCase):
    def test_invalid_image_and_force_override(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root);work=folder/'input'
            p=track(work/'one.flac')
            s=Settings(root);s.config=folder/'base.yaml';s.config.write_text('plugins: fetchart embedart\n')
            self.assertIsNotNone(usable_image(MediaFile(p)))
            self.assertTrue(artwork_config(s,folder,work)[1])
            self.assertFalse(artwork_config(s,folder,work,True)[1])
            from types import SimpleNamespace
            self.assertIsNone(usable_image(SimpleNamespace(images=[SimpleNamespace(data=b'broken image')])))
            media=MediaFile(p);media.images=[];media.save()
            self.assertFalse(artwork_config(s,folder,work)[1])
