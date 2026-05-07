#!/usr/bin/env python3
"""DocOrganizer — inbox processing CLI.

Usage:
    docorganizer                                         # interactive: propose, confirm, execute
    docorganizer --dry-run                               # propose only, print to screen
    docorganizer --propose                               # propose, validate, save to proposals.json
    docorganizer --validate FILE                         # validate and auto-correct proposals
    docorganizer --execute FILE                          # execute proposals from JSON file
    docorganizer --refactor --match PATTERN --to FOLDER  # plan folder refactor
    docorganizer --refactor-execute FILE                 # apply refactor plan
    docorganizer --archive /path/to/archive ...          # explicit archive root (default: CWD)
"""

import hashlib
import json
import os
import plistlib
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import anthropic
import xattr as xattr_lib
from dotenv import load_dotenv

from docorganizer.charset import path_to_ascii, to_ascii
from docorganizer.config import ArchiveConfig, ArchiveContext, date_to_subfolder, load_config
from docorganizer.extractor import (
    SUPPORTED_EXTENSIONS,
    ExtractionError,
    UnsupportedFileType,
    compute_text_hash,
    extract_text,
)
from docorganizer.translator import (
    TranslationError,
    detect_language,
    translate_document,
)
from docorganizer.validator import (
    build_sender_registry,
    format_validation_report,
    sanitize_field,
    validate_proposals,
)

# ── Constants ────────────────────────────────────────────────────────────────

MODEL = os.getenv("DOCORG_MODEL", "claude-haiku-4-5-20251001")
MAX_FILES_PER_RUN = 10


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class Proposal:
    original_path: Path
    sender: str
    topic: str
    person: str
    date: str
    country: str  # country name or "" for country-independent
    folder_topic: str  # suggested folder name (may be overridden to Unsorted)
    target_folder: str  # final resolved relative path from archive root
    tags: list[str]
    confidence: str  # High, Medium, Low
    notes: str
    status: str = "proposed"  # proposed, approved, corrected, skipped
    translated_path: Path | None = None  # companion translated document
    filename_override: str | None = None  # explicit filename set by user; bypasses synthesis
    file_hash: str | None = None  # SHA-256 of source file; used to recover from corrupted original_path
    disambiguator: str | None = None  # short signal appended to topic (e.g. "USD 11.35", "Q1 2026")
    non_archive_reason: str | None = None  # if set, route to config.non_archive_dir instead of partner folder
    document_id: str | None = None  # stable identifier (offer #, contract #, invoice #) — used for supersedence detection
    supersedes: list[Path] = field(default_factory=list)  # archived files to move to _archive/ when this one is filed

    @property
    def synthesized_filename(self) -> str:
        """The filename derived from the structured fields (date, sender, topic, person)."""
        topic = self.topic
        if self.disambiguator:
            topic = f"{topic} {self.disambiguator}"
        base = f"{self.date} - {self.sender} - {topic}"
        if self.person:
            base += f" - {self.person}"
        return base + self.original_path.suffix

    @property
    def filename(self) -> str:
        """The filename actually used at execute time.

        Honors ``filename_override`` when set (e.g. user edited ``proposed_filename``
        in the JSON to a name that doesn't match the synthesis pattern); falls
        back to the synthesized name otherwise.
        """
        if self.filename_override:
            return self.filename_override
        return self.synthesized_filename

    @property
    def translated_filename(self) -> str | None:
        """Filename for the translated companion, or None if no translation."""
        if self.translated_path is None:
            return None
        lang_tag = self.translated_path.stem.rsplit("[", 1)[-1].rstrip("]").strip()
        stem = Path(self.filename).stem
        return f"{stem} [{lang_tag}]{self.original_path.suffix}"

    def to_dict(self) -> dict:
        d = {
            "original_path": str(self.original_path),
            "sender": self.sender,
            "topic": self.topic,
            "person": self.person,
            "date": self.date,
            "country": self.country,
            "folder_topic": self.folder_topic,
            "target_folder": self.target_folder,
            "tags": self.tags,
            "confidence": self.confidence,
            "notes": self.notes,
            "status": self.status,
            "proposed_filename": self.filename,
        }
        if self.translated_path is not None:
            d["translated_path"] = str(self.translated_path)
        if self.filename_override is not None:
            d["filename_override"] = self.filename_override
        if self.file_hash is not None:
            d["file_hash"] = self.file_hash
        if self.disambiguator is not None:
            d["disambiguator"] = self.disambiguator
        if self.non_archive_reason is not None:
            d["non_archive_reason"] = self.non_archive_reason
        if self.document_id is not None:
            d["document_id"] = self.document_id
        if self.supersedes:
            d["supersedes"] = [str(p) for p in self.supersedes]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        translated = d.get("translated_path")
        proposal = cls(
            original_path=Path(d["original_path"]),
            sender=d["sender"],
            topic=d["topic"],
            person=d.get("person", ""),
            date=d["date"],
            country=d["country"],
            folder_topic=d["folder_topic"],
            target_folder=d["target_folder"],
            tags=d["tags"],
            confidence=d["confidence"],
            notes=d["notes"],
            status=d.get("status", "approved"),
            translated_path=Path(translated) if translated else None,
            filename_override=d.get("filename_override"),
            file_hash=d.get("file_hash"),
            disambiguator=d.get("disambiguator"),
            non_archive_reason=d.get("non_archive_reason"),
            document_id=d.get("document_id"),
            supersedes=[Path(p) for p in d.get("supersedes", [])],
        )
        # Back-compat: if the user edited ``proposed_filename`` to a name that
        # doesn't match the synthesis pattern, honor it as an override.
        pf = d.get("proposed_filename")
        if pf and pf != proposal.synthesized_filename and not proposal.filename_override:
            proposal.filename_override = pf
        return proposal


@dataclass
class RefactorMove:
    source: Path
    target: Path

    def to_dict(self) -> dict:
        return {"source": str(self.source), "target": str(self.target)}

    @classmethod
    def from_dict(cls, d: dict) -> "RefactorMove":
        return cls(source=Path(d["source"]), target=Path(d["target"]))


@dataclass
class DuplicateFile:
    inbox_path: Path
    existing_path: Path
    file_hash: str  # Either a SHA-256 of file bytes or of normalized text — see ``match_type``.
    archived_to: Path | None = None
    match_type: str = "bytes"  # "bytes" (byte-for-byte identical) or "text" (same rendered text, different metadata)


# ── Step 1: SCAN ─────────────────────────────────────────────────────────────


def _is_translation_companion(path: Path, config: ArchiveConfig) -> bool:
    """Check if a file looks like a translated companion (e.g. 'doc [EN].pdf')."""
    if not config.translation_enabled:
        return False
    lang_tag = config.translation_target.split("-")[0]
    return path.stem.endswith(f" [{lang_tag}]")


def scan_inbox(ctx: ArchiveContext) -> list[Path]:
    """List files in inbox, sorted by creation date, return oldest MAX_FILES_PER_RUN."""
    files = [
        f
        for f in ctx.inbox.iterdir()
        if f.is_file()
        and not f.name.startswith(".")
        and not _is_translation_companion(f, ctx.config)
    ]
    files.sort(key=lambda f: f.stat().st_birthtime)
    total = len(files)

    if total == 0:
        print("Inbox is empty — nothing to process.")
        return []

    if total > MAX_FILES_PER_RUN:
        files = files[:MAX_FILES_PER_RUN]
        print(
            f"Found {total} files in inbox — processing oldest {MAX_FILES_PER_RUN}"
            f" ({total - MAX_FILES_PER_RUN} will remain)."
        )
    else:
        print(f"Found {total} file(s) in inbox.")

    return files


