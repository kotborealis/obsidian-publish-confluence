from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from obsidian_publish_confluence.convert import (
    ConvertResult,
    collect_attachments,
    convert_code_blocks,
    is_fuse_path,
    render_canvas_svg,
)


class ConvertTests(unittest.TestCase):
    def test_fuse_path_is_detected_from_mountinfo(self) -> None:
        mountinfo = "42 1 0:1 / /home/evgeny/writing rw - fuse.rclone writing: rw\n"

        self.assertTrue(is_fuse_path(Path("/home/evgeny/writing/note.md"), mountinfo))
        self.assertFalse(is_fuse_path(Path("/home/evgeny/notes/note.md"), mountinfo))

    def test_fuse_path_is_synced_before_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("# hi\n", encoding="utf-8")

            with (
                patch("obsidian_publish_confluence.convert.is_fuse_path", return_value=True),
                patch("obsidian_publish_confluence.convert.os.sync") as sync,
            ):
                collect_attachments(str(note), None)

            sync.assert_called_once_with()

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

    def test_code_cdata_terminator_is_escaped(self) -> None:
        result = convert_code_blocks("<pre><code>before]]>after</code></pre>")

        self.assertIn("before]]]]><![CDATA[>after", result)

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

    def test_ordered_checkbox_list_becomes_confluence_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("1. [ ] first\n2. [x] second\n", encoding="utf-8")

            result: ConvertResult = collect_attachments(str(note), None)

            body = result["body"]
            self.assertEqual(body.count("<ac:task>"), 2)
            self.assertIn("<ac:task-body>first</ac:task-body>", body)
            self.assertIn("<ac:task-body>second</ac:task-body>", body)

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

    def test_plantuml_cdata_terminator_is_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("```plantuml\nAlice -> Bob: ]]>\n```\n", encoding="utf-8")

            result: ConvertResult = collect_attachments(str(note), None)

            self.assertIn("Alice -> Bob: ]]]]><![CDATA[>", result["body"])

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

    def test_image_embed_is_found_recursively_in_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / ".obsidian").mkdir()
            note = vault / "notes" / "note.md"
            note.parent.mkdir()
            image = vault / "assets" / "image.png"
            image.parent.mkdir()
            image.write_bytes(b"png")
            note.write_text("![[image.png]]\n", encoding="utf-8")

            result: ConvertResult = collect_attachments(str(note), None)

            self.assertEqual(len(result["attachments"]), 1)
            self.assertTrue(result["attachments"][0]["name"].endswith("-image.png"))

    def test_markdown_images_are_namespaced_by_resolved_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / ".obsidian").mkdir()
            note = vault / "note.md"
            for directory, data in (("one", b"one"), ("two", b"two")):
                image = vault / directory / "logo.png"
                image.parent.mkdir()
                image.write_bytes(data)
            note.write_text("![one](one/logo.png)\n![two](two/logo.png)\n", encoding="utf-8")

            result: ConvertResult = collect_attachments(str(note), None)

            names = [attachment["name"] for attachment in result["attachments"]]
            self.assertEqual(len(names), 2)
            self.assertEqual(len(set(names)), 2)
            self.assertTrue(all(name.startswith("opc-") for name in names))

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

            self.assertEqual(len(result["attachments"]), 2)
            attachment = next(
                attachment
                for attachment in result["attachments"]
                if attachment["name"].endswith("-board.svg")
            )
            original = next(
                attachment
                for attachment in result["attachments"]
                if attachment["name"].endswith("-board.canvas")
            )
            self.assertIn('ri:attachment ri:filename="', result["body"])
            self.assertIn(attachment["name"], result["body"])
            self.assertIn(original["name"], result["body"])
            self.assertIn("<![CDATA[📎 Оригинальный .canvas]]>", result["body"])
            self.assertIn('ac:width="640"', result["body"])
            self.assertEqual(base64.b64decode(original["data_b64"]), canvas.read_bytes())

            svg = base64.b64decode(attachment["data_b64"]).decode("utf-8")
            self.assertIn('viewBox="-50 -20 380 130"', svg)
            self.assertIn('x="-10" y="20" width="100" height="50"', svg)
            self.assertIn('x="190" y="20" width="100" height="50"', svg)
            self.assertIn('d="M 90 45 C 140 45 140 45 190 45"', svg)
            self.assertIn('stroke-linecap="round"', svg)
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

    def test_canvas_group_label_renders_multiple_lines(self) -> None:
        svg = render_canvas_svg(
            {
                "nodes": [
                    {
                        "id": "group",
                        "type": "group",
                        "label": "First line\nSecond line",
                        "x": 0,
                        "y": 0,
                        "width": 200,
                        "height": 100,
                    }
                ],
                "edges": [],
            }
        ).decode("utf-8")

        self.assertIn('<tspan x="16" dy="0">First line</tspan>', svg)
        self.assertIn('<tspan x="16" dy="20">Second line</tspan>', svg)
        self.assertIn('viewBox="-40 -70 280 210"', svg)

    def test_canvas_edge_label_renders_multiple_lines(self) -> None:
        svg = render_canvas_svg(
            {
                "nodes": [
                    {"id": "a", "type": "text", "x": 0, "y": 0, "width": 50, "height": 50},
                    {
                        "id": "b",
                        "type": "text",
                        "x": 0,
                        "y": 200,
                        "width": 50,
                        "height": 50,
                    },
                ],
                "edges": [
                    {
                        "fromNode": "a",
                        "fromSide": "bottom",
                        "toNode": "b",
                        "toSide": "top",
                        "label": "First\nSecond",
                    }
                ],
            }
        ).decode("utf-8")

        self.assertIn('dy="0">First</tspan>', svg)
        self.assertIn('dy="18">Second</tspan>', svg)

    def test_canvas_viewbox_includes_curved_edge_control_points(self) -> None:
        svg = render_canvas_svg(
            {
                "nodes": [
                    {
                        "id": "a",
                        "type": "text",
                        "text": "A",
                        "x": 100,
                        "y": 0,
                        "width": 50,
                        "height": 50,
                    },
                    {
                        "id": "b",
                        "type": "text",
                        "text": "B",
                        "x": 100,
                        "y": 300,
                        "width": 50,
                        "height": 50,
                    },
                ],
                "edges": [
                    {
                        "fromNode": "a",
                        "fromSide": "top",
                        "toNode": "b",
                        "toSide": "top",
                    }
                ],
            }
        ).decode("utf-8")

        self.assertIn('viewBox="60 -160 130 550"', svg)

    def test_canvas_markdown_link_becomes_svg_link(self) -> None:
        svg = render_canvas_svg(
            {
                "nodes": [
                    {
                        "id": "node",
                        "type": "text",
                        "text": "[abc](https://foo.bar)",
                        "x": 0,
                        "y": 0,
                        "width": 200,
                        "height": 60,
                    }
                ],
                "edges": [],
            }
        ).decode("utf-8")

        self.assertIn('href="https://foo.bar"', svg)
        self.assertIn(">abc</tspan></a>", svg)
        self.assertNotIn("[abc](https://foo.bar)", svg)

    def test_canvas_renders_rich_markdown(self) -> None:
        svg = render_canvas_svg(
            {
                "nodes": [
                    {
                        "id": "node",
                        "type": "text",
                        "text": (
                            "See https://foo.bar **bold** *italic* `code`\n\n- one\n    - nested"
                        ),
                        "x": 0,
                        "y": 0,
                        "width": 300,
                        "height": 180,
                    }
                ],
                "edges": [],
            }
        ).decode("utf-8")

        self.assertIn('href="https://foo.bar"', svg)
        self.assertIn(">https://foo.bar</tspan></a>", svg)
        self.assertIn('font-weight="bold"', svg)
        self.assertIn('font-style="italic"', svg)
        self.assertIn('font-family="monospace"', svg)
        self.assertIn(">• one</text>", svg)
        self.assertIn(">  • nested</text>", svg)

    def test_canvas_fenced_code_preserves_lines_and_uses_code_block_style(self) -> None:
        svg = render_canvas_svg(
            {
                "nodes": [
                    {
                        "id": "node",
                        "type": "text",
                        "text": "```python\nfor x in range(3):\n    print(x)\n```",
                        "x": 0,
                        "y": 0,
                        "width": 300,
                        "height": 180,
                    }
                ],
                "edges": [],
            }
        ).decode("utf-8")

        self.assertNotIn("```", svg)
        self.assertEqual(svg.count('<text x="18"'), 2)
        self.assertIn('fill="#f1f5f9"', svg)
        self.assertIn('font-family="monospace"', svg)
        self.assertIn('font-size="14"', svg)
        self.assertIn('text x="18"', svg)
        self.assertIn('text-anchor="start"', svg)
        self.assertIn('xml:space="preserve"', svg)
        self.assertIn(">for x in range(3):</tspan>", svg)
        self.assertIn(">    print(x)</tspan>", svg)


if __name__ == "__main__":
    unittest.main()
