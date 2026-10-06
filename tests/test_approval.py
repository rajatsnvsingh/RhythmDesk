import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
import common as approval


class InventoryTests(unittest.TestCase):
    def test_audio_changes_invalidate_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            track = root / '01 - Song.flac'
            track.write_bytes(b'original')
            reviewed = approval.inventory(root)
            track.write_bytes(b'changed tags')
            self.assertNotEqual(reviewed, approval.inventory(root))

    def test_external_art_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '01 - Song.flac').write_bytes(b'audio')
            (root / 'cover.jpg').write_bytes(b'artwork')
            with self.assertRaises(ValueError):
                approval.inventory(root)

    def test_empty_output_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                approval.inventory(Path(directory))


if __name__ == '__main__':
    unittest.main()