# ── Step 1.5: DUPLICATE CHECK ───────────────────────────────────────────────


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of a file's contents."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _iter_existing_documents(ctx: ArchiveContext):
    """Yield every filed/archived document path under the archive context."""
    exclude_dirs = (
        {ctx.root / name for name in ctx.tool_dir_names}
        if ctx.root_folder == ctx.root
        else set()
    )
    for search_root in (ctx.root_folder, ctx.archive):
        if not search_root.exists():
            continue
        skip = exclude_dirs if search_root == ctx.root_folder else set()
        for path in search_root.rglob("*"):
            if not (path.is_file() and not path.name.startswith(".")):
                continue
            if skip and any(path.is_relative_to(d) for d in skip):
                continue
            yield path


def find_duplicates(
    inbox_files: list[Path], ctx: ArchiveContext,
) -> tuple[list[DuplicateFile], list[Path]]:
    """Check inbox files against existing filed documents and each other.

    Two passes:
    1. Byte-hash (SHA-256 of raw bytes) — catches exact re-downloads.
    2. Text-hash (SHA-256 of normalized rendered text) — catches re-renders
       with different PDF metadata/timestamps but identical content.

    Returns (duplicates, remaining) where remaining are non-duplicate files
    that should proceed to extraction.
    """
    existing_paths = list(_iter_existing_documents(ctx))
    existing_byte_hashes: dict[str, Path] = {
        compute_file_hash(path): path for path in existing_paths
    }

    duplicates: list[DuplicateFile] = []
    remaining: list[Path] = []
    seen_byte_in_batch: dict[str, Path] = {}

    # Inbox files needing the text-hash secondary check
    text_check_queue: list[tuple[Path, str]] = []  # (inbox_path, byte_hash)

    for inbox_file in inbox_files:
        byte_hash = compute_file_hash(inbox_file)

        if byte_hash in existing_byte_hashes:
            duplicates.append(DuplicateFile(
                inbox_path=inbox_file,
                existing_path=existing_byte_hashes[byte_hash],
                file_hash=byte_hash,
                match_type="bytes",
            ))
            continue
        if byte_hash in seen_byte_in_batch:
            duplicates.append(DuplicateFile(
                inbox_path=inbox_file,
                existing_path=seen_byte_in_batch[byte_hash],
                file_hash=byte_hash,
                match_type="bytes",
            ))
            continue

        seen_byte_in_batch[byte_hash] = inbox_file
        text_check_queue.append((inbox_file, byte_hash))

    if not text_check_queue:
        return duplicates, remaining

    # Lazy text-hash: only computed when at least one inbox file passed the
    # byte-hash check (otherwise we'd pay the extraction cost for nothing).
    inbox_text_hashes: dict[Path, str] = {}
    for inbox_file, _ in text_check_queue:
        text_hash = compute_text_hash(inbox_file)
        if text_hash is not None:
            inbox_text_hashes[inbox_file] = text_hash

    existing_text_hashes: dict[str, Path] = {}
    if inbox_text_hashes:
        for path in existing_paths:
            text_hash = compute_text_hash(path)
            if text_hash is not None:
                # Last write wins on collision — for true content-duplicates
                # within the archive, either path is a valid match target.
                existing_text_hashes[text_hash] = path

    seen_text_in_batch: dict[str, Path] = {}
    for inbox_file, _ in text_check_queue:
        text_hash = inbox_text_hashes.get(inbox_file)
        if text_hash is None:
            remaining.append(inbox_file)
            continue
        if text_hash in existing_text_hashes:
            duplicates.append(DuplicateFile(
                inbox_path=inbox_file,
                existing_path=existing_text_hashes[text_hash],
                file_hash=text_hash,
                match_type="text",
            ))
        elif text_hash in seen_text_in_batch:
            duplicates.append(DuplicateFile(
                inbox_path=inbox_file,
                existing_path=seen_text_in_batch[text_hash],
                file_hash=text_hash,
                match_type="text",
            ))
        else:
            seen_text_in_batch[text_hash] = inbox_file
            remaining.append(inbox_file)

    return duplicates, remaining


# ── Step 2: EXTRACT ──────────────────────────────────────────────────────────


@dataclass
class Extraction:
    path: Path
    text: str | None = None
    error: str | None = None
    translated_path: Path | None = None  # companion translated document
    source_language: str | None = None  # detected language code (e.g. "LV")

    @property
    def ok(self) -> bool:
        return self.text is not None


def extract_all(files: list[Path]) -> list[Extraction]:
    """Extract text from all files. Failures are captured, not raised."""
    results = []
    for f in files:
        print(f"  Extracting: {f.name} ... ", end="", flush=True)
        try:
            text = extract_text(f)
            results.append(Extraction(path=f, text=text))
            print(f"OK ({len(text)} chars)")
        except (ExtractionError, UnsupportedFileType) as e:
            results.append(Extraction(path=f, error=str(e)))
            print(f"FAILED — {e}")
    return results


# ── Step 2.5: TRANSLATE ─────────────────────────────────────────────────────


def translate_all(
    extractions: list[Extraction], ctx: ArchiveContext,
) -> None:
    """Detect language and translate foreign-language documents via DeepL.

    Modifies extractions in-place: sets ``translated_path``,
    ``source_language``, and replaces ``text`` with translated content
    so that downstream classification works from English text.
    """
    if not ctx.config.translation_enabled:
        return

    import deepl as deepl_lib

    auth_key = os.getenv("DEEPL_API_KEY")
    if not auth_key:
        print("  Warning: DEEPL_API_KEY not set — skipping translation.")
        return

    translator = deepl_lib.Translator(auth_key)
    sources_upper = {s.upper() for s in ctx.config.translation_sources}

    for ext in extractions:
        if not ext.ok:
            continue

        try:
            detected = detect_language(ext.text, ctx.config.translation_target, translator)
        except Exception as exc:
            print(f"  Warning: language detection failed for {ext.path.name}: {exc}")
            continue

        if not detected or detected.upper() not in sources_upper:
            continue

        ext.source_language = detected.upper()
        print(
            f"  Translating: {ext.path.name} "
            f"({ext.source_language} → {ctx.config.translation_target}) ... ",
            end="", flush=True,
        )

        try:
            translated_path = translate_document(
                ext.path, ctx.config.translation_target, translator,
            )
            ext.translated_path = translated_path

            # Re-extract text from the translated document for classification
            translated_text = extract_text(translated_path)
            ext.text = translated_text
            print("OK")
        except TranslationError as exc:
            print(f"FAILED — {exc}")
            print(f"    (original text will be used for classification)")
        except (ExtractionError, UnsupportedFileType) as exc:
            # Document translated but text extraction from translation failed;
            # keep the translated file but use original text for classification.
            print(f"OK (translated file saved, but text re-extraction failed: {exc})")
        except Exception as exc:
            # Defense-in-depth: never let a single document abort the whole batch.
            print(f"FAILED — unexpected error: {exc}")
            print(f"    (original text will be used for classification)")


# ── Step 3: PROPOSE ──────────────────────────────────────────────────────────


def get_existing_structure(ctx: ArchiveContext) -> dict[str, list[str]] | list[str]:
    """Return existing folder structure.

    With countries: {country: [folder_names]}
    Without countries: [folder_names]
    """
    # In flat layout, exclude tool directories from folder listing
    exclude_names = ctx.tool_dir_names if ctx.root_folder == ctx.root else frozenset()

    if ctx.config.countries:
        structure = {}
        for country in sorted(ctx.config.countries):
            country_path = ctx.root_folder / country
            if country_path.exists():
                folders = [
                    f.name for f in country_path.iterdir()
                    if f.is_dir() and f.name != "Unsorted"
                ]
                structure[country] = sorted(folders)
            else:
                structure[country] = []
        return structure

    # No countries — flat folder list under root
    if ctx.root_folder.exists():
        return sorted(
            f.name for f in ctx.root_folder.iterdir()
            if f.is_dir()
            and f.name != "Unsorted"
            and f.name not in exclude_names
        )
    return []


