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
