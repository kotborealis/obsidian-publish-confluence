from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from obsidian_publish_confluence.publish import (
    Config,
    ConfluenceApiError,
    parse_json_response,
    publish_markdown,
)


class PublishTests(unittest.TestCase):
    def test_parse_json_response_surfaces_confluence_message(self) -> None:
        with self.assertRaises(ConfluenceApiError) as ctx:
            parse_json_response('{"statusCode":400,"message":"boom","reason":"Bad Request"}')
        self.assertIn("HTTP 400", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))

    def test_dry_run_does_not_call_prereqs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("# hi\n", encoding="utf-8")
            config = Config(
                base_url="https://confluence.example.com",
                space="DOCS",
                parent_id="123",
            )

            with patch("obsidian_publish_confluence.publish.check_prereqs") as check_prereqs:
                result = publish_markdown(config, str(note), dry_run=True)

            self.assertEqual(result, "DRY-RUN")
            check_prereqs.assert_not_called()

    def test_frontmatter_page_url_updates_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text(
                '---\nconfluence_url: "https://confluence.example.com/spaces/DOCS/'
                'pages/999"\n---\n# hi\n',
                encoding="utf-8",
            )
            config = Config(
                base_url="https://confluence.example.com",
                space="DOCS",
                parent_id="123",
            )

            with (
                patch("obsidian_publish_confluence.publish.check_prereqs"),
                patch(
                    "obsidian_publish_confluence.publish.fetch_page_details",
                    return_value=(2, "Existing Confluence title"),
                ),
                patch(
                    "obsidian_publish_confluence.publish.update_page", return_value=3
                ) as update_page,
                patch("obsidian_publish_confluence.publish.upload_attachments"),
            ):
                result = publish_markdown(config, str(note))

            self.assertTrue(result.endswith("/pages/999"))
            update_page.assert_called_once()
            self.assertEqual(update_page.call_args.args[2], "Existing Confluence title")
            self.assertIn("confluence_url:", note.read_text(encoding="utf-8"))

    def test_cli_page_url_is_saved_to_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("# hi\n", encoding="utf-8")
            config = Config(
                base_url="https://confluence.example.com",
                space="DOCS",
                parent_id="123",
            )

            with (
                patch("obsidian_publish_confluence.publish.check_prereqs"),
                patch(
                    "obsidian_publish_confluence.publish.fetch_page_details",
                    return_value=(2, "Existing Confluence title"),
                ),
                patch("obsidian_publish_confluence.publish.update_page", return_value=3),
                patch("obsidian_publish_confluence.publish.upload_attachments"),
            ):
                result = publish_markdown(
                    config,
                    str(note),
                    page_url="https://confluence.example.com/pages/viewpage.action?pageId=456",
                )

            self.assertTrue(result.endswith("/pages/456"))
            self.assertIn(
                'confluence_url: "https://confluence.example.com/spaces/DOCS/pages/456"',
                note.read_text(encoding="utf-8"),
            )

    def test_created_page_url_is_saved_to_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("# hi\n", encoding="utf-8")
            config = Config(
                base_url="https://confluence.example.com",
                space="DOCS",
                parent_id="123",
            )

            with (
                patch("obsidian_publish_confluence.publish.check_prereqs"),
                patch("obsidian_publish_confluence.publish.create_page", return_value="321"),
                patch("obsidian_publish_confluence.publish.upload_attachments"),
            ):
                result = publish_markdown(config, str(note))

            self.assertTrue(result.endswith("/pages/321"))
            self.assertIn(
                'confluence_url: "https://confluence.example.com/spaces/DOCS/pages/321"',
                note.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
