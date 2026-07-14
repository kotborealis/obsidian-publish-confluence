from __future__ import annotations

import argparse
import subprocess
import sys

from .publish import cmd_check, config_from_env, publish_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish Obsidian Markdown to Confluence over Kerberos")
    parser.add_argument("file", nargs="?", help="Path to markdown file")
    parser.add_argument("--title", help="Page title")
    parser.add_argument("--space", help="Confluence space key")
    parser.add_argument("--parent-id", help="Parent page ID")
    parser.add_argument("--base-url", help="Confluence base URL")
    parser.add_argument("--check", action="store_true", help="Show mapping status")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = config_from_env()

    if args.check:
        return cmd_check(config.mapping_file)
    if not args.file:
        parser.error("the following arguments are required: file")

    try:
        page_url = publish_markdown(
            config,
            args.file,
            title=args.title,
            space_key=args.space,
            parent_id=args.parent_id,
            base_url=args.base_url,
        )
    except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"Done: {page_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
