# obsidian-publish-confluence

[![PyPI](https://img.shields.io/pypi/v/obsidian-publish-confluence)](https://pypi.org/project/obsidian-publish-confluence/)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://pypi.org/project/obsidian-publish-confluence/)
[![Build](https://github.com/kotborealis/obsidian-publish-confluence/actions/workflows/build.yml/badge.svg)](https://github.com/kotborealis/obsidian-publish-confluence/actions/workflows/build.yml)
[![License: WTFPL](https://img.shields.io/badge/license-WTFPL-brightgreen.svg)](LICENSE)

Publish Obsidian-flavored Markdown to Confluence over Kerberos.

Versioning is derived from Git tags for releases.

## Features

- Create or update Confluence pages from Markdown.
- Track `markdown -> page_id` mappings locally.
- Upload attachments on both page create and update.
- Convert Obsidian image embeds like `![[image.png]]` and `![[image.png|400]]`.
- Convert fenced code blocks to Confluence code macros.
- Convert PlantUML blocks to the native Confluence `plantuml` macro.

## Requirements

- Python 3.10+
- `curl`
- `kinit` and `klist`
- A Confluence instance that accepts Kerberos auth via `curl --negotiate`

## Installation

```bash
pip install obsidian-publish-confluence
```

For local development:

```bash
python -m pip install -e .
```

## Configuration

Set these environment variables:

- `OBSIDIAN_PUBLISH_CONFLUENCE_BASE_URL`
- `OBSIDIAN_PUBLISH_CONFLUENCE_SPACE`
- `OBSIDIAN_PUBLISH_CONFLUENCE_PARENT_ID`
- `OBSIDIAN_PUBLISH_CONFLUENCE_MAPPING_FILE` (optional)

Defaults:

- `OBSIDIAN_PUBLISH_CONFLUENCE_MAPPING_FILE` defaults to `~/.config/obsidian-publish-confluence/mapping.json`

## Usage

```bash
export OBSIDIAN_PUBLISH_CONFLUENCE_BASE_URL="https://confluence.example.com"
export OBSIDIAN_PUBLISH_CONFLUENCE_SPACE="DOCS"
export OBSIDIAN_PUBLISH_CONFLUENCE_PARENT_ID="123456"

obsidian-publish-confluence note.md
obsidian-publish-confluence note.md --title "Custom title"
obsidian-publish-confluence note.md --space DEV --parent-id 987654
obsidian-publish-confluence --check
```

## Kerberos

The CLI expects a valid Kerberos ticket:

```bash
kinit YOUR_LOGIN@REALM.EXAMPLE
```

## PlantUML

PlantUML fenced blocks are converted to the native Confluence `plantuml` macro.

This requires a Confluence instance with a compatible `plantuml` macro installed.

## Release versioning

The package version comes from the Git tag used for the release.
For PyPI releases, create a tag like `v0.1.0` before publishing a GitHub Release.

## Limitations

- Only image-style Obsidian embeds are converted to Confluence attachments.
- Non-image wiki links stay plain text.
- PDF embeds are not converted.
- Auth mode is Kerberos-only.
- If a note is not already tracked in the mapping file, a new Confluence page is created instead of matching an existing page by title.
- PlantUML publishing depends on the Confluence-side `plantuml` macro being available.

## Publishing

This repository includes GitHub Actions for:

- building distributions on push and pull request
- publishing to PyPI on GitHub Release `published`

PyPI publishing is configured for Trusted Publishing.