def _build_prompt(
    text: str, existing_structure: dict, config: ArchiveConfig,
) -> str:
    """Build the full classification prompt for the Claude API call."""
    tags_block = "\n".join(
        f"- {name}: {desc}" for name, desc in config.tags.items()
    )
    structure_json = json.dumps(existing_structure, indent=2)

    # Countries section — only if configured
    if config.countries:
        countries_csv = ", ".join(config.countries)
        country_choices = "|".join(config.countries + ["none"])
        countries_section = (
            f"COUNTRIES: {countries_csv}\n"
            'Use "none" if the document is country-independent.'
        )
    else:
        country_choices = "none"
        countries_section = (
            "COUNTRIES: This archive is not organized by country. "
            'Always use "none".'
        )

    if config.use_person:
        known_people = ", ".join(f'"{name}"' for name in config.people.keys())
        person_json = '"person": "...", '
        person_field = (
            f"- Person: who the document primarily concerns (first name only). "
            f"Known people: {known_people}. "
            f'Use "Unknown" if no specific person applies or can be identified.\n'
        )
        naming_convention = "[Date] - [Sender] - [Topic] - [Person].[ext]"
    else:
        person_json = ""
        person_field = ""
        naming_convention = "[Date] - [Sender] - [Topic].[ext]"

    non_archive_field = ""
    non_archive_section = ""
    if config.non_archive_dir:
        non_archive_field = (
            '"non_archive_reason": "short reason if this document should NOT be filed (or null)", '
        )
        non_archive_section = (
            f"\nNON-ARCHIVE — set ``non_archive_reason`` (otherwise leave null) when the document is "
            f"clearly not a partner/business record worth filing: blank scans, working drafts of letters, "
            f"templates, screenshots, internal notes, or anything that ended up in the inbox by mistake. "
            f"When set, the file is moved to ``{config.non_archive_dir}/`` instead of the archive. "
            f"All other classification fields (sender/topic/etc.) may be set best-effort or left empty — "
            f"they are not used when non_archive_reason is present.\n"
        )

    json_example = (
        f'{{"date": "...", "sender": "...", "topic": "...", {person_json}'
        f'"country": "{country_choices}", '
        f'"folder_topic": "short folder name for this document\'s topic", '
        f'"tags": [], "confidence": "High|Medium|Low", '
        f'"disambiguator": "short signal that distinguishes this doc from same-day same-topic siblings, or null", '
        f'"document_id": "stable identifier visible on the document (offer #, contract #, invoice #, reference #), or null", '
        f'{non_archive_field}'
        f'"notes": "brief explanation of your reasoning"}}'
    )

    return f"""\
You are a document filing assistant. {config.prompt_context}

NAMING CONVENTION: {naming_convention}

Field definitions (in filename order):
- Date: the document's issue date in ISO 8601 (YYYY-MM-DD, YYYY-MM, or YYYY). \
Placed first for chronological sorting. Use "Undated" if the date cannot be determined. Never guess.
- Sender: who issued the document. Use the natural name (e.g. "Finanzamt Heidelberg", \
"Commerzbank"). Abbreviate only when unambiguous.
- Topic: what the document is about, always in English. \
For common document types, use the bare type word ALONE — do NOT include descriptive details \
about the subject matter. The Sender field already provides context.
  Examples of correct vs. wrong detail level:
  - GOOD `Invoice`           BAD `Interior design invoice — window treatments`
  - GOOD `Order`             BAD `Eyeglasses order and warranty — Fielmann BD481 CL`
  - GOOD `Purchase contract` BAD `Purchase contract — bed frame and delivery`
  - GOOD `Statement`         BAD `Checking Account Statement — December 2025`
  - GOOD `Policy`            BAD `Liability Insurance Policy — coverage details`
  Only add a qualifier when it truly distinguishes the document from another filing that would \
otherwise collide (e.g. `Tax Assessment 2023` vs. `Tax Assessment 2024` from the same sender, same year). \
Embed a period or year ONLY when the covered period differs from the issue date \
(e.g. a tax assessment for 2023 issued in 2024, or a bank statement for Dec 2025 issued Jan 2026). \
When the topic period matches the issue date month, OMIT it — the Date field carries the temporal context. \
Keep it concise.
{person_field}
{countries_section}

EXISTING FOLDERS (reuse if appropriate):
{structure_json}

TAGS — apply only from this list, only when clearly relevant:
{tags_block}

{non_archive_section}DOCUMENT_ID — set when the document carries a stable, vendor-issued identifier:
- Offer numbers, contract numbers, invoice numbers, reference numbers, ticket IDs, etc.
- Use the value EXACTLY as printed (e.g. "362283", "S5DKLG1Q-0014", "RE001454", "#2104541").
- This enables supersedence detection: when a newer version of the same document arrives, the older one is moved to the archive's superseded folder.
- Leave null when no such identifier is visible.

DISAMBIGUATOR — set when the topic alone won't be unique within the same (date, sender):
- Multiple charges from the same vendor on the same day → use a brief amount signal: `"USD 11.35"`, `"EUR 488.13"`, `"CHF 65.95"`.
- Multiple statements/reports for different periods issued the same day → use the period: `"Q1 2026"`, `"2025-12"`.
- Multiple documents with stable identifiers (invoice numbers, contract IDs) → use the short trailing digits: `"0014"`, `"#2104541"`.
- For invoice + receipt pairs covering the SAME charge (same invoice number from the same sender), use the SAME disambiguator on both — they sort together that way.
- Leave null when topic+date+sender is already unique. Do NOT pad the filename with redundant words.

CONFIDENCE:
- High: all four fields clearly identified from document content
- Medium: one field inferred or uncertain (explain in notes)
- Low: significant uncertainty (explain in notes)

Respond with a single JSON object, no markdown fencing:
{json_example}

DOCUMENT TEXT:
{text}"""


def _call_claude(
    text: str, existing_structure: dict, config: ArchiveConfig,
) -> dict:
    """Send document text to Claude and parse the structured response."""
    client = anthropic.Anthropic()

    prompt = _build_prompt(text, existing_structure, config)

    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    return json.loads(raw)


def _build_proposal(
    path: Path, data: dict, config: ArchiveConfig,
) -> Proposal:
    """Build a Proposal from Claude's response."""
    country = data.get("country", "")
    if country == "none":
        country = ""

    folder_topic = data.get("folder_topic", "Unsorted")
    tags = [t for t in data.get("tags", []) if t in config.controlled_tags]

    # Charset (ADR-0009): transliterate every field that lands on the filesystem
    # before assembling target_folder/filename, so the LLM never has to know
    # about the ASCII rule.
    cs = config.filename_charset
    sender = to_ascii(data["sender"], cs)
    topic = to_ascii(data["topic"], cs)
    person = to_ascii(data.get("person", ""), cs)
    country = to_ascii(country, cs)
    folder_topic = path_to_ascii(folder_topic, cs)

    prefix = config.root_folder_prefix
    if country:
        target_folder = f"{prefix}{country}/{folder_topic}"
    else:
        target_folder = f"{prefix}{folder_topic}"

    if config.date_subfolders:
        target_folder = f"{target_folder}/{date_to_subfolder(data.get('date', 'Undated'))}"

    try:
        file_hash = compute_file_hash(path)
    except OSError:
        # Best-effort: hash is used only for recovery; never block propose on it.
        file_hash = None

    disambiguator = data.get("disambiguator")
    if isinstance(disambiguator, str):
        disambiguator = disambiguator.strip() or None
        if disambiguator:
            disambiguator = to_ascii(disambiguator, cs)

    non_archive_reason = data.get("non_archive_reason")
    if isinstance(non_archive_reason, str):
        non_archive_reason = non_archive_reason.strip() or None

    document_id = data.get("document_id")
    if isinstance(document_id, str):
        document_id = document_id.strip() or None

    # Non-archive routing overrides target_folder and preserves the original
    # filename — the file is being disposed of, not filed for retrieval.
    filename_override = None
    if non_archive_reason and config.non_archive_dir:
        target_folder = config.non_archive_dir
        filename_override = path.name

    return Proposal(
        original_path=path,
        sender=sender,
        topic=topic,
        person=person,
        date=data["date"],
        country=country,
        folder_topic=folder_topic,
        target_folder=target_folder,
        tags=tags,
        confidence=data["confidence"],
        notes=data.get("notes", ""),
        file_hash=file_hash,
        disambiguator=disambiguator,
        non_archive_reason=non_archive_reason,
        document_id=document_id,
        filename_override=filename_override,
    )


