"""Keep the documentation's local links and illustration assets intact."""
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_local_links_resolve(self):
        documents = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
        for document in documents:
            contents = document.read_text(encoding="utf-8")
            contents = re.sub(r"```.*?```", "", contents, flags=re.S)
            links = re.findall(r"\]\(([^\s)]+)\)", contents)
            links += re.findall(r'(?:src|href)="([^"]+)"', contents)
            for link in links:
                if link.startswith(("https://", "http://", "#")):
                    continue
                target = (document.parent / link.split("#", 1)[0]).resolve()
                with self.subTest(document=document.name, link=link):
                    self.assertTrue(target.is_relative_to(ROOT))
                    self.assertTrue(target.is_file(), f"Missing target: {target}")

    def test_banner_is_self_contained_svg(self):
        root = ET.parse(ROOT / "docs/assets/banner.svg").getroot()
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertEqual(root.attrib["viewBox"], "0 0 1440 430")
        for element in root.iter():
            self.assertNotIn(element.tag.split("}")[-1], ("script", "foreignObject"))
            for name, value in element.attrib.items():
                if name.endswith("href"):
                    self.assertTrue(value.startswith("#"))

    def test_screenshots_are_jpegs(self):
        screenshots = sorted((ROOT / "docs/assets/screenshots").glob("*.jpg"))
        required = {'overview.jpg', 'incoming.jpg', 'inspector.jpg', 'manual-curation.jpg',
                    'mobile-review.jpg', 'music-drawer.jpg', 'mobile-more.jpg'}
        self.assertTrue(required <= {screenshot.name for screenshot in screenshots})
        for screenshot in screenshots:
            with self.subTest(screenshot=screenshot.name):
                data = screenshot.read_bytes()
                self.assertTrue(data.startswith(b"\xff\xd8"))
                self.assertTrue(data.endswith(b"\xff\xd9"))


if __name__ == "__main__":
    unittest.main()
