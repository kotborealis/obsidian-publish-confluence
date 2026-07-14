from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .convert import collect_attachments

ENV_PREFIX = "OBSIDIAN_PUBLISH_CONFLUENCE"
DEFAULT_MAPPING_FILE = os.path.expanduser("~/.config/obsidian-publish-confluence/mapping.json")


def die(message: str) -> None:
    raise RuntimeError(message)


def info(message: str) -> None:
    print(f"  {message}", file=sys.stderr)


@dataclass
class Config:
    base_url: str | None
    space: str | None
    parent_id: str | None
    mapping_file: str
    plantuml_server: str | None

    @property
    def api_url(self) -> str:
        if not self.base_url:
            die(f"Set {ENV_PREFIX}_BASE_URL or pass --base-url")
        return f"{self.base_url.rstrip('/')}/rest/api"

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
        mapping_file=os.environ.get(f"{ENV_PREFIX}_MAPPING_FILE", DEFAULT_MAPPING_FILE),
        plantuml_server=os.environ.get(f"{ENV_PREFIX}_PLANTUML_SERVER") or None,
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


def parse_json_response(response_text: str) -> dict[str, object]:
    if not response_text.strip():
        die("Confluence API returned an empty response")
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as exc:
        snippet = response_text[:500].strip()
        die(f"Confluence API returned non-JSON response: {snippet or '<empty>'}")


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


def confluence_get(config: Config, path: str) -> dict[str, object]:
    return parse_json_response(curl(config, [f"{config.api_url}{path}"]))


def confluence_post(config: Config, path: str, payload: dict[str, object]) -> dict[str, object]:
    return parse_json_response(curl(config, ["-X", "POST", f"{config.api_url}{path}"], data=json.dumps(payload)))


def confluence_put(config: Config, path: str, payload: dict[str, object]) -> dict[str, object]:
    return parse_json_response(curl(config, ["-X", "PUT", f"{config.api_url}{path}"], data=json.dumps(payload)))


def fetch_page_version(config: Config, page_id: str) -> int:
    response = confluence_get(config, f"/content/{page_id}?expand=version")
    return int(response["version"]["number"])


def create_page(config: Config, title: str, body: str, parent_id: str, space_key: str) -> str:
    response = confluence_post(
        config,
        "/content",
        {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "ancestors": [{"id": int(parent_id)}],
            "body": {"storage": {"value": body, "representation": "storage"}},
        },
    )
    return str(response["id"])


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
    return int(response["version"]["number"])


def upload_attachments(config: Config, page_id: str, attachments: list[dict[str, str]]) -> None:
    if not attachments:
        return

    info(f"Uploading {len(attachments)} attachment(s)...")
    with tempfile.TemporaryDirectory() as work_dir:
        for attachment in attachments:
            name = attachment["name"]
            path = Path(work_dir) / name
            path.write_bytes(base64.b64decode(attachment["data_b64"]))
            completed = subprocess.run(
                [
                    "curl",
                    "-s",
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
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
            code = completed.stdout.strip()
            if code != "200":
                info(f"WARNING: Failed to upload {name} (HTTP {code})")
            else:
                info(f"Uploaded: {name}")


def load_mapping(mapping_file: str) -> dict[str, dict[str, object]]:
    path = Path(mapping_file)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def lookup_page_id(mapping_file: str, md_path: str) -> str | None:
    entry = load_mapping(mapping_file).get(md_path, {})
    page_id = entry.get("page_id")
    return str(page_id) if page_id else None


def save_mapping_entry(
    mapping_file: str,
    base_url: str,
    md_path: str,
    page_id: str,
    title: str,
    parent_id: str,
    space_key: str,
    version: int,
) -> None:
    mapping = load_mapping(mapping_file)
    url = f"{base_url.rstrip('/')}/spaces/{space_key}/pages/{page_id}"
    mapping[md_path] = {
        "page_id": page_id,
        "title": title,
        "parent_id": parent_id,
        "space_key": space_key,
        "url": url,
        "version": version,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = Path(mapping_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")


def cmd_check(mapping_file: str) -> int:
    path = Path(mapping_file)
    if not path.exists():
        print(f"No mapping file found at {mapping_file}")
        return 0
    mapping = load_mapping(mapping_file)
    if not mapping:
        print("Mapping file is empty.")
        return 0
    print(f"{len(mapping)} tracked page(s):")
    for source_path, entry in sorted(mapping.items()):
        status = "OK" if Path(source_path).exists() else "MISSING"
        print(f"  [{status}] {source_path}")
        print(f"          -> {entry['url']}")
    return 0


def publish_markdown(
    config: Config,
    md_file: str,
    title: str | None = None,
    space_key: str | None = None,
    parent_id: str | None = None,
    base_url: str | None = None,
) -> str:
    config = Config(
        base_url=base_url or config.base_url,
        space=space_key or config.space,
        parent_id=parent_id or config.parent_id,
        mapping_file=config.mapping_file,
        plantuml_server=config.plantuml_server,
    )
    config.require_publish_config()
    check_prereqs(config)

    abs_md = str(Path(md_file).resolve())
    resolved_title = title or Path(md_file).stem
    info(f"Title:       {resolved_title}")
    info(f"Space:       {config.space}")
    info(f"Parent ID:   {config.parent_id}")
    info("Converting markdown...")

    convert_result = collect_attachments(abs_md, config.plantuml_server)
    html_body = str(convert_result["body"])
    attachments = list(convert_result["attachments"])
    if not html_body:
        die("Conversion produced empty body")

    page_id = lookup_page_id(config.mapping_file, abs_md)
    if page_id:
        info(f"Found existing page ID: {page_id} (updating...)")
        prev_version = fetch_page_version(config, page_id)
        info(f"Current version: {prev_version}")
        new_version = update_page(config, page_id, resolved_title, html_body, prev_version)
        info(f"Updated to version: {new_version}")
        upload_attachments(config, page_id, attachments)
        save_mapping_entry(
            config.mapping_file,
            config.base_url or "",
            abs_md,
            page_id,
            resolved_title,
            config.parent_id or "",
            config.space or "",
            new_version,
        )
    else:
        info(f"Creating new page under parent {config.parent_id}...")
        page_id = create_page(config, resolved_title, html_body, config.parent_id or "", config.space or "")
        info(f"Created page ID: {page_id}")
        upload_attachments(config, page_id, attachments)
        save_mapping_entry(
            config.mapping_file,
            config.base_url or "",
            abs_md,
            page_id,
            resolved_title,
            config.parent_id or "",
            config.space or "",
            1,
        )

    return f"{config.base_url.rstrip('/')}/spaces/{config.space}/pages/{page_id}"