_FILENAME_DATE_RE = re.compile(r"^(\d{4}(?:-\d{1,2}(?:-\d{1,2})?)?)\b")
_DOC_ID_MIN_LENGTH = 4  # below this, false-positive substring matches are too likely


def _parse_filename_date(name: str) -> str | None:
    """Extract the leading ISO date prefix (YYYY[-MM[-DD]]) from a filename, or None."""
    m = _FILENAME_DATE_RE.match(name)
    return m.group(1) if m else None


def detect_supersedence(
    proposals: list[Proposal], ctx: ArchiveContext,
) -> None:
    """Mark older archived documents as superseded by newer same-id proposals.

    For each proposal carrying a ``document_id``:
    1. Scan the archive for filenames containing that ID as a substring.
    2. For each match, parse the leading date prefix from its filename.
    3. If the archived file's date is strictly older than the new proposal's
       date, append it to ``proposal.supersedes``.

    Conservative: short IDs (under ``_DOC_ID_MIN_LENGTH`` chars) are ignored,
    and matches where the date can't be parsed or isn't older are ignored.
    The execute step moves each ``supersedes`` entry into ``_archive/`` with
    a "Superseded" suffix on the filename stem.
    """
    candidates_by_id: dict[str, list[Path]] = {}
    paths = list(_iter_existing_documents(ctx))
    for p in proposals:
        if not p.document_id or p.non_archive_reason:
            continue
        doc_id = p.document_id
        if len(doc_id) < _DOC_ID_MIN_LENGTH:
            continue

        matches = candidates_by_id.get(doc_id)
        if matches is None:
            matches = [path for path in paths if doc_id in path.name]
            candidates_by_id[doc_id] = matches

        new_date = p.date or ""
        for archived in matches:
            old_date = _parse_filename_date(archived.name)
            if old_date is None or new_date == "" or old_date >= new_date:
                continue
            if archived not in p.supersedes:
                p.supersedes.append(archived)


def _archive_superseded(path: Path, ctx: ArchiveContext) -> Path:
    """Move ``path`` into ``_archive/`` mirroring its current relative location.

    Adds a " Superseded" suffix to the stem. Returns the new path.
    """
    try:
        rel = path.relative_to(ctx.root_folder)
    except ValueError:
        # Already under archive (or outside the filing root) — mirror from ctx.root.
        rel = path.relative_to(ctx.root) if path.is_relative_to(ctx.root) else Path(path.name)

    target_dir = ctx.archive / rel.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    new_name = f"{path.stem} Superseded{path.suffix}"
    target = target_dir / new_name

    # Avoid collision if the same supersedence runs twice or there's already
    # a Superseded file with the same name.
    if target.exists():
        n = 2
        while True:
            candidate = target_dir / f"{path.stem} Superseded ({n}){path.suffix}"
            if not candidate.exists():
                target = candidate
                break
            n += 1

    path.rename(target)
    return target


def apply_three_document_rule(
    proposals: list[Proposal], ctx: ArchiveContext,
) -> None:
    """Demote folder assignments to Unsorted when the three-document rule is not met.

    A topic folder is used only if:
    - The folder already exists on disk, OR
    - Three or more documents (in this batch) share the same (country, folder_topic).

    Otherwise, file under Unsorted/.
    """
    topic_counts: Counter[tuple[str, str]] = Counter()
    for p in proposals:
        if p.folder_topic != "Unsorted":
            topic_counts[(p.country, p.folder_topic)] += 1

    prefix = ctx.config.root_folder_prefix
    for p in proposals:
        if p.non_archive_reason:
            continue
        if p.folder_topic == "Unsorted":
            continue

        # Skip if target_folder was overridden to a non-standard location
        # (e.g. by business_routing to "ToDo/Stratech") — the three-document
        # rule only governs the default country/folder_topic layout.
        # Note: date_subfolders mode appends "/YYYY-MM" to the expected base,
        # so accept that as a normal (non-override) shape.
        if p.country:
            expected = f"{prefix}{p.country}/{p.folder_topic}"
        else:
            expected = f"{prefix}{p.folder_topic}"
        if p.target_folder != expected and not p.target_folder.startswith(
            expected + "/"
        ):
            continue

        if p.country:
            folder_path = ctx.root_folder / p.country / p.folder_topic
        else:
            folder_path = ctx.root_folder / p.folder_topic
        if folder_path.exists():
            continue  # folder exists — keep assignment

        key = (p.country, p.folder_topic)
        if topic_counts[key] < 3:
            p.folder_topic = "Unsorted"
            if p.country:
                unsorted = f"{prefix}{p.country}/Unsorted"
            else:
                unsorted = f"{prefix}Unsorted"
            if ctx.config.date_subfolders:
                unsorted = f"{unsorted}/{date_to_subfolder(p.date)}"
            p.target_folder = unsorted


def _match_business_routing(text: str, config: ArchiveConfig):
    """Return the first BusinessRoutingRule whose match_strings appear in ``text``.

    Matching is case-insensitive and substring-based.
    """
    lowered = text.lower()
    for rule in config.business_routing:
        if any(s.lower() in lowered for s in rule.match_strings):
            return rule
    return None


def apply_business_routing(proposal: Proposal, text: str, config: ArchiveConfig) -> str | None:
    """Override proposal routing fields when a business-routing rule matches ``text``.

    Returns the name of the rule applied, or None.
    """
    if proposal.non_archive_reason:
        return None
    rule = _match_business_routing(text, config)
    if rule is None:
        return None
    cs = config.filename_charset
    proposal.target_folder = path_to_ascii(rule.target_folder, cs)
    proposal.folder_topic = proposal.target_folder.rsplit("/", 1)[-1]
    for tag in rule.append_tags:
        if tag not in proposal.tags:
            proposal.tags.append(tag)
    if rule.override_person:
        proposal.person = to_ascii(rule.override_person, cs)
    if rule.override_sender:
        proposal.sender = to_ascii(rule.override_sender, cs)
    return rule.name


def propose_all(
    extractions: list[Extraction], ctx: ArchiveContext,
) -> list[Proposal]:
    """Generate proposals for all successfully extracted files."""
    existing = get_existing_structure(ctx)
    plugin = ctx.plugin
    proposals = []

    for ext in extractions:
        if not ext.ok:
            continue
        print(f"  Classifying: {ext.path.name} ... ", end="", flush=True)
        try:
            data = _call_claude(ext.text, existing, ctx.config)
            proposal = _build_proposal(ext.path, data, ctx.config)
            proposal.translated_path = ext.translated_path
            routed = apply_business_routing(proposal, ext.text, ctx.config)
            if plugin.post_propose is not None:
                try:
                    proposal = plugin.post_propose(proposal, ext.text)
                except Exception as e:
                    print(f"\n  Warning: post_propose hook raised {e!r} — keeping unmodified proposal", file=sys.stderr)
            proposals.append(proposal)
            suffix = f" [routed: {routed}]" if routed else ""
            print(f"OK ({proposal.confidence}){suffix}")
        except Exception as e:
            print(f"FAILED — {e}")

    apply_three_document_rule(proposals, ctx)
    detect_supersedence(proposals, ctx)
    if plugin.post_batch is not None:
        try:
            proposals = plugin.post_batch(proposals)
        except Exception as e:
            print(f"  Warning: post_batch hook raised {e!r} — keeping batch unchanged", file=sys.stderr)
    return proposals


# ── Proposals I/O ────────────────────────────────────────────────────────────


