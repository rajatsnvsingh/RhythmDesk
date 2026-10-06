"""Real, tiny tagged FLAC fixtures. No MusicBrainz requests."""
from io import BytesIO
from pathlib import Path
import subprocess
from mediafile import MediaFile, Image
from PIL import Image as PillowImage, ImageDraw


def track(path, artist='Northbound', album='Signals', title='First Light', number=1, seconds=12, tagged=True, art=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', f'sine=frequency={220 + number * 40}:sample_rate=44100',
                    '-t', str(seconds), '-c:a', 'flac', '-y', str(path)], check=True)
    if tagged:
        media = MediaFile(path)
        media.artist = artist
        media.albumartist = artist
        media.album = album
        media.title = title
        media.track = number
        media.disc = 1
        media.year = 2024
        if art:
            image = PillowImage.new('RGB', (300, 300), '#19312f')
            drawing = ImageDraw.Draw(image)
            for i in range(12):
                drawing.line([(0, i * 24), (300, 300 - i * 16)], fill='#70dbaa', width=2)
            buffer = BytesIO()
            image.save(buffer, format='JPEG')
            media.images = [Image(buffer.getvalue())]
        media.save()
    return path
