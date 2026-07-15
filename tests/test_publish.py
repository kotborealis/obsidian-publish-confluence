from __future__ import annotations

import json
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
                mapping_file=str(Path(tmp) / "mapping.json"),
            )

            with patch("obsidian_publish_confluence.publish.check_prereqs") as check_prereqs:
                result = publish_markdown(config, str(note), dry_run=True)

            self.assertEqual(result, "DRY-RUN")
            check_prereqs.assert_not_called()

    def test_stale_mapping_falls_back_to_create(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("# hi\n", encoding="utf-8")
            mapping = Path(tmp) / "mapping.json"
            mapping.write_text(
                json.dumps(
                    {
                        str(note): {
                            "page_id": "999",
                            "title": "x",
                            "parent_id": "123",
                            "space_key": "DOCS",
                            "url": "u",
                            "version": 1,
                            "updated_at": "now",
                        }
                    }
                ),
                encoding="utf-8",
            )
            config = Config(
                base_url="https://confluence.example.com",
                space="DOCS",
                parent_id="123",
                mapping_file=str(mapping),
            )

            with (
                patch("obsidian_publish_confluence.publish.check_prereqs"),
                patch(
                    "obsidian_publish_confluence.publish.fetch_page_version",
                    side_effect=RuntimeError("Mapped page ID 999 no longer exists in Confluence"),
                ),
                patch(
                    "obsidian_publish_confluence.publish.create_page", return_value="321"
                ) as create_page,
                patch("obsidian_publish_confluence.publish.upload_attachments"),
                patch("obsidian_publish_confluence.publish.save_mapping_entry"),
            ):
                result = publish_markdown(config, str(note))

            self.assertTrue(result.endswith("/pages/321"))
            create_page.assert_called_once()


if __name__ == "__main__":
    unittest.main()