def save_proposals(
    proposals: list[Proposal],
    flagged: list[Extraction],
    duplicates: list[DuplicateFile],
    path: Path | None = None,
    *,
    default_path: Path | None = None,
) -> Path:
    """Save proposals, flagged files, and duplicates to JSON."""
    target = path or default_path or Path("proposals.json")
    data = {
        "proposals": [p.to_dict() for p in proposals],
        "flagged": [
            {"path": str(e.path), "error": e.error} for e in flagged
        ],
        "duplicates": [
            {
                "inbox_path": str(d.inbox_path),
                "existing_path": str(d.existing_path),
                "hash": d.file_hash,
                "match_type": d.match_type,
            }
            for d in duplicates
        ],
    }
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return target


def load_proposals(
    path: Path,
) -> tuple[list[Proposal], list[Extraction], list[DuplicateFile]]:
    """Load proposals from a JSON file."""
    data = json.loads(path.read_text())
    proposals = [Proposal.from_dict(d) for d in data["proposals"]]
    flagged = [
        Extraction(path=Path(f["path"]), error=f["error"])
        for f in data.get("flagged", [])
    ]
    duplicates = [
        DuplicateFile(
            inbox_path=Path(d["inbox_path"]),
            existing_path=Path(d["existing_path"]),
            file_hash=d["hash"],
            match_type=d.get("match_type", "bytes"),
        )
        for d in data.get("duplicates", [])
    ]
    return proposals, flagged, duplicates


# ── Display ──────────────────────────────────────────────────────────────────


def display_proposals(
    proposals: list[Proposal],
    extractions: list[Extraction],
    duplicates: list[DuplicateFile],
) -> None:
    """Print a dry-run diff of all proposals, flagged files, and duplicates."""
    if duplicates:
        print("\n── Duplicates (will be archived) ────────────────────────")
        for d in duplicates:
            label = "byte-identical" if d.match_type == "bytes" else "text-identical (metadata differs)"
            print(f"  {d.inbox_path.name}  [{label}]")
            print(f"    Duplicate of: {d.existing_path}")
            print(f"    Will move to: _archive/inbox/{d.inbox_path.name}")

    flagged = [e for e in extractions if not e.ok]
    if flagged:
        print("\n── Flagged (not processable) ────────────────────────────")
        for e in flagged:
            print(f"  {e.path.name}")
            print(f"    Reason: {e.error}")

    if not proposals:
        print("\nNo proposals to display.")
        return

    print("\n── Proposals ───────────────────────────────────────────────")
    for i, p in enumerate(proposals, 1):
        print(f"\n  [{i}] {p.original_path.name}")
        if p.non_archive_reason:
            print(f"      → {p.target_folder}/{p.filename}  [non-archive disposal]")
            print(f"      Reason:     {p.non_archive_reason}")
            continue
        print(f"      → {p.filename}")
        if p.translated_path:
            print(f"      + {p.translated_filename}")
        print(f"      Folder:     {p.target_folder}/")
        print(f"      Tags:       {', '.join(p.tags) if p.tags else '(none)'}")
        print(f"      Confidence: {p.confidence}")
        if p.supersedes:
            print(f"      Supersedes: {len(p.supersedes)} file(s) (will move to _archive/)")
            for old in p.supersedes:
                print(f"                  - {old}")
        print(f"      Notes:      {p.notes}")


# ── Step 4: CONFIRM ──────────────────────────────────────────────────────────


def confirm_all(proposals: list[Proposal]) -> list[Proposal]:
    """Interactive confirmation: approve, edit, or skip each proposal."""
    print("\n── Confirm ─────────────────────────────────────────────────")
    print("  For each file: [a]pprove / [e]dit / [s]kip\n")

    for i, p in enumerate(proposals, 1):
        print(f"  [{i}] {p.original_path.name}")
        print(f"      → {p.filename}")
        print(f"      Folder: {p.target_folder}/")
        print(f"      Tags:   {', '.join(p.tags) if p.tags else '(none)'}")

        while True:
            choice = input("      Action [a/e/s]: ").strip().lower()
            if choice in ("a", "approve"):
                p.status = "approved"
                break
            elif choice in ("s", "skip"):
                p.status = "skipped"
                break
            elif choice in ("e", "edit"):
                _edit_proposal(p)
                p.status = "approved"
                break
            else:
                print("      Invalid choice — enter a, e, or s.")

    return proposals


def _edit_proposal(p: Proposal) -> None:
    """Let user edit individual fields. Press Enter to keep current value."""
    print("      Edit fields (Enter to keep current value):")

    val = input(f"        Sender [{p.sender}]: ").strip()
    if val:
        p.sender = val

    val = input(f"        Topic [{p.topic}]: ").strip()
    if val:
        p.topic = val

    if p.person:
        val = input(f"        Person [{p.person}]: ").strip()
        if val:
            p.person = val

    val = input(f"        Date [{p.date}]: ").strip()
    if val:
        p.date = val

    val = input(f"        Folder [{p.target_folder}]: ").strip()
    if val:
        p.target_folder = val

    val = input(f"        Tags [{', '.join(p.tags)}]: ").strip()
    if val:
        p.tags = [t.strip() for t in val.split(",") if t.strip()]

    print(f"      → {p.filename}")
    print(f"      Folder: {p.target_folder}/")


# ── Step 5: EXECUTE ──────────────────────────────────────────────────────────


def archive_duplicates(
    duplicates: list[DuplicateFile], ctx: ArchiveContext,
) -> None:
    """Move duplicate inbox files to _archive/inbox/."""
    if not duplicates:
        return

    archive_inbox = ctx.archive / "inbox"
    archive_inbox.mkdir(parents=True, exist_ok=True)

    print("\n── Archive duplicates ──────────────────────────────────────")
    for d in duplicates:
        if not d.inbox_path.exists():
            print(f"  MISSING: {d.inbox_path.name} — no longer in inbox, skipping")
            continue
        target = archive_inbox / d.inbox_path.name
        d.inbox_path.rename(target)
        d.archived_to = target
        print(f"  Archived: {d.inbox_path.name} → _archive/inbox/{d.inbox_path.name}")


def apply_tags(path: Path, tags: list[str]) -> None:
    """Write macOS Finder tags via xattr."""
    if not tags:
        return
    tag_values = [f"{tag}\n0" for tag in tags]  # \n0 = no colour
    plist_data = plistlib.dumps(tag_values, fmt=plistlib.FMT_BINARY)
    xattr_lib.setxattr(str(path), "com.apple.metadata:_kMDItemUserTags", plist_data)


def _resolve_collision(target_dir: Path, proposal: Proposal) -> Path:
    """Return a non-colliding target path for ``proposal`` in ``target_dir``.

    If the default filename already exists, append ``(2)``, ``(3)``, … until a
    free slot is found. Mutation is intentional: downstream logging and
    persistence reflect the actual filename chosen.

    - For synthesized filenames, the counter is appended to the ``topic`` field.
    - For overridden filenames, the counter is appended to the override's stem.
    """
    target = target_dir / proposal.filename
    if not target.exists():
        return target

    if proposal.filename_override:
        stem = Path(proposal.filename_override).stem
        suffix = Path(proposal.filename_override).suffix
        base_stem = stem
        n = 2
        while True:
            proposal.filename_override = f"{base_stem} ({n}){suffix}"
            target = target_dir / proposal.filename
            if not target.exists():
                return target
            n += 1

    base_topic = proposal.topic
    n = 2
    while True:
        proposal.topic = f"{base_topic} ({n})"
        target = target_dir / proposal.filename
        if not target.exists():
            return target
        n += 1


def _recover_by_hash(proposal: Proposal, ctx: ArchiveContext) -> Path | None:
    """Find an inbox file whose SHA-256 matches ``proposal.file_hash``.

    Used when ``original_path`` no longer exists — typically because a user
    or agent edited proposals.json and the source filename was rewritten as
    a side-effect (e.g. global replace-all on a string that also appeared
    in the path). Returns the matching inbox path, or None if not found.
    """
    if not proposal.file_hash:
        return None
    if not ctx.inbox.exists():
        return None
    for candidate in ctx.inbox.iterdir():
        if not candidate.is_file():
            continue
        if candidate.name.startswith("."):
            continue
        try:
            if compute_file_hash(candidate) == proposal.file_hash:
                return candidate
        except OSError:
            continue
    return None


