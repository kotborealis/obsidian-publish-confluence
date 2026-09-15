from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from obsidian_publish_confluence.convert import (
    ConvertResult,
    collect_attachments,
    render_canvas_svg,
)


class ConvertTests(unittest.TestCase):
    def test_fenced_code_block_becomes_confluence_code_macro(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text(
                "```yaml\nextensions:\n\tprovidesServices:\n\t  - test_service\n```\n",
                encoding="utf-8",
            )

            result: ConvertResult = collect_attachments(str(note), None)

            self.assertIn('<ac:structured-macro ac:name="code"', result["body"])
            self.assertIn('<ac:parameter ac:name="language">yaml</ac:parameter>', result["body"])
            self.assertIn("providesServices:", result["body"])
            self.assertNotIn("<pre", result["body"])

    def test_checkbox_list_becomes_confluence_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("* [ ] todo\n* [x] done\n", encoding="utf-8")

            result: ConvertResult = collect_attachments(str(note), None)

            body = result["body"]
            self.assertEqual(body.count("<ac:task>"), 2)
            self.assertIn("<ac:task-id>1</ac:task-id>", body)
            self.assertIn("<ac:task-id>2</ac:task-id>", body)
            self.assertIn("<ac:task-status>incomplete</ac:task-status>", body)
            self.assertIn("<ac:task-status>complete</ac:task-status>", body)
            self.assertIn("<ac:task-body>todo</ac:task-body>", body)
            self.assertIn("<ac:task-body>done</ac:task-body>", body)
            self.assertNotIn("<ac:task-body>[", body)

    def test_nested_checkbox_lists_become_nested_confluence_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text(
                "* [ ] parent [link](https://example.com)\n    * [x] child\n",
                encoding="utf-8",
            )

            result: ConvertResult = collect_attachments(str(note), None)

            body = result["body"]
            self.assertEqual(body.count("<ac:task-list>"), 2)
            self.assertEqual(body.count("<ac:task>"), 2)
            self.assertIn('<a href="https://example.com">link</a>', body)
            self.assertLess(body.index("<ac:task-id>1"), body.index("<ac:task-id>2"))
            self.assertNotIn("[ ]", body)
            self.assertNotIn("[x]", body)

    def test_checkbox_in_code_block_stays_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("```text\n* [ ] not a task\n```\n", encoding="utf-8")

            result: ConvertResult = collect_attachments(str(note), None)

            self.assertEqual(result["body"].count("<ac:task>"), 0)
            self.assertIn("* [ ] not a task", result["body"])

    def test_frontmatter_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text(
                "---\ntitle: Hidden title\ntags:\n  - internal\n---\n\n# Published title\n",
                encoding="utf-8",
            )

            result: ConvertResult = collect_attachments(str(note), None)

            self.assertNotIn("Hidden title", result["body"])
            self.assertNotIn("internal", result["body"])
            self.assertIn("Published title", result["body"])

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

    def test_canvas_embed_becomes_svg_attachment_with_original_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            canvas = Path(tmp) / "board.canvas"
            note.write_text("![[board.canvas|640]]\n", encoding="utf-8")
            canvas.write_text(
                '{"nodes": ['
                '{"id": "a", "type": "text", "text": "A", '
                '"x": -10, "y": 20, "width": 100, "height": 50}, '
                '{"id": "b", "type": "text", "text": "B", '
                '"x": 190, "y": 20, "width": 100, "height": 50}], '
                '"edges": [{"fromNode": "a", "fromSide": "right", '
                '"toNode": "b", "toSide": "left", "label": "next"}]}',
                encoding="utf-8",
            )

            result: ConvertResult = collect_attachments(str(note), None)

            self.assertEqual(len(result["attachments"]), 1)
            attachment = result["attachments"][0]
            self.assertTrue(attachment["name"].endswith("-board.svg"))
            self.assertIn('ri:attachment ri:filename="', result["body"])
            self.assertIn(attachment["name"], result["body"])
            self.assertIn('ac:width="640"', result["body"])

            svg = base64.b64decode(attachment["data_b64"]).decode("utf-8")
            self.assertIn('viewBox="-50 -20 340 100"', svg)
            self.assertIn('x="-10" y="20" width="100" height="50"', svg)
            self.assertIn('x="190" y="20" width="100" height="50"', svg)
            self.assertIn('x1="90" y1="45" x2="190" y2="45"', svg)
            self.assertIn(">next</text>", svg)

    def test_missing_canvas_embed_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("![[missing.canvas]]\n", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "Canvas file not found"):
                collect_attachments(str(note), None)

    def test_canvas_group_label_is_above_nodes_at_group_top(self) -> None:
        svg = render_canvas_svg(
            {
                "nodes": [
                    {
                        "id": "group",
                        "type": "group",
                        "label": "Group",
                        "x": 0,
                        "y": 0,
                        "width": 200,
                        "height": 100,
                    },
                    {
                        "id": "node",
                        "type": "text",
                        "text": "Node",
                        "x": 0,
                        "y": 0,
                        "width": 100,
                        "height": 50,
                    },
                ],
                "edges": [],
            }
        ).decode("utf-8")

        self.assertIn('<text x="16" y="-10"', svg)
        self.assertIn('<rect x="0" y="0" width="100" height="50"', svg)


if __name__ == "__main__":
    unittest.main()
