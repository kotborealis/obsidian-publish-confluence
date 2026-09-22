from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .convert import AttachmentJson, ConvertResult, collect_attachments

ENV_PREFIX = "OBSIDIAN_PUBLISH_CONFLUENCE"
FRONTMATTER_RE = re.compile(r"\A(?:\ufeff)?---[ \t]*\n(?P<body>.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
CONFLUENCE_URL_RE = re.compile(r"/pages/(\d+)(?:/|$)")

JsonDict = dict[str, Any]


class ConfluenceApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PageNotFoundError(ConfluenceApiError):
    pass


def die(message: str) -> None:
    raise RuntimeError(message)


def info(message: str) -> None:
    print(f"  {message}", file=sys.stderr)


def summarize_confluence_error(response: JsonDict) -> str:
    parts: list[str] = []
    status_code = response.get("statusCode")
    if status_code is not None:
        parts.append(f"HTTP {status_code}")
    message = response.get("message")
    if isinstance(message, str) and message:
        parts.append(message)
    reason = response.get("reason")
    if isinstance(reason, str) and reason:
        parts.append(f"reason={reason}")
    if not parts:
        return "Confluence API request failed"
    return "; ".join(parts)


@dataclass
class Config:
    base_url: str | None
    space: str | None
    parent_id: str | None

    @property
    def api_url(self) -> str:
        if not self.base_url:
            die(f"Set {ENV_PREFIX}_BASE_URL or pass --base-url")
        base_url = self.base_url
        assert base_url is not None
        return f"{base_url.rstrip('/')}/rest/api"

    def require_publish_config(self) -> None:
        if not self.base_url:
            die(f"Set {ENV_PREFIX}_BASE_URL or pass --base-url")
        if not self.space:
            die(f"Set {ENV_PREFIX}_SPACE or pass --space")
        if not self.parent_id:
            die(f"Set {ENV_PREFIX}_PARENT_ID or pass --parent-id")


def config_from_env() -> Config:
    return Config(
        base_url=os.environ.get(f"{ENV_PREFIX}_BASE_URL"),
        space=os.environ.get(f"{ENV_PREFIX}_SPACE"),
        parent_id=os.environ.get(f"{ENV_PREFIX}_PARENT_ID"),
    )


def check_kerberos() -> None:
    result = subprocess.run(["klist", "-s"], capture_output=True, check=False)
    if result.returncode != 0:
        die("No Kerberos ticket. Run: kinit <your-login>@REALM")


def curl(config: Config, args: list[str], data: str | None = None) -> str:
    cmd = [
        "curl",
        "-s",
        "--negotiate",
        "-u",
        ":",
        "-H",
        "Content-Type: application/json",
        *args,
    ]
    if data is not None:
        cmd.extend(["--data-binary", "@-"])
    completed = subprocess.run(cmd, input=data, text=True, capture_output=True, check=True)
    return completed.stdout


def parse_json_response(response_text: str) -> JsonDict:
    if not response_text.strip():
        die("Confluence API returned an empty response")
    try:
        parsed_value = json.loads(response_text)
    except json.JSONDecodeError:
        snippet = response_text[:500].strip()
        die(f"Confluence API returned non-JSON response: {snippet or '<empty>'}")
    if not isinstance(parsed_value, dict):
        die("Confluence API returned JSON that is not an object")
    parsed = cast(JsonDict, parsed_value)
    status_code = parsed.get("statusCode")
    if "statusCode" in parsed and not isinstance(status_code, int):
        raise ConfluenceApiError("Confluence API returned an invalid status code")
    if "statusCode" in parsed and status_code != 200:
        error_type = PageNotFoundError if status_code == 404 else ConfluenceApiError
        raise error_type(summarize_confluence_error(parsed), status_code)
    return parsed
    raise AssertionError("unreachable")


def curl_status(url: str) -> str:
    completed = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--negotiate", "-u", ":", url],
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def check_prereqs(config: Config) -> None:
    config.require_publish_config()
    check_kerberos()
    try:
        import markdown  # noqa: F401
    except ImportError:
        die("Python package 'Markdown' is not installed. Run: pip install Markdown")
    code = curl_status(f"{config.api_url}/content/{config.parent_id}")
    if code != "200":
        die(f"Cannot authenticate to Confluence (HTTP {code}). Check Kerberos ticket and config.")


def confluence_get(config: Config, path: str) -> JsonDict:
    return parse_json_response(curl(config, [f"{config.api_url}{path}"]))


def confluence_post(config: Config, path: str, payload: JsonDict) -> JsonDict:
    return parse_json_response(
        curl(config, ["-X", "POST", f"{config.api_url}{path}"], data=json.dumps(payload))
    )


def confluence_put(config: Config, path: str, payload: JsonDict) -> JsonDict:
    return parse_json_response(
        curl(config, ["-X", "PUT", f"{config.api_url}{path}"], data=json.dumps(payload))
    )


def confluence_delete(config: Config, path: str) -> JsonDict | None:
    response_text = curl(config, ["-X", "DELETE", f"{config.api_url}{path}"])
    if not response_text.strip():
        return None
    return parse_json_response(response_text)


def fetch_page_details(config: Config, page_id: str) -> tuple[int, str]:
    try:
        response = confluence_get(config, f"/content/{page_id}?expand=version")
    except PageNotFoundError as exc:
        raise PageNotFoundError(
            f"Mapped page ID {page_id} no longer exists in Confluence", 404
        ) from exc
    version = response.get("version")
    version_number = numeric_id(version.get("number")) if isinstance(version, dict) else None
    if version_number is None:
        raise ConfluenceApiError(
            f"Confluence API response for page {page_id} does not include valid version info"
        )
    title = response.get("title")
    if not isinstance(title, str) or not title:
        die(f"Confluence API response for page {page_id} does not include title info")
    return int(version_number), cast(str, title)


def find_attachment_id_by_name(config: Config, page_id: str, name: str) -> str | None:
    encoded_name = urllib.parse.quote(name)
    response = confluence_get(
        config, f"/content/{page_id}/child/attachment?filename={encoded_name}"
    )
    results = response.get("results", [])
    if results:
        if not isinstance(results, list) or not isinstance(results[0], dict):
            raise ConfluenceApiError("Confluence API returned invalid attachment results")
        attachment_id = numeric_id(results[0].get("id"))
        if attachment_id is None:
            raise ConfluenceApiError("Confluence API returned an invalid attachment ID")
        return attachment_id
    return None


def delete_attachment_by_name(config: Config, page_id: str, name: str) -> bool:
    attachment_id = find_attachment_id_by_name(config, page_id, name)
    if not attachment_id:
        return False
    confluence_delete(config, f"/content/{attachment_id}")
    return True


def create_page(config: Config, title: str, body: str, parent_id: str, space_key: str) -> str:
    parent_id_value = numeric_id(parent_id)
    if parent_id_value is None:
        raise ConfluenceApiError(f"Invalid parent page ID: {parent_id}")
    response = confluence_post(
        config,
        "/content",
        {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "ancestors": [{"id": int(parent_id_value)}],
            "body": {"storage": {"value": body, "representation": "storage"}},
        },
    )
    page_id = numeric_id(response.get("id"))
    if page_id is None:
        raise ConfluenceApiError("Confluence API response does not include a valid page ID")
    return page_id


def update_page(config: Config, page_id: str, title: str, body: str, prev_version: int) -> int:
    response = confluence_put(
        config,
        f"/content/{page_id}",
        {
            "id": page_id,
            "type": "page",
            "title": title,
            "version": {"number": prev_version + 1},
            "body": {"storage": {"value": body, "representation": "storage"}},
        },
    )
    version = response.get("version")
    version_number = numeric_id(version.get("number")) if isinstance(version, dict) else None
    if version_number is None:
        raise ConfluenceApiError("Confluence API response does not include a valid version")
    return int(version_number)


def numeric_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value.isdigit() else None


def upload_attachments(config: Config, page_id: str, attachments: list[AttachmentJson]) -> None:
    if not attachments:
        return

    info(f"Uploading {len(attachments)} attachment(s)...")
    with tempfile.TemporaryDirectory() as work_dir:
        for attachment in attachments:
            name = attachment["name"]
            path = Path(work_dir) / name
            path.write_bytes(base64.b64decode(attachment["data_b64"]))

            def upload(path: Path = path, name: str = name) -> tuple[str, str]:
                completed = subprocess.run(
                    [
                        "curl",
                        "-s",
                        "-w",
                        "\n%{http_code}",
                        "--negotiate",
                        "-u",
                        ":",
                        "-H",
                        "X-Atlassian-Token: no-check",
                        "-F",
                        f"file=@{path};filename={name}",
                        f"{config.api_url}/content/{page_id}/child/attachment",
                    ],
                    text=True,
                    capture_output=True,
                    check=True,
                )
                body, separator, code = completed.stdout.rstrip().rpartition("\n")
                if not separator:
                    body = completed.stderr.strip()
                return code, body.strip()

            code, error_body = upload()
            if code != "200":
                if code == "400" and delete_attachment_by_name(config, page_id, name):
                    info(f"Attachment {name} already exists, replacing it...")
                    retry_code, error_body = upload()
                    if retry_code == "200":
                        info(f"Uploaded: {name}")
                        continue
                    code = retry_code
                detail = f": {error_body}" if error_body else ""
                raise ConfluenceApiError(
                    f"Failed to upload attachment {name} (HTTP {code}){detail}",
                    int(code) if code.isdigit() else None,
                )
            info(f"Uploaded: {name}")


def page_id_from_url(page_url: str) -> str:
    parsed = urllib.parse.urlparse(page_url)
    query_page_id = urllib.parse.parse_qs(parsed.query).get("pageId")
    if query_page_id and query_page_id[0]:
        page_id = numeric_id(query_page_id[0])
        if page_id is not None:
            return page_id
        die(f"Invalid Confluence page ID in URL: {page_url}")
    path_match = CONFLUENCE_URL_RE.search(parsed.path)
    if path_match:
        return path_match.group(1)
    die(f"Cannot extract Confluence page ID from URL: {page_url}")
    raise AssertionError("unreachable")


def make_page_url(config: Config, page_id: str) -> str:
    base_url = config.base_url or ""
    space = config.space or ""
    return f"{base_url.rstrip('/')}/spaces/{space}/pages/{page_id}"


def read_frontmatter_page_url(md_path: str) -> str | None:
    text = Path(md_path).read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    for line in match.group("body").splitlines():
        stripped = line.strip()
        url_match = re.fullmatch(r"confluence_url\s*:\s*(.*?)\s*", stripped)
        if url_match:
            value = url_match.group(1).strip().strip("'\"")
            if not value:
                die("Frontmatter confluence_url cannot be empty")
            return value
        if stripped.startswith("confluence_url"):
            die("Invalid confluence_url in frontmatter")
    return None


def save_frontmatter_page_url(md_path: str, page_url: str) -> None:
    text = Path(md_path).read_text(encoding="utf-8")
    page_url_line = f'confluence_url: "{page_url}"'
    match = FRONTMATTER_RE.match(text)
    if match:
        body = match.group("body")
        updated_body, count = re.subn(
            r"^confluence_url\s*:.*$", page_url_line, body, count=1, flags=re.MULTILINE
        )
        if not count:
            updated_body = f"{page_url_line}\n{body}"
        text = text[: match.start("body")] + updated_body + text[match.end("body") :]
    else:
        text = f"---\n{page_url_line}\n---\n{text}"
    Path(md_path).write_text(text, encoding="utf-8")


def publish_markdown(
    config: Config,
    md_file: str,
    title: str | None = None,
    page_url: str | None = None,
    space_key: str | None = None,
    parent_id: str | None = None,
    base_url: str | None = None,
    dry_run: bool = False,
) -> str:
    config = Config(
        base_url=base_url or config.base_url,
        space=space_key or config.space,
        parent_id=parent_id or config.parent_id,
    )
    config.require_publish_config()
    if not dry_run:
        check_prereqs(config)

    abs_md = str(Path(md_file).resolve())
    resolved_title = title or Path(md_file).stem
    info(f"Title:       {resolved_title}")
    info(f"Space:       {config.space}")
    info(f"Parent ID:   {config.parent_id}")
    info("Converting markdown...")

    convert_result: ConvertResult = collect_attachments(abs_md)
    html_body = convert_result["body"]
    attachments = convert_result["attachments"]
    if not html_body:
        die("Conversion produced empty body")

    page_url = page_url or read_frontmatter_page_url(abs_md)
    page_id = page_id_from_url(page_url) if page_url else None
    if dry_run:
        action = "update" if page_id else "create"
        info(f"Dry run:     {action}")
        info(f"Attachments: {len(attachments)}")
        for attachment in attachments:
            info(f"  - {attachment['name']}")
        plantuml_count = html_body.count('ac:name="plantuml"')
        info(f"PlantUML:    {plantuml_count} macro(s)")
        return "DRY-RUN"

    if page_id:
        info(f"Found existing page ID: {page_id} (updating...)")
        try:
            prev_version, existing_title = fetch_page_details(config, page_id)
        except PageNotFoundError as exc:
            info(f"Stored page ID {page_id} is stale ({exc}); creating a new page...")
            page_id = None
            page_url = None
        else:
            info(f"Current version: {prev_version}")
            new_version = update_page(config, page_id, existing_title, html_body, prev_version)
            info(f"Updated to version: {new_version}")
            upload_attachments(config, page_id, attachments)
            page_url = make_page_url(config, page_id)
            save_frontmatter_page_url(abs_md, page_url)
            return page_url
    if not page_id:
        info(f"Creating new page under parent {config.parent_id}...")
        page_id = create_page(
            config, resolved_title, html_body, config.parent_id or "", config.space or ""
        )
        info(f"Created page ID: {page_id}")
        upload_attachments(config, page_id, attachments)
        page_url = make_page_url(config, page_id)
        save_frontmatter_page_url(abs_md, page_url)

    assert page_url is not None
    return page_url