def execute_all(
    proposals: list[Proposal],
    ctx: ArchiveContext,
    *,
    on_executed: "callable | None" = None,
) -> list[Proposal]:
    """Move and tag approved files.

    Mandatory tags from config are merged into each file's tags on execution.
    """
    executed = []
    for p in proposals:
        if p.status != "approved":
            continue

        # Defensive sanitization: strip path-unsafe characters right before
        # rename so a bad proposal that slipped past the validator can't abort
        # the whole batch. The validator should have caught this already —
        # this is a belt-and-braces guard.
        _sanitize_proposal_fields(p, ctx.config)

        target_dir = ctx.root / p.target_folder
        target_dir.mkdir(parents=True, exist_ok=True)
        default_target = target_dir / p.filename

        # Merge mandatory tags (deduplicated, order-preserving)
        all_tags = list(dict.fromkeys(p.tags + ctx.config.mandatory_tags))

        # Resume: file already moved in a previous partial run
        if not p.original_path.exists():
            if default_target.exists():
                apply_tags(default_target, all_tags)
                p.status = "executed"
                executed.append(p)
                print(
                    f"  RESUMED: {p.original_path.name} "
                    f"→ already at {p.target_folder}/{p.filename}"
                )
                if on_executed:
                    on_executed(p)
                continue

            # Recovery: original_path was corrupted (e.g. user-edited JSON
            # accidentally rewrote the source filename). If a content hash
            # was stored at propose-time, scan the inbox for a matching file.
            recovered = _recover_by_hash(p, ctx)
            if recovered is not None:
                print(
                    f"  RECOVERED: {p.original_path.name} → {recovered.name} "
                    f"(matched by content hash)"
                )
                p.original_path = recovered
                # fall through to the normal move path below
            else:
                print(
                    f"  MISSING: {p.original_path.name} "
                    f"— not in inbox or target, skipping"
                )
                p.status = "skipped"
                continue

        try:
            # Move any superseded files into _archive first, so a same-name new
            # file can take their slot without collision.
            for old in list(p.supersedes):
                if not old.exists():
                    continue
                archived_to = _archive_superseded(old, ctx)
                print(f"  Superseded: {old.name} → {archived_to.relative_to(ctx.root)}")

            # Resolve name collisions by appending (2), (3), … to the topic.
            target = _resolve_collision(target_dir, p)

            # Rename + move in one operation
            p.original_path.rename(target)
            # Non-archive disposals don't get archive tags.
            if not p.non_archive_reason:
                apply_tags(target, all_tags)
            p.status = "executed"
            executed.append(p)
            label = "Disposed" if p.non_archive_reason else "Moved"
            print(f"  {label}: {p.original_path.name} → {p.target_folder}/{p.filename}")

            # Move translated companion file alongside the original
            if p.translated_path and p.translated_path.exists():
                translated_target = target_dir / p.translated_filename
                p.translated_path.rename(translated_target)
                apply_tags(translated_target, all_tags)
                print(f"  Moved: {p.translated_path.name} → {p.target_folder}/{p.translated_filename}")

            if on_executed:
                on_executed(p)
        except (OSError, ValueError) as e:
            p.status = "error"
            p.notes = f"{p.notes} [execute error: {e}]".strip()
            print(
                f"  ERROR: {p.original_path.name} — {e}. "
                f"Continuing with remaining proposals."
            )
            if on_executed:
                on_executed(p)

    return executed


def _sanitize_proposal_fields(p: Proposal, config: ArchiveConfig) -> None:
    """In-place strip of path-unsafe chars from filename fields.

    Mirrors validator._check_path_unsafe_chars but without producing Issues —
    a last-line-of-defence before the rename call so a raw '/' in a sender
    can't be interpreted as a directory separator by pathlib. Also enforces
    the ASCII charset rule (ADR-0009).
    """
    p.sender = sanitize_field(p.sender)
    p.topic = sanitize_field(p.topic)
    if p.person:
        p.person = sanitize_field(p.person)
    if p.filename_override:
        p.filename_override = sanitize_field(p.filename_override)

    cs = config.filename_charset
    p.sender = to_ascii(p.sender, cs)
    p.topic = to_ascii(p.topic, cs)
    if p.person:
        p.person = to_ascii(p.person, cs)
    if p.filename_override:
        p.filename_override = to_ascii(p.filename_override, cs)
    if p.disambiguator:
        p.disambiguator = to_ascii(p.disambiguator, cs)
    if p.target_folder:
        p.target_folder = path_to_ascii(p.target_folder, cs)
    if p.folder_topic:
        p.folder_topic = path_to_ascii(p.folder_topic, cs)


# ── Step 6: REPORT ───────────────────────────────────────────────────────────


def next_batch_id(ctx: ArchiveContext) -> str:
    """Determine the next batch ID for today (YYYY-MM-DD-N)."""
    today = date.today().isoformat()
    n = 1

    if ctx.intake_log.exists():
        content = ctx.intake_log.read_text()
        prefix = f"## {today}-"
        for line in content.splitlines():
            if line.startswith(prefix):
                try:
                    existing_n = int(line[len(prefix):])
                    n = max(n, existing_n + 1)
                except ValueError:
                    pass

    return f"{today}-{n}"


def write_intake_log(
    batch_id: str,
    executed: list[Proposal],
    flagged: list[Extraction],
    duplicates: list[DuplicateFile],
    ctx: ArchiveContext,
    base_content: str | None = None,
) -> None:
    """Prepend intake log entries (newest at top)."""
    lines = [f"## {batch_id}\n"]

    for p in executed:
        if p.non_archive_reason:
            lines.append(f"### NON-ARCHIVE: {p.original_path.name}")
            lines.append(f"- Original:    inbox/{p.original_path.name}")
            lines.append(f"- Disposed to: {p.target_folder}/{p.filename}")
            lines.append(f"- Reason:      {p.non_archive_reason}")
            lines.append("")
            continue
        lines.append(f"### {p.filename}")
        lines.append(f"- Original:    inbox/{p.original_path.name}")
        lines.append(f"- Moved to:    {p.target_folder}/")
        lines.append(f"- Tags:        {', '.join(p.tags) if p.tags else '(none)'}")
        lines.append(f"- Confidence:  {p.confidence}")
        lines.append(f"- Notes:       {p.notes}")
        if p.translated_filename:
            lines.append(f"- Translation: {p.translated_filename}")
        lines.append("")

    for d in duplicates:
        lines.append(f"### DUPLICATE: {d.inbox_path.name}")
        lines.append(f"- Original:    inbox/{d.inbox_path.name}")
        lines.append(f"- Match type:  {d.match_type}")
        lines.append(f"- Duplicate of: {d.existing_path}")
        if d.archived_to:
            lines.append(f"- Archived to: {d.archived_to}")
        lines.append("")

    for e in flagged:
        lines.append(f"### FLAGGED: {e.path.name}")
        lines.append(f"- Original:    inbox/{e.path.name}")
        lines.append(f"- Reason:      {e.error}")
        lines.append("")

    new_entry = "\n".join(lines) + "\n"

    if base_content is not None:
        ctx.intake_log.write_text(new_entry + base_content)
    elif ctx.intake_log.exists():
        ctx.intake_log.write_text(new_entry + ctx.intake_log.read_text())
    else:
        ctx.intake_log.write_text(new_entry)


def report(
    proposals: list[Proposal],
    flagged: list[Extraction],
    executed: list[Proposal],
    duplicates: list[DuplicateFile],
    ctx: ArchiveContext,
) -> None:
    """Print final summary."""
    skipped = [p for p in proposals if p.status == "skipped"]

    print("\n── Summary ─────────────────────────────────────────────────")
    print(f"  Processed:  {len(executed)}")
    print(f"  Skipped:    {len(skipped)}")
    print(f"  Duplicates: {len(duplicates)}")
    print(f"  Flagged:    {len(flagged)}")

    remaining = [
        f for f in ctx.inbox.iterdir()
        if f.is_file() and not f.name.startswith(".")
    ]
    if remaining:
        print(f"  Remaining in inbox: {len(remaining)}")


