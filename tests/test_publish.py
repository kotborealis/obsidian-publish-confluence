from __future__ import annotations

import tempfile
import unittest
from base64 import b64encode
from pathlib import Path
from unittest.mock import patch

from obsidian_publish_confluence.convert import AttachmentJson
from obsidian_publish_confluence.publish import (
    Config,
    ConfluenceApiError,
    PageNotFoundError,
    create_page,
    page_id_from_url,
    parse_json_response,
    publish_markdown,
    read_frontmatter_page_url,
    update_page,
    upload_attachments,
)


class PublishTests(unittest.TestCase):
    def test_parse_json_response_surfaces_confluence_message(self) -> None:
        with self.assertRaises(ConfluenceApiError) as ctx:
            parse_json_response('{"statusCode":400,"message":"boom","reason":"Bad Request"}')
        self.assertIn("HTTP 400", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))

    def test_parse_json_response_marks_missing_pages(self) -> None:
        with self.assertRaises(PageNotFoundError):
            parse_json_response('{"statusCode":404,"message":"missing"}')

    def test_parse_json_response_rejects_non_object(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not an object"):
            parse_json_response("[]")

    def test_page_id_url_must_contain_numeric_id(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Invalid Confluence page ID"):
            page_id_from_url("https://confluence.example.com/pages/viewpage.action?pageId=abc")

    def test_create_page_rejects_invalid_response_id(self) -> None:
        config = Config("https://confluence.example.com", "DOCS", "123")
        with patch("obsidian_publish_confluence.publish.confluence_post", return_value={"id": []}):
            with self.assertRaisesRegex(ConfluenceApiError, "valid page ID"):
                create_page(config, "title", "body", "123", "DOCS")

    def test_update_page_rejects_invalid_response_version(self) -> None:
        config = Config("https://confluence.example.com", "DOCS", "123")
        with patch(
            "obsidian_publish_confluence.publish.confluence_put", return_value={"version": {}}
        ):
            with self.assertRaisesRegex(ConfluenceApiError, "valid version"):
                update_page(config, "123", "title", "body", 1)

    def test_frontmatter_page_url_key_must_be_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("---\nconfluence_url\n---\n# hi\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Invalid confluence_url"):
                read_frontmatter_page_url(str(note))

    def test_non_404_page_error_does_not_create_duplicate_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text(
                '---\nconfluence_url: "https://confluence.example.com/spaces/DOCS/pages/999"\n'
                "---\n# hi\n",
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
                    side_effect=ConfluenceApiError("HTTP 500; unavailable", 500),
                ),
                patch("obsidian_publish_confluence.publish.create_page") as create_page,
            ):
                with self.assertRaises(ConfluenceApiError):
                    publish_markdown(config, str(note))

            create_page.assert_not_called()

    def test_attachment_upload_failure_is_fatal_and_includes_response(self) -> None:
        config = Config(
            base_url="https://confluence.example.com",
            space="DOCS",
            parent_id="123",
        )
        attachment: AttachmentJson = {
            "name": "image.png",
            "data_b64": b64encode(b"image").decode("ascii"),
        }

        with patch(
            "obsidian_publish_confluence.publish.subprocess.run",
            return_value=type(
                "Completed", (), {"stdout": '{"message":"denied"}\n500', "stderr": ""}
            )(),
        ):
            with self.assertRaisesRegex(ConfluenceApiError, "denied"):
                upload_attachments(config, "123", [attachment])

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
