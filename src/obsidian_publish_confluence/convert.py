from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple, TypedDict

import markdown

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}


class AttachmentJson(TypedDict):
    name: str
    data_b64: str


class ImageRef(NamedTuple):
    source: str
    attachment_name: str
    width: int | None
    data: bytes | None = None
    related_attachment_name: str | None = None
    related_data: bytes | None = None


class HtmlListItem(NamedTuple):
    start: int
    content_start: int
    content_end: int
    end: int
    start_tag: str
    end_tag: str


class CanvasTextPart(NamedTuple):
    text: str
    href: str | None
    bold: bool = False
    italic: bool = False
    code: bool = False
    pre: bool = False


class ConvertResult(TypedDict):
    body: str
    attachments: list[AttachmentJson]


def find_vault_root(start_dir: str) -> Path:
    current = Path(start_dir).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".obsidian").is_dir():
            return candidate
    return current


def is_image_path(path_text: str) -> bool:
    return Path(path_text.strip()).suffix.lower() in IMAGE_EXTENSIONS


def is_canvas_path(path_text: str) -> bool:
    return Path(path_text.strip()).suffix.lower() == ".canvas"


def escape_markdown_url(url: str) -> str:
    return url.replace("\\", "\\\\").replace(" ", "%20")


def make_attachment_name(md_path: str, source: str) -> str:
    digest = hashlib.sha256(f"{md_path}:{source}".encode()).hexdigest()[:12]
    return f"opc-{digest}-{Path(source).name}"


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


def resolve_canvas_path(src: str, base_dir: str, vault_root: Path) -> Path | None:
    resolved = resolve_attachment_path(src, base_dir, vault_root)
    if resolved is not None:
        return resolved

    normalized = src.strip()
    if not normalized:
        return None
    pattern = normalized if len(Path(normalized).parts) > 1 else Path(normalized).name
    matches = sorted(path for path in vault_root.rglob(pattern) if path.is_file())
    return matches[0] if matches else None


