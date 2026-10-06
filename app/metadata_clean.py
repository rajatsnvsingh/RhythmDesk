"""Remove distributor website watermarks from disposable matching metadata."""
import re

ADDRESS=re.compile(r'(?<![\w@])(?:https?://[^\s<>\[\]()]+|www\.[a-z0-9.-]+(?:/[^\s<>\[\]()]*)?|(?:[a-z0-9-]+\.)+(?:com|pk|in|net|org|info|co\.uk)(?:/[^\s<>\[\]()]*)?)(?![\w.])',re.I)


def clean(value):
    original=str(value or '')
    if not ADDRESS.search(original):return original
    text=ADDRESS.sub('',original)
    text=re.sub(r'[\[(]\s*[\])]', '',text)
    text=re.sub(r'\s+[-|–—]\s+(?=\s|$)',' ',text)
    text=re.sub(r'\s+', ' ',text).strip(' \t-|–—')
    # Never erase an entire title/artist: ask for manual metadata instead.
    return text or original
