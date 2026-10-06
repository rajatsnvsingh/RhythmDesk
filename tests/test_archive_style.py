"""Guard the archive's readable palette and dependency-free presentation."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def luminance(colour):
    values = [int(colour[i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
    return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))


class ArchiveStyleTests(unittest.TestCase):
    def test_text_and_semantic_colours_meet_normal_text_contrast(self):
        css = (ROOT / 'app/static/archive.css').read_text(encoding='utf-8')
        tokens = dict(re.findall(r'--([a-z]+):\s*(#[0-9a-f]{6})', css))
        for foreground in ('text', 'muted', 'accent', 'amber', 'red'):
            for background in ('bg', 'panel'):
                with self.subTest(foreground=foreground, background=background):
                    first, second = sorted((luminance(tokens[foreground]), luminance(tokens[background])))
                    self.assertGreaterEqual((second + .05) / (first + .05), 4.5)

    def test_skin_is_loaded_last_without_external_fonts_or_decorative_motion(self):
        html = (ROOT / 'app/static/index.html').read_text(encoding='utf-8')
        css = (ROOT / 'app/static/archive.css').read_text(encoding='utf-8')
        self.assertGreater(html.index('href="/archive.css"'), html.index('href="/mobile.css"'))
        for forbidden in ('@import', 'url(', 'animation:', 'gradient('):
            self.assertNotIn(forbidden, css)
        self.assertIn('NO ART', (ROOT / 'app/static/app.js').read_text(encoding='utf-8'))
        self.assertIn('NO ART', (ROOT / 'app/static/review-tools.js').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