def convert_obsidian_image_embeds(text: str, md_path: str) -> tuple[str, dict[str, ImageRef]]:
    replacements: dict[str, ImageRef] = {}

    def replace(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        path_text, _, suffix = target.partition("|")
        path_text = path_text.strip()
        if not is_image_path(path_text):
            return match.group(0)
        width = int(suffix.strip()) if suffix.strip().isdigit() else None
        attachment_name = make_attachment_name(md_path, path_text)
        token = f"OPCIMAGETOKEN{len(replacements)}"
        replacements[token] = ImageRef(path_text, attachment_name, width)
        return f"![]({escape_markdown_url(token)})"

    return re.sub(r"!\[\[([^\]]+)\]\]", replace, text), replacements


def canvas_attachment_name(md_path: str, source: str) -> str:
    return str(Path(make_attachment_name(md_path, source)).with_suffix(".svg").name)


CANVAS_COLORS = {
    "1": "#e93147",
    "2": "#f49d37",
    "3": "#e0de71",
    "4": "#44cf6e",
    "5": "#53dfdd",
    "6": "#a882ff",
}
CANVAS_BARE_URL_RE = re.compile(r'(?<![\[("\'])https?://[^\s<>()]+')
MOUNTINFO_ESCAPE_RE = re.compile(r"\\([0-7]{3})")


def is_fuse_path(path: Path, mountinfo: str | None = None) -> bool:
    if mountinfo is None:
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        except OSError:
            return False

    resolved = path.resolve()
    for line in mountinfo.splitlines():
        mount_fields, separator, filesystem_fields = line.partition(" - ")
        if not separator:
            continue
        fields = mount_fields.split()
        filesystem = filesystem_fields.split()
        if len(fields) < 5 or not filesystem or not filesystem[0].startswith("fuse"):
            continue
        mountpoint = Path(MOUNTINFO_ESCAPE_RE.sub(lambda match: chr(int(match[1], 8)), fields[4]))
        try:
            resolved.relative_to(mountpoint)
        except ValueError:
            continue
        return True
    return False


def sync_if_fuse_path(path: str) -> None:
    if is_fuse_path(Path(path)):
        os.sync()


def canvas_number(node: dict[str, object], key: str, default: float = 0) -> float:
    value = node.get(key, default)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return default


def canvas_node_label(node: dict[str, object]) -> str:
    node_type = node.get("type")
    if node_type == "text":
        value = node.get("text", "")
    elif node_type == "file":
        value = Path(str(node.get("file", ""))).name
    elif node_type == "link":
        value = node.get("url", "")
    elif node_type == "group":
        value = node.get("label", "")
    else:
        value = node.get("text") or node.get("label") or node.get("file") or node.get("url")
    return value if isinstance(value, str) else str(value or "")


def canvas_node_color(node: dict[str, object], default: str) -> str:
    color = node.get("color")
    if not isinstance(color, str):
        return default
    if color in CANVAS_COLORS:
        return CANVAS_COLORS[color]
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", color):
        return color
    return default


def prepare_canvas_markdown(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        url = match.group(0)
        return f"[{url}]({url})"

    return CANVAS_BARE_URL_RE.sub(replace, text)


class CanvasMarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[list[CanvasTextPart]] = [[]]
        self.bold_depth = 0
        self.italic_depth = 0
        self.code_depth = 0
        self.pre_depth = 0
        self.href: str | None = None
        self.list_stack: list[list[object]] = []

    def line_break(self) -> None:
        if self.lines[-1]:
            self.lines.append([])

    def append_text(self, text: str, styled: bool = True) -> None:
        for index, line in enumerate(text.split("\n")):
            if line:
                self.lines[-1].append(
                    CanvasTextPart(
                        line,
                        self.href,
                        self.bold_depth > 0 if styled else False,
                        self.italic_depth > 0 if styled else False,
                        self.code_depth > 0 if styled else False,
                        self.pre_depth > 0 if styled else False,
                    )
                )
            elif self.pre_depth > 0:
                self.lines[-1].append(CanvasTextPart("", None, pre=True))
            if index < len(text.split("\n")) - 1:
                self.line_break()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"p", "div", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.line_break()
            if tag == "blockquote":
                self.append_text("> ", styled=False)
            if tag == "pre":
                self.code_depth += 1
                self.pre_depth += 1
            if tag.startswith("h"):
                self.bold_depth += 1
        elif tag in {"strong", "b"}:
            self.bold_depth += 1
        elif tag in {"em", "i"}:
            self.italic_depth += 1
        elif tag == "code":
            self.code_depth += 1
        elif tag == "a":
            self.href = dict(attrs).get("href")
        elif tag in {"ul", "ol"}:
            self.line_break()
            self.list_stack.append([tag, 0])
        elif tag == "li":
            self.line_break()
            if self.list_stack:
                list_kind, item_number = self.list_stack[-1]
                item_number = int(item_number) + 1
                self.list_stack[-1][1] = item_number
                prefix = "  " * (len(self.list_stack) - 1)
                prefix += f"{item_number}. " if list_kind == "ol" else "• "
                self.append_text(prefix, styled=False)
        elif tag == "br":
            self.line_break()
        elif tag == "hr":
            self.line_break()
            self.append_text("----", styled=False)
        elif tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.append_text(alt, styled=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        self.append_text(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"strong", "b"}:
            self.bold_depth = max(0, self.bold_depth - 1)
        elif tag in {"em", "i"}:
            self.italic_depth = max(0, self.italic_depth - 1)
        elif tag == "code":
            self.code_depth = max(0, self.code_depth - 1)
        elif tag == "a":
            self.href = None
        elif tag == "pre":
            self.code_depth = max(0, self.code_depth - 1)
            self.pre_depth = max(0, self.pre_depth - 1)
            self.line_break()
        elif tag in {"ul", "ol"}:
            if self.list_stack:
                self.list_stack.pop()
            self.line_break()
        elif tag in {"p", "div", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}:
            if tag.startswith("h"):
                self.bold_depth = max(0, self.bold_depth - 1)
            self.line_break()

    def finish(self) -> list[list[CanvasTextPart]]:
        while len(self.lines) > 1:
            last_line = self.lines[-1]
            if not last_line or all(part.pre and not part.text for part in last_line):
                self.lines.pop()
                continue
            break
        return self.lines


def canvas_markdown_lines(text: str) -> list[list[CanvasTextPart]]:
    rendered = markdown.markdown(
        prepare_canvas_markdown(text),
        extensions=["fenced_code", "nl2br", "sane_lists"],
        output_format="html",
    )
    parser = CanvasMarkdownParser()
    parser.feed(rendered)
    parser.close()
    return parser.finish()


def canvas_text_lines(text: str, width: float) -> list[list[CanvasTextPart]]:
    max_chars = max(1, int(width / 8))
    lines: list[list[CanvasTextPart]] = []
    for paragraph in canvas_markdown_lines(text):
        if any(part.pre for part in paragraph):
            lines.append(paragraph)
            continue
        current: list[CanvasTextPart] = []
        current_length = 0
        for part in paragraph:
            for chunk in re.findall(r"\s+|\S+", part.text):
                if chunk.strip() and current and current_length + len(chunk) > max_chars:
                    lines.append(current)
                    current = []
                    current_length = 0
                current.append(part._replace(text=chunk))
                current_length += len(chunk)
        lines.append(current or [CanvasTextPart("", None)])
    return lines


def canvas_svg_inline_content(text: str) -> str:
    lines = canvas_markdown_lines(text)
    return canvas_svg_line_content(lines[0]) if lines else ""


def canvas_svg_line_content(line: list[CanvasTextPart]) -> str:
    content: list[str] = []
    for part in line:
        attributes: list[str] = []
        if part.bold:
            attributes.append('font-weight="bold"')
        if part.italic:
            attributes.append('font-style="italic"')
        if part.code:
            attributes.append('font-family="monospace"')
        if part.pre:
            attributes.append('font-size="14"')
        if part.href:
            attributes.append('text-decoration="underline"')
        escaped_text = escape_xml(part.text)
        if attributes:
            escaped_text = f"<tspan {' '.join(attributes)}>{escaped_text}</tspan>"
        if part.href:
            escaped_text = f'<a href="{escape_xml_attribute(part.href)}">{escaped_text}</a>'
        content.append(escaped_text)
    return "".join(content)


def canvas_node_geometry(
    node: dict[str, object], offset_x: float, offset_y: float
) -> tuple[float, ...]:
    x = canvas_number(node, "x") - offset_x
    y = canvas_number(node, "y") - offset_y
    width = max(0, canvas_number(node, "width"))
    height = max(0, canvas_number(node, "height"))
    return x, y, width, height


def canvas_edge_point(
    node: dict[str, object], side: object, offset_x: float, offset_y: float
) -> tuple[float, float]:
    x, y, width, height = canvas_node_geometry(node, offset_x, offset_y)
    side_name = str(side or "").lower()
    if side_name == "top":
        return x + width / 2, y
    if side_name == "right":
        return x + width, y + height / 2
    if side_name == "bottom":
        return x + width / 2, y + height
    if side_name == "left":
        return x, y + height / 2
    return x + width / 2, y + height / 2


def canvas_edge_direction(side: object, fallback: tuple[float, float]) -> tuple[float, float]:
    directions = {
        "top": (0.0, -1.0),
        "right": (1.0, 0.0),
        "bottom": (0.0, 1.0),
        "left": (-1.0, 0.0),
    }
    return directions.get(str(side or "").lower(), fallback)


def canvas_edge_geometry(
    from_node: dict[str, object],
    from_side: object,
    to_node: dict[str, object],
    to_side: object,
) -> tuple[
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
]:
    start = canvas_edge_point(from_node, from_side, 0, 0)
    end = canvas_edge_point(to_node, to_side, 0, 0)
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    distance = math.hypot(delta_x, delta_y)
    fallback = (delta_x / distance, delta_y / distance) if distance else (0.0, 1.0)
    control_distance = max(24.0, min(120.0, distance / 2))
    from_direction = canvas_edge_direction(from_side, fallback)
    to_direction = canvas_edge_direction(to_side, fallback)
    control_1 = (
        start[0] + from_direction[0] * control_distance,
        start[1] + from_direction[1] * control_distance,
    )
    control_2 = (
        end[0] + to_direction[0] * control_distance,
        end[1] + to_direction[1] * control_distance,
    )
    return start, control_1, control_2, end


def canvas_edge_path(
    from_node: dict[str, object],
    from_side: object,
    to_node: dict[str, object],
    to_side: object,
) -> tuple[str, tuple[float, float], tuple[float, float]]:
    start, control_1, control_2, end = canvas_edge_geometry(from_node, from_side, to_node, to_side)
    path = (
        f"M {format(start[0], '.15g')} {format(start[1], '.15g')} "
        f"C {format(control_1[0], '.15g')} {format(control_1[1], '.15g')} "
        f"{format(control_2[0], '.15g')} {format(control_2[1], '.15g')} "
        f"{format(end[0], '.15g')} {format(end[1], '.15g')}"
    )
    return path, start, end


def render_canvas_svg(data: object) -> bytes:
    if not isinstance(data, dict) or not isinstance(data.get("nodes", []), list):
        raise ValueError("Invalid canvas: expected a JSON object with a nodes list")
    if not isinstance(data.get("edges", []), list):
        raise ValueError("Invalid canvas: expected edges to be a list")

    nodes = [node for node in data["nodes"] if isinstance(node, dict)]
    edges = [edge for edge in data["edges"] if isinstance(edge, dict)]
    if nodes:
        min_x = min(canvas_number(node, "x") for node in nodes)
        min_y = min(canvas_number(node, "y") for node in nodes)
        max_x = max(
            canvas_number(node, "x") + max(0, canvas_number(node, "width")) for node in nodes
        )
        max_y = max(
            canvas_number(node, "y") + max(0, canvas_number(node, "height")) for node in nodes
        )
    else:
        min_x = min_y = max_x = max_y = 0

    node_by_id = {str(node["id"]): node for node in nodes if isinstance(node.get("id"), str)}
    edge_points: list[tuple[float, float]] = []
    for edge in edges:
        from_node = node_by_id.get(str(edge.get("fromNode")))
        to_node = node_by_id.get(str(edge.get("toNode")))
        if from_node is not None and to_node is not None:
            edge_points.extend(
                canvas_edge_geometry(from_node, edge.get("fromSide"), to_node, edge.get("toSide"))
            )
    if edge_points:
        min_x = min(min_x, *(point[0] for point in edge_points))
        min_y = min(min_y, *(point[1] for point in edge_points))
        max_x = max(max_x, *(point[0] for point in edge_points))
        max_y = max(max_y, *(point[1] for point in edge_points))

    margin = 40
    svg_width = max(1, max_x - min_x + margin * 2)
    svg_height = max(1, max_y - min_y + margin * 2)
    view_min_x = min_x - margin
    view_min_y = min_y - margin
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{format(svg_width, ".15g")}" height="{format(svg_height, ".15g")}" '
        f'viewBox="{format(view_min_x, ".15g")} {format(view_min_y, ".15g")} '
        f'{format(svg_width, ".15g")} {format(svg_height, ".15g")}">',
        '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" '
        'orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" '
        'fill="#64748b"/></marker></defs>',
        f'<rect x="{format(view_min_x, ".15g")}" y="{format(view_min_y, ".15g")}" '
        f'width="{format(svg_width, ".15g")}" height="{format(svg_height, ".15g")}" '
        'fill="#ffffff"/>',
    ]

    for node in nodes:
        if node.get("type") != "group":
            continue
        x, y, width, height = canvas_node_geometry(node, 0, 0)
        color = canvas_node_color(node, "#94a3b8")
        parts.append(
            f'<rect x="{format(x, ".15g")}" y="{format(y, ".15g")}" '
            f'width="{format(width, ".15g")}" height="{format(height, ".15g")}" '
            f'rx="12" fill="{color}" fill-opacity="0.12" stroke="{color}" '
            'stroke-width="2" stroke-dasharray="8 5"/>'
        )
        label = canvas_node_label(node)
        if label:
            parts.append(
                f'<text x="{format(x + 16, ".15g")}" y="{format(y - 10, ".15g")}" '
                'font-family="Arial, sans-serif" font-size="18" font-weight="bold" '
                f'fill="{color}">{canvas_svg_inline_content(label)}</text>'
            )

    for edge in edges:
        from_node = node_by_id.get(str(edge.get("fromNode")))
        to_node = node_by_id.get(str(edge.get("toNode")))
        if from_node is None or to_node is None:
            continue
        path, (start_x, start_y), (end_x, end_y) = canvas_edge_path(
            from_node,
            edge.get("fromSide"),
            to_node,
            edge.get("toSide"),
        )
        parts.append(
            f'<path d="{path}" fill="none" stroke="#64748b" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round" marker-end="url(#arrow)"/>'
        )
        label = edge.get("label")
        if isinstance(label, str) and label:
            label_x = (start_x + end_x) / 2
            label_y = (start_y + end_y) / 2 - 5
            parts.append(
                f'<text x="{format(label_x, ".15g")}" y="{format(label_y, ".15g")}" '
                'text-anchor="middle" font-family="Arial, sans-serif" font-size="14" '
                'fill="#334155" paint-order="stroke" stroke="#ffffff" stroke-width="5">'
                f"{canvas_svg_inline_content(label)}</text>"
            )

    for node in nodes:
        if node.get("type") == "group":
            continue
        x, y, width, height = canvas_node_geometry(node, 0, 0)
        color = canvas_node_color(node, "#64748b")
        parts.append(
            f'<rect x="{format(x, ".15g")}" y="{format(y, ".15g")}" '
            f'width="{format(width, ".15g")}" height="{format(height, ".15g")}" '
            f'rx="8" fill="#ffffff" stroke="{color}" stroke-width="2"/>'
        )
        lines = canvas_text_lines(canvas_node_label(node), width - 24)
        line_height = 22
        first_y = y + height / 2 - (len(lines) - 1) * line_height / 2
        code_line_indexes = [
            index for index, line in enumerate(lines) if any(part.pre for part in line)
        ]
        if code_line_indexes:
            code_start = code_line_indexes[0]
            code_end = code_start
            for index in code_line_indexes[1:]:
                if index == code_end + 1:
                    code_end = index
                    continue
                parts.append(
                    f'<rect x="{format(x + 10, ".15g")}" '
                    f'y="{format(first_y + code_start * line_height - 13, ".15g")}" '
                    f'width="{format(max(0, width - 20), ".15g")}" '
                    f'height="{format((code_end - code_start + 1) * line_height + 6, ".15g")}" '
                    'rx="4" fill="#f1f5f9"/>'
                )
                code_start = code_end = index
            parts.append(
                f'<rect x="{format(x + 10, ".15g")}" '
                f'y="{format(first_y + code_start * line_height - 13, ".15g")}" '
                f'width="{format(max(0, width - 20), ".15g")}" '
                f'height="{format((code_end - code_start + 1) * line_height + 6, ".15g")}" '
                'rx="4" fill="#f1f5f9"/>'
            )
        for index, line in enumerate(lines):
            code_line = any(part.pre for part in line)
            text_x = x + 18 if code_line else x + width / 2
            text_anchor = "start" if code_line else "middle"
            preserve_space = ' xml:space="preserve"' if code_line else ""
            parts.append(
                f'<text x="{format(text_x, ".15g")}" '
                f'y="{format(first_y + index * line_height, ".15g")}" '
                f'text-anchor="{text_anchor}" dominant-baseline="middle"{preserve_space} '
                'font-family="Arial, sans-serif" font-size="16" fill="#1e293b">'
                f"{canvas_svg_line_content(line)}</text>"
            )

    parts.append("</svg>")
    return "\n".join(parts).encode("utf-8")


def render_canvas_file(path: Path) -> bytes:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid canvas JSON in {path}: {error}") from error
    return render_canvas_svg(data)


def convert_obsidian_canvas_embeds(
    text: str,
    md_path: str,
    base_dir: str,
    vault_root: Path,
    image_refs: dict[str, ImageRef],
) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        path_text, _, suffix = target.partition("|")
        path_text = path_text.strip()
        if not is_canvas_path(path_text):
            return match.group(0)

        resolved = resolve_canvas_path(path_text, base_dir, vault_root)
        if resolved is None:
            raise FileNotFoundError(f"Canvas file not found: {path_text}")

        token = f"OPCCANVASTOKEN{len(image_refs)}"
        image_refs[token] = ImageRef(
            path_text,
            canvas_attachment_name(md_path, path_text),
            int(suffix.strip()) if suffix.strip().isdigit() else None,
            render_canvas_file(resolved),
            make_attachment_name(md_path, path_text),
            resolved.read_bytes(),
        )
        return f"![]({escape_markdown_url(token)})"

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
            f'<ac:structured-macro ac:name="plantuml" '
            f'ac:schema-version="1" ac:macro-id="{macro_id}">'
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


def escape_xml_attribute(text: str) -> str:
    return escape_xml(text).replace('"', "&quot;").replace("'", "&apos;")


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

    pattern = re.compile(
        r'<pre(?:\s[^>]*)?><code(?:\s+class="language-(\w+)")?>(.*?)</code></pre>',
        re.DOTALL,
    )
    return pattern.sub(replace, html)


def fix_xhtml(html: str) -> str:
    html = re.sub(r"<br>", "<br/>", html)
    html = re.sub(r"<hr>", "<hr/>", html)
    html = re.sub(r"<img\s+([^>]*?[^/])>", r"<img \1/>", html)
    return html


HTML_LIST_TAG_RE = re.compile(r"<(/?)(ul|li)\b[^>]*>", re.IGNORECASE)
HTML_UL_OPEN_RE = re.compile(r"<ul\b[^>]*>", re.IGNORECASE)
TASK_MARKER_RE = re.compile(r"^\s*\[([ xX])\](?:\s+|$)")


def find_matching_list(html: str, opening: re.Match[str]) -> tuple[int, int] | None:
    depth = 1
    for match in HTML_LIST_TAG_RE.finditer(html, opening.end()):
        if match.group(2).lower() != "ul":
            continue
        if match.group(1):
            depth -= 1
            if depth == 0:
                return match.start(), match.end()
        else:
            depth += 1
    return None


def find_immediate_list_items(inner: str) -> list[HtmlListItem]:
    items: list[HtmlListItem] = []
    list_depth = 0
    current: tuple[int, int, str] | None = None

    for match in HTML_LIST_TAG_RE.finditer(inner):
        tag = match.group(2).lower()
        if tag == "ul":
            list_depth += -1 if match.group(1) else 1
            continue
        if list_depth != 0:
            continue
        if match.group(1):
            if current is not None:
                start, content_start, start_tag = current
                items.append(
                    HtmlListItem(
                        start,
                        content_start,
                        match.start(),
                        match.end(),
                        start_tag,
                        match.group(0),
                    )
                )
                current = None
        elif current is None:
            current = (match.start(), match.end(), match.group(0))
    return items


def task_marker(content: str) -> re.Match[str] | None:
    return TASK_MARKER_RE.match(content)


def convert_task_lists(html: str) -> str:
    next_task_id = 1

    def render_task(body_source: str, marker: re.Match[str]) -> str:
        nonlocal next_task_id
        status = "complete" if marker.group(1).lower() == "x" else "incomplete"
        task_id = next_task_id
        next_task_id += 1
        body = convert_fragment(body_source)
        return "\n".join(
            [
                "<ac:task>",
                f"<ac:task-id>{task_id}</ac:task-id>",
                f"<ac:task-uuid>{uuid.uuid4()}</ac:task-uuid>",
                f"<ac:task-status>{status}</ac:task-status>",
                f"<ac:task-body>{body}</ac:task-body>",
                "</ac:task>",
            ]
        )

    def render_task_list(tasks: list[str]) -> str:
        return "\n".join(["<ac:task-list>", *tasks, "</ac:task-list>"])

    def render_list(
        source: str,
        opening: re.Match[str],
        closing_start: int,
        closing_end: int,
    ) -> str:
        inner = source[opening.end() : closing_start]
        items = find_immediate_list_items(inner)
        if not items:
            return opening.group(0) + convert_fragment(inner) + source[closing_start:closing_end]

        item_data: list[tuple[HtmlListItem, re.Match[str] | None, str]] = []
        for item in items:
            content = inner[item.content_start : item.content_end]
            marker = task_marker(content)
            body_source = content[marker.end() :] if marker else content
            item_data.append((item, marker, body_source))

        if all(marker is not None for _, marker, _ in item_data):
            return render_task_list(
                [render_task(body, marker) for _, marker, body in item_data if marker]
            )

        blocks: list[str] = []
        index = 0
        while index < len(item_data):
            item, marker, body = item_data[index]
            if marker is not None:
                tasks: list[str] = []
                while index < len(item_data) and item_data[index][1] is not None:
                    _, task, task_body = item_data[index]
                    assert task is not None
                    tasks.append(render_task(task_body, task))
                    index += 1
                blocks.append(render_task_list(tasks))
                continue

            normal_items = [f"{item.start_tag}{convert_fragment(body)}{item.end_tag}"]
            index += 1
            while index < len(item_data) and item_data[index][1] is None:
                normal_item, _, normal_body = item_data[index]
                normal_items.append(
                    f"{normal_item.start_tag}{convert_fragment(normal_body)}{normal_item.end_tag}"
                )
                index += 1
            blocks.append(
                opening.group(0) + "".join(normal_items) + source[closing_start:closing_end]
            )

        return "\n".join(blocks)

    def convert_fragment(fragment: str) -> str:
        parts: list[str] = []
        cursor = 0
        while True:
            opening = HTML_UL_OPEN_RE.search(fragment, cursor)
            if opening is None:
                parts.append(fragment[cursor:])
                return "".join(parts)
            bounds = find_matching_list(fragment, opening)
            if bounds is None:
                parts.append(fragment[cursor:])
                return "".join(parts)
            closing_start, closing_end = bounds
            parts.append(fragment[cursor : opening.start()])
            parts.append(render_list(fragment, opening, closing_start, closing_end))
            cursor = closing_end

    return convert_fragment(html)


def collect_local_image_attachments(
    html: str, base_dir: str, vault_root: Path, image_refs: dict[str, ImageRef]
) -> list[tuple[str, bytes]]:
    attachments: list[tuple[str, bytes]] = []

    def collect(match: re.Match[str]) -> str:
        src = match.group(1)
        if src.startswith(("http://", "https://", "data:")):
            return ""
        src = src.replace("%20", " ")
        image_ref = image_refs.get(src)
        if image_ref and image_ref.data is not None:
            attachments.append((image_ref.attachment_name, image_ref.data))
            if image_ref.related_attachment_name and image_ref.related_data is not None:
                attachments.append((image_ref.related_attachment_name, image_ref.related_data))
            return ""
        resolved = resolve_attachment_path(
            image_ref.source if image_ref else src, base_dir, vault_root
        )
        if resolved is not None:
            attachment_name = image_ref.attachment_name if image_ref else resolved.name
            attachments.append((attachment_name, resolved.read_bytes()))
        return ""

    re.sub(r'<img\s+[^>]*src="([^"]+)"', collect, html)
    return attachments


def convert_local_images_to_ac(
    html: str, base_dir: str, vault_root: Path, image_refs: dict[str, ImageRef]
) -> str:
    def replace(match: re.Match[str]) -> str:
        full_tag = match.group(0)
        src = match.group(1)
        if src.startswith(("http://", "https://", "data:")):
            return full_tag
        src = src.replace("%20", " ")
        image_ref = image_refs.get(src)
        if image_ref and image_ref.data is not None:
            attrs = ' ac:height="auto"'
            if image_ref.width is not None:
                attrs += f' ac:width="{image_ref.width}"'
            escaped_name = escape_xml(image_ref.attachment_name)
            image = f'<ac:image{attrs}><ri:attachment ri:filename="{escaped_name}"/></ac:image>'
            if image_ref.related_attachment_name:
                related_name = escape_xml(image_ref.related_attachment_name)
                image += (
                    "<br/><ac:link>"
                    f'<ri:attachment ri:filename="{related_name}"/>'
                    "<ac:plain-text-link-body><![CDATA[📎 Оригинальный .canvas]]>"
                    "</ac:plain-text-link-body>"
                    "</ac:link>"
                )
            return image
        resolved = resolve_attachment_path(
            image_ref.source if image_ref else src, base_dir, vault_root
        )
        if resolved is None:
            return full_tag
        attrs = ' ac:height="auto"'
        if image_ref and image_ref.width is not None:
            attrs += f' ac:width="{image_ref.width}"'
        attachment_name = image_ref.attachment_name if image_ref else resolved.name
        escaped_name = escape_xml(attachment_name)
        return f'<ac:image{attrs}><ri:attachment ri:filename="{escaped_name}"/></ac:image>'

    return re.sub(r'<img\s+[^>]*src="([^"]+)"[^>]*>', replace, html)


def render_markdown(text: str) -> str:
    return markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        output_format="html",
    )


def remove_frontmatter(text: str) -> str:
    return re.sub(r"\A(?:\ufeff)?---\s*\n.*?\n---\s*(?:\n|\Z)", "", text, count=1, flags=re.DOTALL)


def collect_attachments(md_path: str, plantuml_server: str | None = None) -> ConvertResult:
    md_path = os.path.abspath(md_path)
    sync_if_fuse_path(md_path)
    if not os.path.isfile(md_path):
        raise FileNotFoundError(f"File not found: {md_path}")

    base_dir = os.path.dirname(md_path)
    vault_root = find_vault_root(base_dir)
    text = remove_frontmatter(Path(md_path).read_text(encoding="utf-8"))
    text, image_refs = convert_obsidian_image_embeds(text, md_path)
    text = convert_obsidian_canvas_embeds(text, md_path, base_dir, vault_root, image_refs)

    text, plantuml_replacements = extract_plantuml_macros(text)

    html = render_markdown(text)
    html = fix_xhtml(html)
    html = convert_task_lists(html)
    html = restore_plantuml_macros(html, plantuml_replacements)
    html = convert_code_blocks(html)

    image_attachments = collect_local_image_attachments(html, base_dir, vault_root, image_refs)
    html = convert_local_images_to_ac(html, base_dir, vault_root, image_refs)

    attachments: list[AttachmentJson] = [
        {"name": name, "data_b64": base64.b64encode(data).decode("ascii")}
        for name, data in image_attachments
    ]
    return {"body": html, "attachments": attachments}
