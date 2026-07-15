# AGENTS.md

## Project purpose

`obsidian-publish-confluence` publishes Obsidian-flavored Markdown to Confluence over Kerberos.

Main entrypoints:

- `src/obsidian_publish_confluence/cli.py`
- `src/obsidian_publish_confluence/publish.py`
- `src/obsidian_publish_confluence/convert.py`

## Local development

- Use Python 3.10+.
- Install locally with `pipx install --force .` or `python -m pip install -e .`.
- Build with `python -m build`.
- Run tests with `PYTHONPATH=src python -m unittest discover -s tests`.

## Release flow

- Package version is derived from Git tags via `setuptools-scm`.
- Release tags must look like `v0.1.0`, `v0.1.1`, etc.
- GitHub Release `published` triggers `.github/workflows/publish.yml`.
- PyPI publishing uses Trusted Publishing.

## Confluence behavior

- Auth mode is Kerberos-only.
- Existing pages are updated only when the markdown file is already tracked in the mapping file.
- If there is no mapping entry, the tool creates a new page instead of matching by title.
- Attachments must be uploaded on both create and update.
- For `curl` POST/PUT requests, JSON payload must be sent with `--data-binary @-`.

## Obsidian behavior

- Image embeds like `![[image.png]]` and `![[image.png|400]]` are converted to Confluence attachments.
- Non-image wiki links stay as text.
- `plantuml` / `puml` fenced blocks are converted directly to the native Confluence `plantuml` macro.

## Environment variables

- `OBSIDIAN_PUBLISH_CONFLUENCE_BASE_URL`
- `OBSIDIAN_PUBLISH_CONFLUENCE_SPACE`
- `OBSIDIAN_PUBLISH_CONFLUENCE_PARENT_ID`
- `OBSIDIAN_PUBLISH_CONFLUENCE_MAPPING_FILE`

## Things to avoid

- Do not reintroduce title-based page adoption.
- Do not hardcode company-specific defaults into the published package.
- Do not reintroduce attachment-based PlantUML rendering.
- Do not break the existing mapping format unless migration is added deliberately.