# ── Refactor ────────────────────────────────────────────────────────────────


def refactor_propose(
    match_pattern: str, target_folder: str, ctx: ArchiveContext,
) -> list[RefactorMove]:
    """Find files matching glob pattern and plan moves to target folder."""
    target_dir = ctx.root / target_folder

    matches = sorted(ctx.root.glob(match_pattern))
    matches = [m for m in matches if m.is_file() and not m.name.startswith(".")]

    if not matches:
        print(f"No files match pattern: {match_pattern}")
        return []

    moves = []
    for source in matches:
        target = target_dir / source.name
        if source == target:
            continue
        moves.append(RefactorMove(source=source, target=target))

    if not moves:
        print("All matching files are already in the target folder.")

    return moves


def display_refactor(moves: list[RefactorMove], ctx: ArchiveContext) -> None:
    """Print a dry-run of planned refactor moves."""
    print(f"\n── Refactor plan ({len(moves)} file(s)) ─────────────────────────")
    for m in moves:
        rel_source = m.source.relative_to(ctx.root)
        rel_target = m.target.relative_to(ctx.root)
        print(f"  {rel_source}")
        print(f"    → {rel_target}")


def save_refactor(moves: list[RefactorMove], ctx: ArchiveContext) -> Path:
    """Save refactor plan to JSON."""
    data = {"moves": [m.to_dict() for m in moves]}
    ctx.refactor_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return ctx.refactor_file


def load_refactor(path: Path) -> list[RefactorMove]:
    """Load refactor plan from JSON."""
    data = json.loads(path.read_text())
    return [RefactorMove.from_dict(d) for d in data["moves"]]


def execute_refactor(
    moves: list[RefactorMove], ctx: ArchiveContext,
) -> list[RefactorMove]:
    """Move files according to the refactor plan."""
    executed = []
    empty_dirs: set[Path] = set()

    for m in moves:
        if not m.source.exists():
            print(f"  MISSING: {m.source.name} — skipping")
            continue
        if m.target.exists():
            print(f"  CONFLICT: {m.target} already exists — skipping")
            continue

        m.target.parent.mkdir(parents=True, exist_ok=True)
        empty_dirs.add(m.source.parent)
        m.source.rename(m.target)
        executed.append(m)

        rel_source = m.source.relative_to(ctx.root)
        rel_target = m.target.relative_to(ctx.root)
        print(f"  Moved: {rel_source} → {rel_target}")

    # Clean up empty source directories
    stop_dirs = {ctx.root, ctx.root_folder}
    for d in sorted(empty_dirs, reverse=True):
        current = d
        while current not in stop_dirs:
            try:
                if current.exists() and not any(current.iterdir()):
                    rel = current.relative_to(ctx.root)
                    current.rmdir()
                    print(f"  Removed empty folder: {rel}/")
                    current = current.parent
                else:
                    break
            except OSError:
                break

    return executed


def write_refactor_log(
    batch_id: str, moves: list[RefactorMove], ctx: ArchiveContext,
) -> None:
    """Append refactor entries to intake log."""
    lines = [f"## {batch_id}\n"]
    lines.append("### REFACTOR\n")
    for m in moves:
        rel_source = m.source.relative_to(ctx.root)
        rel_target = m.target.relative_to(ctx.root)
        lines.append(f"- {rel_source} → {rel_target}")
    lines.append("")

    new_entry = "\n".join(lines) + "\n"
    if ctx.intake_log.exists():
        existing = ctx.intake_log.read_text()
        ctx.intake_log.write_text(new_entry + existing)
    else:
        ctx.intake_log.write_text(new_entry)


# ── Charset migration (ADR-0009) ─────────────────────────────────────────────


def _charset_migration_plan(ctx: ArchiveContext) -> list[tuple[Path, Path]]:
    """Build a list of (old, new) paths whose ASCII form differs from current.

    Walks the filing root and the ToDo folder, skipping inbox / _archive and
    hidden entries. Returns a deepest-first list so renames execute children
    before parents (renaming a folder first would invalidate paths to the
    files still inside it).
    """
    cs = ctx.config.filename_charset
    skip_names = {ctx.config.inbox_dir, ctx.config.archive_dir}

    roots: list[Path] = []
    if ctx.root_folder.exists():
        roots.append(ctx.root_folder)
    if ctx.todo.exists() and ctx.todo not in roots:
        roots.append(ctx.todo)

    pairs: list[tuple[Path, Path]] = []
    for root in roots:
        for path in root.rglob("*"):
            if path.name.startswith("."):
                continue
            if any(part in skip_names for part in path.relative_to(ctx.root).parts):
                continue
            new_name = to_ascii(path.name, cs)
            if new_name == path.name:
                continue
            pairs.append((path, path.with_name(new_name)))

    # Deepest-first: rename files before their parent folders.
    pairs.sort(key=lambda pair: len(pair[0].parts), reverse=True)
    return pairs


def migrate_charset(ctx: ArchiveContext, *, apply: bool) -> None:
    """Bring the existing archive into ASCII compliance.

    Without ``--apply`` prints a dry-run table. With ``--apply`` performs the
    renames and appends a refactor entry to the intake log.
    """
    print("── Migrate charset (ADR-0009) ──────────────────────────────")
    plan = _charset_migration_plan(ctx)

    if not plan:
        print("  All paths already ASCII — nothing to do.")
        return

    print(f"  {len(plan)} path(s) need migration:\n")
    for old, new in plan:
        rel_old = old.relative_to(ctx.root)
        rel_new = new.relative_to(ctx.root)
        print(f"    {rel_old}")
        print(f"      → {rel_new}")
    print()

    if not apply:
        print("  (dry run — pass --apply to perform the renames)")
        return

    moves: list[RefactorMove] = []
    for old, new in plan:
        # A sibling path already exists at the target — skip and report.
        # ``new == old`` is impossible here (filtered out in the plan).
        if new.exists() and new.resolve() != old.resolve():
            print(f"  SKIP: {new.relative_to(ctx.root)} already exists — leaving {old.name} as-is")
            continue
        try:
            old.rename(new)
            moves.append(RefactorMove(source=old, target=new))
            print(f"  Moved: {old.relative_to(ctx.root)} → {new.relative_to(ctx.root)}")
        except OSError as e:
            print(f"  ERROR: {old.relative_to(ctx.root)} — {e}")

    if moves:
        batch_id = next_batch_id(ctx)
        write_refactor_log(batch_id, moves, ctx)
        print(f"\n  Migrated {len(moves)} path(s). Intake log entry: {batch_id}")


# ── Main ─────────────────────────────────────────────────────────────────────


def _scan_extract_propose(
    ctx: ArchiveContext,
) -> tuple[list[Proposal], list[Extraction], list[DuplicateFile]] | None:
    """Run steps 1–3: scan, extract, propose."""
    # 1. SCAN
    print("── Scan ────────────────────────────────────────────────────")
    files = scan_inbox(ctx)
    if not files:
        return None

    # 1.5. DUPLICATE CHECK
    print("\n── Duplicate check ─────────────────────────────────────────")
    duplicates, files = find_duplicates(files, ctx)
    if duplicates:
        print(f"  Found {len(duplicates)} duplicate(s) — will be skipped.")
    else:
        print("  No duplicates found.")

    if not files:
        display_proposals([], [], duplicates)
        return [], [], duplicates

    # 2. EXTRACT
    print("\n── Extract ─────────────────────────────────────────────────")
    extractions = extract_all(files)
    flagged = [e for e in extractions if not e.ok]

    if not any(e.ok for e in extractions):
        print("No readable files — nothing to propose.")
        display_proposals([], extractions, duplicates)
        return [], flagged, duplicates

    # 2.5. TRANSLATE
    if ctx.config.translation_enabled:
        print("\n── Translate ───────────────────────────────────────────────")
        translate_all(extractions, ctx)

    # 3. PROPOSE
    print("\n── Propose ─────────────────────────────────────────────────")
    proposals = propose_all(extractions, ctx)
    display_proposals(proposals, extractions, duplicates)
    return proposals, flagged, duplicates


