"""Validate actual image bytes rather than trusting the presence of a tag."""
from io import BytesIO
from PIL import Image


def usable_image(media):
    for image in media.images or []:
        try:
            with Image.open(BytesIO(image.data)) as decoded:
                decoded.verify()
            return image
        except (OSError, ValueError, SyntaxError):
            continue
    return None
