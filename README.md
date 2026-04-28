# DocOrganizer

Classifies, renames, and files documents from an inbox into a structured archive. Uses Claude API for document classification. Each archive is an independent directory with its own configuration.

## Installation

```bash
cd /path/to/docorganizer
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the `docorganizer` CLI command (available when the venv is active).

### Optional: OCR fallback

For scanned PDFs and PDFs whose text is rendered as vector outlines (no
selectable glyphs), the extractor falls back to OCR via `tesseract` + `pdftoppm`.
Install both if you expect such documents:

```bash
brew install tesseract tesseract-lang poppler
```

`tesseract-lang` adds German/Latvian language data (the extractor uses `deu+eng+lav`).
If either binary is missing, OCR is skipped and the file is flagged as unreadable.

## Setting up a new archive

An archive is any directory with a `docorganizer.yaml` config file. The tool reads this file from the current working directory.

### 1. Create the directory structure

```bash
mkdir -p /path/to/MyArchive/inbox
cd /path/to/MyArchive
```

The only required subdirectory is `inbox/` — everything else is created automatically.

### 2. Create `docorganizer.yaml`

```yaml
# Required
name: My Archive                    # human-readable name (for logs)

# Optional — top-level folder for filed documents.
# When set, filed documents go into root_folder/Country/Topic/.
# When omitted, topic folders live directly in the archive root (flat layout).
root_folder: Documents

# People — first name as key, full name variants as values
# The classifier uses these to normalize person references in documents.
# Omit this section entirely if person tracking is not needed.
people:
  Alice:
    - "Alice Smith"
    - "A. Smith"
  Bob:
    - "Bob Smith"
    - "Robert Smith"

# Countries — used for folder structure: root_folder/Country/Topic/
# Omit if the archive is not organized by country.
countries:
  - Germany
  - Switzerland

# Tags — controlled vocabulary for macOS Finder tags
# Key = tag name, Value = description (injected into the classification prompt)
tags:
  Tax: "Documents relevant for tax filing"
  Contract: "Signed contracts and agreements"
  Expiring: "Documents with expiration dates requiring monitoring"

# Optional: tags applied to every filed document regardless of classification
# mandatory_tags:
#   - processed

# Prompt context — injected into the Claude classification prompt.
# Use this for archive-specific instructions, naming preferences, etc.
prompt_context: >-
  Classify documents for a personal archive.
  The archive owner is Alice Smith — always use "Alice".
```

### 3. Set your API key

Either create a `.env` file in the archive directory:

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
```

Or set the environment variable in your shell profile.

### 4. (Optional) Add a CLAUDE.md

If you use Claude Code to work in this archive directory interactively, add a `CLAUDE.md` with the naming convention and workflow rules.

## Usage

Always run from the archive directory:

```bash
cd /path/to/MyArchive
```

### Workflow: propose, review, execute

```bash
# Step 1: Scan inbox, extract text, classify via Claude API, save proposals
docorganizer --propose

# Step 2: Review proposals.json (edit if needed)

# Step 3: Apply approved proposals (rename, move, tag)
docorganizer --execute proposals.json
```

### Other modes

```bash
docorganizer --dry-run               # preview only, nothing saved or moved
docorganizer                         # interactive: propose, confirm, execute in one go
docorganizer --validate proposals.json   # auto-correct and validate saved proposals
```

### Refactoring filed documents

```bash
# Plan moves matching a glob pattern to a new folder
docorganizer --refactor --match "Family/Germany/Unsorted/*.pdf" --to "Family/Germany/Steuer"

# Review refactor.json, then apply
docorganizer --refactor-execute refactor.json
```

### Explicit archive path

```bash
docorganizer --archive /path/to/MyArchive --propose
```

## Configuration reference

