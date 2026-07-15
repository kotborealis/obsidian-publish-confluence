from __future__ import annotations

import base64
import os
import re
import uuid
from pathlib import Path

import markdown

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}


def find_vault_root(start_dir: str) -> Path:
    current = Path(start_dir).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".obsidian").is_dir():
            return candidate
    return current


def is_image_path(path_text: str) -> bool:
    return Path(path_text.strip()).suffix.lower() in IMAGE_EXTENSIONS


def escape_markdown_url(url: str) -> str:
    return url.replace("\\", "\\\\").replace(" ", "%20")


def resolve_attachment_path(src: str, base_dir: str, vault_root: Path) -> Path | None:
    normalized = src.strip()
    if not normalized:
        return None

    candidate = Path(normalized).expanduser()
    candidates: list[Path] = []
    if candidate.is_absolute():
        candidates.append(candidate)
    else:
        basename = candidate.name
        candidates.extend(
            [
                Path(base_dir) / candidate,
                vault_root / candidate,
                Path(base_dir) / "_attachments" / basename,
                vault_root / "_attachments" / basename,
            ]
        )

    for resolved in candidates:
        if resolved.is_file():
            return resolved
    return None


def convert_obsidian_image_embeds(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        path_text = target.split("|", 1)[0].strip()
        if not is_image_path(path_text):
            return match.group(0)
        return f"![]({escape_markdown_url(path_text)})"

    return re.sub(r"!\[\[([^\]]+)\]\]", replace, text)


def extract_plantuml_macros(text: str) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        code = match.group(2).strip()
        if not code:
            return ""
        macro_id = uuid.uuid4()
        token = f"PLANTUMLMACRO{len(replacements)}TOKEN"
        replacements[token] = (
            f'<ac:structured-macro ac:name="plantuml" ac:schema-version="1" ac:macro-id="{macro_id}">'
            '<ac:parameter ac:name="atlassian-macro-output-type">INLINE</ac:parameter>'
            f"<ac:plain-text-body><![CDATA[{code}\n]]></ac:plain-text-body>"
            "</ac:structured-macro>"
        )
        return f"\n\n{token}\n\n"

    pattern = re.compile(r"```(plantuml|puml)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
    return pattern.sub(replace, text), replacements


def restore_plantuml_macros(html: str, replacements: dict[str, str]) -> str:
    for token, macro in replacements.items():
        html = html.replace(f"<p>{token}</p>", macro)
        html = html.replace(token, macro)
    return html


def escape_xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def convert_code_blocks(html: str) -> str:
    def replace(match: re.Match[str]) -> str:
        lang = match.group(1) or ""
        code = match.group(2)
        parts = ['<ac:structured-macro ac:name="code" ac:schema-version="1">']
        if lang:
            parts.append(f'<ac:parameter ac:name="language">{escape_xml(lang)}</ac:parameter>')
        parts.append(f"<ac:plain-text-body><![CDATA[{code}]]></ac:plain-text-body>")
        parts.append("</ac:structured-macro>")
        return "\n".join(parts)

    pattern = re.compile(r'<pre><code(?:\s+class="language-(\w+)")?>(.*?)</code></pre>', re.DOTALL)
    return pattern.sub(replace, html)


def fix_xhtml(html: str) -> str:
    html = re.sub(r"<br>", "<br/>", html)
    html = re.sub(r"<hr>", "<hr/>", html)
    html = re.sub(r"<img\s+([^>]*?[^/])>", r"<img \1/>", html)
    return html


def collect_local_image_attachments(html: str, base_dir: str, vault_root: Path) -> list[tuple[str, bytes]]:
    attachments: list[tuple[str, bytes]] = []

    def collect(match: re.Match[str]) -> str:
        src = match.group(1)
        if src.startswith(("http://", "https://", "data:")):
            return ""
        resolved = resolve_attachment_path(src.replace("%20", " "), base_dir, vault_root)
        if resolved is not None:
            attachments.append((resolved.name, resolved.read_bytes()))
        return ""

    re.sub(r'<img\s+[^>]*src="([^"]+)"', collect, html)
    return attachments


def convert_local_images_to_ac(html: str, base_dir: str, vault_root: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        full_tag = match.group(0)
        src = match.group(1)
        if src.startswith(("http://", "https://", "data:")):
            return full_tag
        resolved = resolve_attachment_path(src.replace("%20", " "), base_dir, vault_root)
        if resolved is None:
            return full_tag
        return (
            '<ac:image ac:height="auto">'
            f'<ri:attachment ri:filename="{escape_xml(resolved.name)}"/>'
            "</ac:image>"
        )

    return re.sub(r'<img\s+[^>]*src="([^"]+)"[^>]*>', replace, html)


def render_markdown(text: str) -> str:
    return markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"],
        output_format="html",
    )


def collect_attachments(md_path: str, plantuml_server: str | None = None) -> dict[str, object]:
    md_path = os.path.abspath(md_path)
    if not os.path.isfile(md_path):
        raise FileNotFoundError(f"File not found: {md_path}")

    base_dir = os.path.dirname(md_path)
    vault_root = find_vault_root(base_dir)
    text = Path(md_path).read_text(encoding="utf-8")
    text = convert_obsidian_image_embeds(text)

    text, plantuml_replacements = extract_plantuml_macros(text)

    html = render_markdown(text)
    html = fix_xhtml(html)
    html = restore_plantuml_macros(html, plantuml_replacements)
    html = convert_code_blocks(html)

    image_attachments = collect_local_image_attachments(html, base_dir, vault_root)
    html = convert_local_images_to_ac(html, base_dir, vault_root)

    attachments = [
        {"name": name, "data_b64": base64.b64encode(data).decode("ascii")}
        for name, data in image_attachments
    ]
    return {"body": html, "attachments": attachments}
