from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from obsidian_publish_confluence.convert import ConvertResult, collect_attachments


class ConvertTests(unittest.TestCase):
    def test_obsidian_image_embed_is_converted_and_attached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            image = Path(tmp) / "image.png"
            note.write_text("![[image.png]]\n", encoding="utf-8")
            image.write_bytes(b"png")

            result: ConvertResult = collect_attachments(str(note), None)

            attachment_names = [attachment["name"] for attachment in result["attachments"]]
            self.assertEqual(len(attachment_names), 1)
            self.assertIn('ri:attachment ri:filename="', result["body"])
            self.assertIn(attachment_names[0], result["body"])
            self.assertTrue(attachment_names[0].endswith("-image.png"))

    def test_non_image_wikilink_stays_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("![[some-note]]\n", encoding="utf-8")

            result: ConvertResult = collect_attachments(str(note), None)

            self.assertIn("![[some-note]]", result["body"])
            self.assertEqual(result["attachments"], [])

    def test_plantuml_block_becomes_confluence_macro_without_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text(
                "```plantuml\n@startuml\nAlice -> Bob: ping\n@enduml\n```\n",
                encoding="utf-8",
            )

            result: ConvertResult = collect_attachments(str(note), None)

            self.assertIn('ac:name="plantuml"', result["body"])
            self.assertIn(
                'ac:name="atlassian-macro-output-type">INLINE</ac:parameter>', result["body"]
            )
            self.assertIn("@startuml\nAlice -> Bob: ping\n@enduml", result["body"])
            self.assertEqual(result["attachments"], [])

    def test_image_width_is_preserved_in_confluence_markup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            image = Path(tmp) / "image.png"
            note.write_text("![[image.png|640]]\n", encoding="utf-8")
            image.write_bytes(b"png")

            result: ConvertResult = collect_attachments(str(note), None)

            self.assertIn('ac:width="640"', result["body"])

    def test_same_basename_from_different_paths_gets_different_attachment_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            a_dir = Path(tmp) / "a"
            b_dir = Path(tmp) / "b"
            a_dir.mkdir()
            b_dir.mkdir()
            (a_dir / "logo.png").write_bytes(b"a")
            (b_dir / "logo.png").write_bytes(b"b")
            note.write_text("![[a/logo.png]]\n![[b/logo.png]]\n", encoding="utf-8")

            result: ConvertResult = collect_attachments(str(note), None)

            attachment_names = [attachment["name"] for attachment in result["attachments"]]
            self.assertEqual(len(attachment_names), 2)
            self.assertNotEqual(attachment_names[0], attachment_names[1])
            for name in attachment_names:
                self.assertIn(name, result["body"])


if __name__ == "__main__":
    unittest.main()