| Field | Required | Default | Description |
|---|---|---|---|
| `name` | yes | — | Archive name (for logs) |
| `root_folder` | no | *(flat)* | Top-level folder for filed documents. Omit for flat layout. |
| `people` | no | `{}` | Person name variants for normalization |
| `countries` | no | `[]` | Optional intermediate folder grouping (e.g. by country). Omit for flat `root_folder/topic/` layout. |
| `tags` | no | `{}` | Controlled tag vocabulary (name: description) |
| `mandatory_tags` | no | `[]` | Tags applied to every filed document |
| `prompt_context` | no | `""` | Instructions injected into classification prompt |
| `inbox_dir` | no | `inbox` | Inbox directory name |
| `archive_dir` | no | `_archive` | Archive directory name |
| `intake_log` | no | `intake-log.md` | Intake log filename |
| `todo_dir` | no | `ToDo` | Directory for files needing manual review |
| `non_archive_dir` | no | *(disabled)* | Path (relative to archive root) where the classifier may route documents it judges as non-archivable (templates, working drafts, blank scans). When unset, every readable document must be filed. |

## File naming convention

All documents are renamed to:

```
[Date] - [Sender] - [Topic] - [Person].[ext]
```

- **Date**: ISO 8601 (`YYYY-MM-DD`, `YYYY-MM`, or `YYYY`). `Undated` if unknown.
- **Sender**: who issued the document. Natural name, original language.
- **Topic**: what the document is about. Always in English.
- **Person**: who the document concerns. First name from `people` config, or `Unknown`.

## Folder structure

With countries configured:

```
MyArchive/
  docorganizer.yaml
  .env
  inbox/
  Documents/               # root_folder
    Germany/               # country
      Invoices/            # topic folder
      Unsorted/            # default when no folder matches
    Switzerland/
      ...
  _archive/
  intake-log.md
```

Without countries:

```
MyArchive/
  docorganizer.yaml
  .env
  inbox/
  Documents/               # root_folder
    Invoices/              # topic folder directly under root
    Contracts/
    Unsorted/
  _archive/
  intake-log.md
```

Without root_folder (flat layout — topic folders at archive root):

```
MyArchive/
  docorganizer.yaml
  .env
  inbox/
  Germany/                 # country folder directly at root
    Invoices/
    Contracts/
    Unsorted/
  _archive/
  intake-log.md
```

## Per-archive plugin

When an archive directory contains `docorganizer_plugin.py` next to its
`docorganizer.yaml`, that module is auto-loaded and its hooks are called at
designated points in the pipeline. This keeps the core generic — vendor-specific
quirks (sender name canonicalization, paired-document alignment, custom
supersedence rules) live with each archive instead of in the tool.

Two optional hooks:

```python
# my_archive/docorganizer_plugin.py
from docorganizer.cli import Proposal


def post_propose(proposal: Proposal, text: str) -> Proposal:
    """Called once per proposal after LLM classification.
    Use for single-document rewrites: canonicalize sender name,
    derive disambiguator from text, set document_id, etc.
    """
    if proposal.sender == "Acme Inc, formerly known as Foo":
        proposal.sender = "Acme Inc"
    return proposal


def post_batch(proposals: list[Proposal]) -> list[Proposal]:
    """Called once with the full batch, after supersedence detection.
    Use for cross-document patterns: invoice/receipt pairing,
    sibling normalization, etc.
    """
    # … cross-document logic …
    return proposals
```

Both hooks are optional — define only what you need. A broken plugin produces
a stderr warning and is silently skipped (it never blocks document processing).

## Built-in cross-document features

These work without any plugin — they are part of the core pipeline:

- **Text-hash deduplication** — files that share rendered text but differ in
  PDF metadata (timestamps, producer string, re-renders) are detected as
  duplicates and routed to `_archive/inbox/` alongside byte-identical dupes.
- **Supersedence by `document_id`** — when the classifier extracts a stable
  identifier (offer #, contract #, invoice #) and an older file in the archive
  has the same ID in its name, the older file is moved to `_archive/` with a
  ` Superseded` suffix when the new one is filed.
- **Disambiguator field** — the classifier may attach a short signal
  (`USD 11.35`, `Q1 2026`, `#2104541`) to distinguish documents that would
  otherwise produce identical filenames within the same `(date, sender)`.
- **Non-archive routing** — when `non_archive_dir` is configured, the
  classifier may route working drafts, templates, blank scans, etc. to that
  directory instead of forcing them into the partner-folder hierarchy.

## Development

```bash
cd /path/to/docorganizer
source .venv/bin/activate
pip install pytest
pytest -v
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required. Claude API key. |
| `DOCORG_MODEL` | `claude-haiku-4-5-20251001` | Model used for classification. |
