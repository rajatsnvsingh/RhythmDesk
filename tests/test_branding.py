import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from PIL import Image
from branding import asset
from common import Settings, connect
from server import Desk, Handler, ThreadingHTTPServer


class BrandingTests(unittest.TestCase):
    def test_vector_is_small_self_contained_geometry(self):
        data, mime = asset('/icon.svg')
        self.assertEqual(mime, 'image/svg+xml')
        root = ElementTree.fromstring(data)
        self.assertEqual(root.attrib['viewBox'], '0 0 64 64')
        self.assertLess(len(data), 1000)
        self.assertNotIn(b'href', data)
        self.assertNotIn(b'script', data)

    def test_raster_icons_and_favicon_are_decodable(self):
        for path, size in [('/icon-192.png', 192), ('/icon-512.png', 512), ('/apple-touch-icon.png', 180), ('/favicon.ico', 48)]:
            data, mime = asset(path)
            image = Image.open(io.BytesIO(data))
            image.load()
            self.assertEqual(image.size, (size, size))
            self.assertIn(mime, ('image/png', 'image/x-icon'))
            # Record hub is ink; the label ring is mint in both renderers.
            # Small ICO sizes are antialiased; test the dark hub, not an exact byte.
            self.assertLess(max(image.convert('RGB').getpixel((round(33*size/64), round(28*size/64)))), 60)
        apple = Image.open(io.BytesIO(asset('/apple-touch-icon.png')[0]))
        self.assertEqual(apple.getpixel((0, 0)), (123, 223, 181, 255))

    def test_manifest_has_only_same_origin_public_assets(self):
        data, mime = asset('/manifest.webmanifest')
        manifest = json.loads(data)
        self.assertEqual(mime, 'application/manifest+json')
        self.assertEqual(manifest['name'], 'Rhythm Desk')
        self.assertEqual(manifest['start_url'], '/')
        for icon in manifest['icons']:
            self.assertEqual(asset(icon['src'])[1], 'image/png')
        for path in ['/icon-16.png', '/../.env', '/icon.svg/secret', '/app/branding.py']:
            self.assertIsNone(asset(path))

    def test_public_icon_routes_do_not_grant_a_session(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Settings(root)
            settings.initialize()
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.desk = Desk(settings, 'test-token-for-protected-ui')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                for path in ['/icon.svg', '/icon-192.png', '/icon-512.png', '/apple-touch-icon.png', '/favicon.ico', '/manifest.webmanifest']:
                    with urllib.request.urlopen(base+path) as response:
                        expected, mime = asset(path)
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers['Content-Type'], mime)
                        self.assertEqual(response.headers['Content-Length'], str(len(expected)))
                        self.assertEqual(response.read(), expected)
                        self.assertIsNone(response.headers.get('Set-Cookie'))
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base+'/api/snapshot')
                self.assertEqual(error.exception.code, 401)
                with connect(settings) as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM web_sessions').fetchone()[0], 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_more_is_a_page_and_zero_badges_start_hidden(self):
        html = (Path(__file__).parents[1] / 'app/static/index.html').read_text(encoding='utf-8')
        self.assertIn('<section id="more" class="view" hidden>', html)
        self.assertNotIn('more-dialog', html)
        for name in ('nav-incoming', 'nav-jobs'):
            self.assertIn(f'id="{name}" class="nav-count" aria-hidden="true" hidden>', html)
