# Document Archive
<!-- Inherits global rules from ~/.claude/CLAUDE.md -->
<!-- Copy this template into your archive directory as CLAUDE.md and adapt it. -->

---

## Tool

DocOrganizer lives at `~/sources/docorganizer/`. Run from this directory:

```bash
docorganizer --dry-run       # preview proposed changes, nothing is moved
docorganizer --propose       # propose, validate, save to proposals.json
docorganizer --execute FILE  # execute proposals from JSON file
docorganizer                 # interactive: propose, confirm, execute
```

Archive-specific configuration (people, countries, tags, prompt): `docorganizer.yaml`

---

## Conventions

### Naming Pattern

```
[Date] - [Sender] - [Topic] - [Person].[ext]
```

**Field definitions:**
- `Date` — document's issue date in ISO 8601 (`YYYY-MM-DD` or `YYYY-MM` or `YYYY` depending on precision available). Placed first for chronological sorting.
- `Sender` — who issued the document, natural name, spaces allowed. Abbreviate only when unambiguous. Keep sender names in their original language.
- `Topic` — what the document is about. Always in English. Embed a period or year **only when it differs from the Date field** — i.e., the document covers a distinct reporting period (`Steuerbescheid 2023` issued 2024-09-01, `Kontoauszug 2025-12` issued 2026-01-03). When the topic period matches the issue date, omit it.
- `Person` — who the document primarily *concerns* (not necessarily the addressee). Use first name from `people` config. Use `Unknown` when no specific person applies.
- `ext` — original file extension, preserved as-is.

**Field separator:** ` - ` (space-dash-space). Spaces are allowed within fields.

**All fields are always present — no omissions.**

**Placeholders:**

| Field | Placeholder | Notes |
|---|---|---|
| Sender | `Unknown` | |
| Person | `Unknown` | Covers both "shared/org-wide" and "no specific person" |
| Date | `Undated` | Never guess — update if date is found later |
| Topic | *(none)* | Must be resolved before filing — do not file an ununderstood document |

Placeholders are always in English regardless of document language. `Sender` follows the document's natural language.

### Folder Assignment
- One primary folder per document — no symlinks or copies across folders
- When a document spans multiple topics, file under the primary topic; use tags for secondary relevance
- `_archive/` mirrors the main folder structure: superseded files move to `_archive/[original-path]/`
- **Three-document rule:** a new folder is created only when three or more documents share a topic with no existing folder. Until the threshold is met, file under `Unsorted/` (within the appropriate country folder if countries are configured).

### Tags

macOS Finder tags, applied via `xattr`. Title Case throughout. See `docorganizer.yaml` for controlled vocabulary.

- **Tax check is mandatory:** every document must be evaluated for tax relevance during classification.
- Tags are applied in addition to folder assignment, not instead of it
- **Three-document rule:** a new tag is added only when the same retrieval need has arisen for at least three documents and cannot be served by filename or folder alone. Propose new tags — never add them unilaterally.
- **Implementation:** macOS Finder tags stored in `com.apple.metadata:_kMDItemUserTags` as a binary plist. Use `xattr` + `plistlib`.

### Folder Naming
- Title Case, spaces allowed (`Tax Documents`, `Krankenversicherung`)
- Derived from document content — never invented in advance
- As short as unambiguously descriptive — prefer `Steuer` over `Steuerdokumente und Bescheide`
- **Three-document rule:** create a new folder only when three or more documents share a topic with no existing folder.

### Intake Log

Location: `intake-log.md` — append-only, newest entries at the top.
Batch ID format: `YYYY-MM-DD-N` (N resets to 1 each day).

Confidence levels: **High** (all fields clear) / **Medium** (one field inferred; reason in Notes) / **Low** (significant uncertainty; requires manual review).

### File Operations
- **Rename + move:** `pathlib.Path.rename()` to target path in a single operation — do not rename in inbox first
- Create intermediate folders with `Path.mkdir(parents=True, exist_ok=True)`
- **Archive:** superseded files → `_archive/[original-relative-path]/`
- **Never use `shutil.move()` across filesystems** — verify source and target are on the same volume first

### Inbox Processing Workflow

Triggered **manually only**. Maximum **10 files per run**.

```
1. SCAN     — list inbox/, process oldest files, report remainder
2. EXTRACT  — extract full text; flag and skip unreadable files (encrypted, corrupted, image-only)
3. PROPOSE  — present a table for every file in the batch:
              | # | Old Name | New Name | Folder | Tags | Confidence |
              Leave New Name empty when open decisions block naming. Follow table with open decisions / flags.
4. CONFIRM  — user approves / corrects / skips per file; skipped files stay in inbox unchanged
5. EXECUTE  — rename+move, apply tags, append to intake-log.md
6. REPORT   — summary: X processed, Y skipped, Z flagged; remaining inbox count if non-zero
```

---

## Never Do

- Never delete any file — move to `_archive/` and log the reason
- Never rename in bulk without showing a dry-run diff first and getting explicit confirmation
- Never process a document whose content cannot be read (encrypted, corrupted) — flag it instead
- Never log document content — log metadata only (filenames, paths, confidence, notes)
- Do not resolve open decisions unilaterally — surface them and ask