def _execute_and_report(
    proposals: list[Proposal],
    flagged: list[Extraction],
    duplicates: list[DuplicateFile],
    ctx: ArchiveContext,
    proposals_path: Path | None = None,
) -> None:
    """Run steps 5–6: execute approved proposals, archive duplicates, write log."""
    approved = [p for p in proposals if p.status == "approved"]

    if not approved and not duplicates:
        print("\nNo files approved and no duplicates — nothing to execute.")
        return

    archive_duplicates(duplicates, ctx)

    batch_id = next_batch_id(ctx)
    base_log = ctx.intake_log.read_text() if ctx.intake_log.exists() else ""
    executed_so_far: list[Proposal] = []
    flush_target = proposals_path or ctx.proposals_file

    def _on_executed(p: Proposal) -> None:
        executed_so_far.append(p)
        if flush_target.exists():
            save_proposals(proposals, flagged, duplicates, path=flush_target)
        write_intake_log(
            batch_id, executed_so_far, [], [], ctx, base_content=base_log,
        )

    executed = []
    if approved:
        print("\n── Execute ─────────────────────────────────────────────────")
        executed = execute_all(proposals, ctx, on_executed=_on_executed)

    write_intake_log(
        batch_id, executed_so_far, flagged, duplicates, ctx, base_content=base_log,
    )
    print(f"\n  Intake log updated: {batch_id}")
    report(proposals, flagged, executed, duplicates, ctx)

    errored = [p for p in proposals if p.status == "error"]
    if flush_target.exists():
        if errored:
            print(
                f"\n  {len(errored)} proposal(s) failed to execute — "
                f"preserving {flush_target.name} for review."
            )
        else:
            flush_target.unlink()


def _resolve_archive_root(args: list[str]) -> tuple[Path, list[str]]:
    """Determine archive root and return (root, remaining_args)."""
    if "--archive" in args:
        idx = args.index("--archive")
        if idx + 1 >= len(args):
            print("Error: --archive requires a path argument.")
            sys.exit(1)
        root = Path(args[idx + 1]).resolve()
        remaining = args[:idx] + args[idx + 2:]
        return root, remaining
    return Path.cwd(), args


def main() -> None:
    archive_root, args = _resolve_archive_root(sys.argv[1:])

    # Load config
    config = load_config(archive_root)
    ctx = ArchiveContext(config=config, root=archive_root)

    # Load .env from archive root
    load_dotenv(dotenv_path=archive_root / ".env", override=True)

    # Refresh MODEL after dotenv
    global MODEL
    MODEL = os.getenv("DOCORG_MODEL", "claude-haiku-4-5-20251001")

    # --migrate-charset [--apply]
    if "--migrate-charset" in args:
        apply = "--apply" in args
        migrate_charset(ctx, apply=apply)
        return

    # --refactor --match PATTERN --to FOLDER
    if "--refactor" in args:
        if "--match" not in args or "--to" not in args:
            print("Error: --refactor requires --match PATTERN and --to FOLDER")
            sys.exit(1)
        match_pattern = args[args.index("--match") + 1]
        target_folder = args[args.index("--to") + 1]

        moves = refactor_propose(match_pattern, target_folder, ctx)
        if not moves:
            return
        display_refactor(moves, ctx)
        out = save_refactor(moves, ctx)
        print(f"\n  Plan saved to: {out}")
        print("  Review, then run:")
        print(f"    docorganizer --refactor-execute {out}")
        return

    # --refactor-execute FILE
    if "--refactor-execute" in args:
        idx = args.index("--refactor-execute")
        if idx + 1 >= len(args):
            print("Error: --refactor-execute requires a file path argument.")
            sys.exit(1)
        plan_path = Path(args[idx + 1])
        if not plan_path.exists():
            print(f"Error: file not found: {plan_path}")
            sys.exit(1)

        moves = load_refactor(plan_path)
        print("── Execute refactor ────────────────────────────────────────")
        executed = execute_refactor(moves, ctx)

        if executed:
            batch_id = next_batch_id(ctx)
            write_refactor_log(batch_id, executed, ctx)
            print(f"\n  Intake log updated: {batch_id}")

        print(f"\n── Summary ─────────────────────────────────────────────────")
        print(f"  Moved: {len(executed)} / {len(moves)}")

        if plan_path.exists():
            plan_path.unlink()
        return

    # --validate FILE
    if "--validate" in args:
        idx = args.index("--validate")
        if idx + 1 >= len(args):
            print("Error: --validate requires a file path argument.")
            sys.exit(1)
        proposals_path = Path(args[idx + 1])
        if not proposals_path.exists():
            print(f"Error: file not found: {proposals_path}")
            sys.exit(1)
        proposals, flagged, duplicates = load_proposals(proposals_path)
        registry = build_sender_registry(ctx.root_folder, ctx.config)
        issues = validate_proposals(proposals, registry, ctx.config)
        print(format_validation_report(proposals, issues))
        save_proposals(proposals, flagged, duplicates, path=proposals_path)
        review_count = sum(
            1 for issue_list in issues.values()
            for issue in issue_list if issue.severity == "review"
        )
        if review_count:
            print(f"  {review_count} issue(s) need review in: {proposals_path}")
        else:
            print(f"  Validated proposals saved to: {proposals_path}")
        display_proposals(proposals, [], duplicates)
        return

    # --execute FILE
    if "--execute" in args:
        idx = args.index("--execute")
        if idx + 1 >= len(args):
            print("Error: --execute requires a file path argument.")
            sys.exit(1)
        proposals_path = Path(args[idx + 1])
        if not proposals_path.exists():
            print(f"Error: file not found: {proposals_path}")
            sys.exit(1)
        proposals, flagged, duplicates = load_proposals(proposals_path)
        _execute_and_report(
            proposals, flagged, duplicates, ctx, proposals_path=proposals_path,
        )
        return

    # --propose / --dry-run / interactive

    # Guard: refuse to overwrite unfinished proposals
    if ctx.proposals_file.exists() and "--force" not in args:
        print(f"Unfinished proposals found: {ctx.proposals_file.name}")
        print(
            "  To resume:   docorganizer --execute",
            ctx.proposals_file,
        )
        print("  To discard:  add --force to your command")
        sys.exit(1)

    result = _scan_extract_propose(ctx)
    if result is None:
        return
    proposals, flagged, duplicates = result

    if "--dry-run" in args:
        if proposals:
            registry = build_sender_registry(ctx.root_folder, ctx.config)
            issues = validate_proposals(proposals, registry, ctx.config)
            print(format_validation_report(proposals, issues))
            if issues:
                display_proposals(proposals, [], duplicates)
        print("\n  (dry run — no changes made)")
        return

    if "--propose" in args:
        if not proposals and not duplicates:
            print("\nNo proposals to save.")
            return
        registry = build_sender_registry(ctx.root_folder, ctx.config)
        issues = validate_proposals(proposals, registry, ctx.config)
        print(format_validation_report(proposals, issues))
        out = save_proposals(
            proposals, flagged, duplicates, default_path=ctx.proposals_file,
        )
        review_count = sum(
            1 for issue_list in issues.values()
            for issue in issue_list if issue.severity == "review"
        )
        print(f"\n  Proposals saved to: {out}")
        if review_count:
            print(f"  {review_count} issue(s) need review — edit {out}, then run:")
        else:
            print("  Review, then run:")
        print(f"    docorganizer --execute {out}")
        return

    # Default: interactive mode
    if not proposals:
        return

    proposals = confirm_all(proposals)
    _execute_and_report(proposals, flagged, duplicates, ctx)


if __name__ == "__main__":
    main()
