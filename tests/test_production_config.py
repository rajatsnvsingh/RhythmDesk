import unittest
from pathlib import Path
import yaml

class ProductionConfigTests(unittest.TestCase):
    def test_musicbrainz_and_artwork_configuration(self):
        config=yaml.safe_load((Path(__file__).parents[1]/'config/config.yaml').read_text())
        self.assertIn('musicbrainz',config['plugins'].split())
        self.assertEqual(config['fetchart']['enforce_ratio'],'20%')
        self.assertEqual(config['import']['quiet_fallback'],'skip')
        self.assertEqual(config['match']['strong_rec_thresh'],0.03)
