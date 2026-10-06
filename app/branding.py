"""Hand-built record/R monogram. Public branding only, with no deployment data.

Keeping the vector and raster renderers in source means branding also deploys
through the existing source-only SSH boundary; no binary upload access is needed.
"""
from functools import lru_cache
import io
import json

MINT = '#7bdfb5'
INK = '#112c22'
ASSET_PATHS = frozenset(('/icon.svg', '/icon-192.png', '/icon-512.png',
                         '/apple-touch-icon.png', '/favicon.ico', '/manifest.webmanifest'))
SVG = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="{MINT}"/>
<g fill="{INK}">
<rect x="17" y="14" width="8" height="36"/>
<circle cx="33" cy="28" r="14"/>
<path d="M30 36 39 34 51 50H40Z"/>
</g>
<circle cx="33" cy="28" r="6" fill="{MINT}"/>
<circle cx="33" cy="28" r="2" fill="{INK}"/>
</svg>'''.encode()


def raster(size, square=False):
    from PIL import Image, ImageDraw
    scale = size * 4 / 64
    image = Image.new('RGBA', (size * 4, size * 4), MINT if square else (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    def box(values):
        return tuple(round(value * scale) for value in values)
    if not square:
        draw.rounded_rectangle((0, 0, size * 4 - 1, size * 4 - 1), radius=14 * scale, fill=MINT)
    draw.rectangle(box((17, 14, 25, 50)), fill=INK)
    draw.ellipse(box((19, 14, 47, 42)), fill=INK)
    draw.polygon([box(point) for point in ((30, 36), (39, 34), (51, 50), (40, 50))], fill=INK)
    draw.ellipse(box((27, 22, 39, 34)), fill=MINT)
    draw.ellipse(box((31, 26, 35, 30)), fill=INK)
    return image.resize((size, size), Image.Resampling.LANCZOS)


@lru_cache(maxsize=6)
def asset(path):
    """Return only fixed public assets. Unknown names never become file paths."""
    if path == '/icon.svg':
        return SVG, 'image/svg+xml'
    if path == '/manifest.webmanifest':
        manifest = dict(name='Rhythm Desk', short_name='Rhythm Desk', start_url='/', scope='/',
                        display='standalone', background_color='#10161a', theme_color='#10161a',
                        description='Your music, deliberately curated.',
                        icons=[dict(src=f'/icon-{size}.png', sizes=f'{size}x{size}', type='image/png', purpose='any')
                               for size in (192, 512)])
        return json.dumps(manifest).encode(), 'application/manifest+json'
    sizes = {'/icon-192.png': 192, '/icon-512.png': 512, '/apple-touch-icon.png': 180, '/favicon.ico': 48}
    if path not in sizes:
        return None
    output = io.BytesIO()
    image = raster(sizes[path], square=path == '/apple-touch-icon.png')
    if path == '/favicon.ico':
        image.save(output, format='ICO', sizes=[(16, 16), (32, 32), (48, 48)])
        return output.getvalue(), 'image/x-icon'
    image.save(output, format='PNG')
    return output.getvalue(), 'image/png'
