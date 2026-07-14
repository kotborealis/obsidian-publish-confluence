from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from obsidian_publish_confluence.convert import collect_attachments


class ConvertTests(unittest.TestCase):
    def test_obsidian_image_embed_is_converted_and_attached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            image = Path(tmp) / "image.png"
            note.write_text("![[image.png]]\n", encoding="utf-8")
            image.write_bytes(b"png")

            result = collect_attachments(str(note), None)

            self.assertIn('ri:attachment ri:filename="image.png"', result["body"])
            self.assertEqual([attachment["name"] for attachment in result["attachments"]], ["image.png"])

    def test_non_image_wikilink_stays_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("![[some-note]]\n", encoding="utf-8")

            result = collect_attachments(str(note), None)

            self.assertIn("![[some-note]]", result["body"])
            self.assertEqual(result["attachments"], [])


if __name__ == "__main__":
    unittest.main()
