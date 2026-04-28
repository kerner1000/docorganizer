"""Post-proposal validation and auto-correction.

Runs between --propose and --execute to catch and fix mechanical issues
that would otherwise require expensive Opus review. Only genuinely
ambiguous cases are left for human review.

All validation functions receive an ArchiveConfig to access the archive's
people, countries, tags, and root_folder — nothing is hardcoded.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from docorganizer.config import ArchiveConfig, date_to_subfolder

# ── Constants ────────────────────────────────────────────────────────────────

DATE_PATTERN = re.compile(
    r"^(?:Undated|\d{4}(?:-\d{2}(?:-\d{2})?)?)$"
)

# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class SenderEntry:
    """Known sender with defaults inferred from filing history."""
    canonical_name: str
    country: str
    default_tags: list[str] = field(default_factory=list)
    folder_topic: str = "Unsorted"
    filing_count: int = 0


@dataclass
class Issue:
    """A validation finding for a single proposal."""
    field: str          # which proposal field (sender, date, person, tags, ...)
    severity: str       # "fixed" (auto-corrected) or "review" (needs human)
    old_value: str
    new_value: str
    reason: str


# ── Sender registry ─────────────────────────────────────────────────────────


def _parse_filename(name: str) -> dict | None:
    """Parse a filed document name into fields.

    Expected: 'Date - Sender - Topic - Person.ext' (4 fields)
    or:       'Date - Sender - Topic.ext' (3 fields, no person tracking)
    Returns dict with date, sender, topic, and optionally person, or None if unparseable.
    """
    stem = Path(name).stem
    parts = stem.split(" - ")
    if len(parts) == 4:
        return {
            "date": parts[0].strip(),
            "sender": parts[1].strip(),
            "topic": parts[2].strip(),
            "person": parts[3].strip(),
        }
    if len(parts) == 3:
        return {
            "date": parts[0].strip(),
            "sender": parts[1].strip(),
            "topic": parts[2].strip(),
        }
    return None


def build_sender_registry(
    root: Path, config: ArchiveConfig,
) -> dict[str, SenderEntry]:
    """Scan the root folder tree and build a sender registry from existing filenames.

    Key: lowercased sender name for fuzzy matching.
    Value: SenderEntry with canonical name, inferred country, tags, folder.

    With countries configured: scans root/country/topic/*.pdf
    Without countries: scans root/topic/*.pdf
    """
    registry: dict[str, SenderEntry] = {}

    if not root.exists():
        return registry

    if config.countries:
        for country_dir in sorted(root.iterdir()):
            if not country_dir.is_dir() or country_dir.name.startswith("."):
                continue
            if country_dir.name not in config.countries_set:
                continue
            _scan_dir_for_senders(
                country_dir, country_dir.name, registry, config,
            )
    else:
        _scan_dir_for_senders(root, "", registry, config)

    return registry


def _scan_dir_for_senders(
    scan_root: Path,
    country: str,
    registry: dict[str, SenderEntry],
    config: ArchiveConfig,
) -> None:
    """Scan a directory tree for filed PDFs and register their senders."""
    for path in scan_root.rglob("*.pdf"):
        if path.name.startswith("."):
            continue

        parsed = _parse_filename(path.name)
        if not parsed:
            continue

        sender_lower = parsed["sender"].lower()

        # Determine folder_topic from path
        rel = path.parent.relative_to(scan_root)
        folder_parts = rel.parts
        folder_topic = folder_parts[0] if folder_parts else "Unsorted"

        tags = _read_tags(path)

        if sender_lower in registry:
            entry = registry[sender_lower]
            entry.filing_count += 1
            for t in tags:
                if t in config.controlled_tags and t not in entry.default_tags:
                    entry.default_tags.append(t)
        else:
            registry[sender_lower] = SenderEntry(
                canonical_name=parsed["sender"],
                country=country,
                default_tags=[t for t in tags if t in config.controlled_tags],
                folder_topic=folder_topic,
                filing_count=1,
            )


def _read_tags(path: Path) -> list[str]:
    """Read macOS Finder tags from a file's xattr. Returns [] on failure."""
    try:
        import plistlib
        import xattr as xattr_lib

        raw = xattr_lib.getxattr(str(path), "com.apple.metadata:_kMDItemUserTags")
        values = plistlib.loads(raw)
        # Tags are stored as "TagName\n0" — strip the colour suffix
        return [v.split("\n")[0] for v in values if isinstance(v, str)]
    except Exception:
        return []


# ── Validation rules ────────────────────────────────────────────────────────


def _check_date_format(proposal) -> list[Issue]:
    """Validate date field matches expected format."""
    issues = []
    if not DATE_PATTERN.match(proposal.date):
        issues.append(Issue(
            field="date",
            severity="review",
            old_value=proposal.date,
            new_value="",
            reason=f"Date '{proposal.date}' doesn't match YYYY-MM-DD / YYYY-MM / YYYY / Undated",
        ))
    return issues


def _check_person(proposal, config: ArchiveConfig) -> list[Issue]:
    """Validate and auto-correct person field against known people."""
    issues = []

    if not config.use_person:
        return issues

    person = proposal.person

    if person in config.people or person == "Unknown":
        return issues

    # Try to match against known full names / variations
    for first_name, variants in config.people.items():
        for variant in variants:
            if person.lower() == variant.lower():
                issues.append(Issue(
                    field="person",
                    severity="fixed",
                    old_value=person,
                    new_value=first_name,
                    reason=f"Normalized '{person}' to first name '{first_name}'",
                ))
                proposal.person = first_name
                return issues

    known = ", ".join(config.people.keys())
    issues.append(Issue(
        field="person",
        severity="review",
        old_value=person,
        new_value="",
        reason=f"Person '{person}' is not a known person ({known}, Unknown)",
    ))
    return issues


def _check_sender_consistency(
    proposal, registry: dict[str, SenderEntry], config: ArchiveConfig,
) -> list[Issue]:
    """Normalize sender against registry; infer country/tags/folder from history."""
    issues = []
    sender_lower = proposal.sender.lower()

    if sender_lower not in registry:
        return issues  # New sender — nothing to validate against

    entry = registry[sender_lower]

    # Auto-correct sender name to canonical form
    if proposal.sender != entry.canonical_name:
        issues.append(Issue(
            field="sender",
            severity="fixed",
            old_value=proposal.sender,
            new_value=entry.canonical_name,
            reason=f"Normalized to canonical name from registry ({entry.filing_count} prior filings)",
        ))
        proposal.sender = entry.canonical_name

    # Auto-correct country if missing or inconsistent
    if not proposal.country and entry.country:
        issues.append(Issue(
            field="country",
            severity="fixed",
            old_value=proposal.country,
            new_value=entry.country,
            reason=f"Inferred from sender registry ({entry.filing_count} prior filings in {entry.country})",
        ))
        proposal.country = entry.country

    # Suggest missing tags from sender history
    for tag in entry.default_tags:
        if tag not in proposal.tags:
            issues.append(Issue(
                field="tags",
                severity="fixed",
                old_value=", ".join(proposal.tags) or "(none)",
                new_value=tag,
                reason=f"Added '{tag}' — all prior filings from this sender had it",
            ))
            proposal.tags.append(tag)

    # Suggest folder from history (only if proposal has Unsorted)
    if proposal.folder_topic == "Unsorted" and entry.folder_topic != "Unsorted":
        issues.append(Issue(
            field="folder_topic",
            severity="fixed",
            old_value=proposal.folder_topic,
            new_value=entry.folder_topic,
            reason=f"Inferred from sender registry — prior filings in '{entry.folder_topic}'",
        ))
        proposal.folder_topic = entry.folder_topic
        prefix = config.root_folder_prefix
        if proposal.country:
            target = f"{prefix}{proposal.country}/{entry.folder_topic}"
        else:
            target = f"{prefix}{entry.folder_topic}"
        if config.date_subfolders:
            target = f"{target}/{date_to_subfolder(proposal.date)}"
        proposal.target_folder = target

    return issues


def _check_batch_sender_drift(proposals: list) -> list[tuple[int, Issue]]:
    """Detect same sender named differently within the batch."""
    issues = []
    sender_variants: dict[str, list[tuple[int, str]]] = {}
    for i, p in enumerate(proposals):
        key = p.sender.lower()
        sender_variants.setdefault(key, []).append((i, p.sender))

    for key, entries in sender_variants.items():
        names = set(name for _, name in entries)
        if len(names) > 1:
            counts = Counter(name for _, name in entries)
            canonical = counts.most_common(1)[0][0]
            for idx, name in entries:
                if name != canonical:
                    issues.append((idx, Issue(
                        field="sender",
                        severity="fixed",
                        old_value=name,
                        new_value=canonical,
                        reason=f"Inconsistent within batch — normalized to '{canonical}'",
                    )))
                    proposals[idx].sender = canonical

    return issues


def _check_batch_topic_drift(proposals: list) -> list[tuple[int, Issue]]:
    """Detect same document type with inconsistent topic wording within batch."""
    issues = []
    groups: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for i, p in enumerate(proposals):
        key = (p.sender.lower(), p.person.lower()) if p.person else (p.sender.lower(),)
        groups.setdefault(key, []).append((i, p.topic))

    for key, entries in groups.items():
        base_topics: dict[str, list[tuple[int, str]]] = {}
        for idx, topic in entries:
            base = re.sub(r"\s+\d[\d\-]*$", "", topic).lower().strip()
            base_topics.setdefault(base, []).append((idx, topic))

        if len(base_topics) > 1:
            all_topics = [
                (idx, topic)
                for entries in base_topics.values()
                for idx, topic in entries
            ]
            topic_set = set(t.lower() for _, t in all_topics)
            if len(topic_set) > 1 and len(topic_set) <= 3:
                prefixes = set()
                for t in topic_set:
                    words = t.split()
                    if len(words) >= 2:
                        prefixes.add(" ".join(words[:2]))
                if len(prefixes) == 1:
                    counts = Counter(t for _, t in all_topics)
                    canonical = counts.most_common(1)[0][0]
                    for idx, topic in all_topics:
                        if topic != canonical:
                            issues.append((idx, Issue(
                                field="topic",
                                severity="review",
                                old_value=topic,
                                new_value=canonical,
                                reason=f"Possible topic drift — similar to '{canonical}' in same batch",
                            )))

    return issues


def _check_tags_valid(proposal, config: ArchiveConfig) -> list[Issue]:
    """Ensure only controlled vocabulary tags are present."""
    issues = []
    invalid = [t for t in proposal.tags if t not in config.controlled_tags]
    if invalid:
        proposal.tags = [t for t in proposal.tags if t in config.controlled_tags]
        issues.append(Issue(
            field="tags",
            severity="fixed",
            old_value=", ".join(invalid),
            new_value=", ".join(proposal.tags),
            reason=f"Removed invalid tag(s): {', '.join(invalid)}",
        ))
    return issues


# Common document-type words whose topics should reduce to the bare type.
# Order matters: multi-word types (e.g. "Purchase contract") must be checked
# BEFORE their single-word counterparts ("Contract") so they match as a unit.
_DOCUMENT_TYPE_WORDS: tuple[tuple[str, str], ...] = (
    ("purchase contract", "Purchase contract"),
    ("invoice", "Invoice"),
    ("order", "Order"),
    ("receipt", "Receipt"),
    ("statement", "Statement"),
    ("contract", "Contract"),
    ("policy", "Policy"),
)

# Period suffix at the end of a topic, e.g. " 2023", " 2023-12", " 2021-Q4".
# Uses the existing lenient pattern (\d[\d\-]*) so Q-suffixed periods keep working.
_PERIOD_SUFFIX = r"\s+\d[\d\-]*"


def _normalize_document_type_topic(proposal) -> list[Issue]:
    """Strip descriptive qualifiers from common document-type topics.

    Reduces topics like 'Interior design invoice — window treatments',
    'Tax Invoice', or 'Eyeglasses order and warranty — Fielmann BD481 CL' to
    the bare canonical type word ('Invoice', 'Order', ...). An optional
    trailing period suffix (e.g. '2023-12') is preserved so that
    'Medical Laboratory Invoice 2021-02' becomes 'Invoice 2021-02'.

    Covered types: Invoice, Order, Receipt, Statement, Contract,
    Purchase contract, Policy. Multi-word types are checked first so they
    match as a unit (a 'Purchase contract — bed frame' topic reduces to
    'Purchase contract', not 'Contract').

    The Sender field already provides context; topics should not duplicate it.
    """
    issues = []
    topic = proposal.topic

    for type_lower, canonical in _DOCUMENT_TYPE_WORDS:
        # Match "<canonical>" or "<canonical> <period>" as the entire topic,
        # case-insensitive — already bare, nothing to fix.
        bare_pattern = rf"^{re.escape(type_lower)}(?:{_PERIOD_SUFFIX})?$"
        if re.match(bare_pattern, topic, re.IGNORECASE):
            return issues

        # Match the type as a word somewhere in the topic (word boundaries on
        # both sides so 'Contract' doesn't accidentally match 'Contracts').
        word_pattern = rf"\b{re.escape(type_lower)}\b"
        if not re.search(word_pattern, topic, re.IGNORECASE):
            continue

        # Preserve a period suffix ONLY when it directly follows the type word
        # and runs to the end of the topic (e.g. 'Medical Lab Invoice 2021-02'
        # keeps '2021-02', but 'Account Statement — December 2025' does not —
        # "December" interrupts the type→period adjacency).
        direct_period_pattern = rf"\b{re.escape(type_lower)}({_PERIOD_SUFFIX})$"
        direct_match = re.search(direct_period_pattern, topic, re.IGNORECASE)
        new_topic = canonical + (direct_match.group(1) if direct_match else "")

        issues.append(Issue(
            field="topic",
            severity="fixed",
            old_value=topic,
            new_value=new_topic,
            reason=f"Stripped descriptive qualifier — topic reduced to bare '{new_topic}'",
        ))
        proposal.topic = new_topic
        return issues

    return issues


def _check_filename_separators(proposal) -> list[Issue]:
    """Check for problematic characters in fields that break the naming convention."""
    issues = []
    fields = ["sender", "topic"]
    if proposal.person:
        fields.append("person")
    for field_name in fields:
        value = getattr(proposal, field_name)
        if " - " in value:
            cleaned = value.replace(" - ", " — ")
            issues.append(Issue(
                field=field_name,
                severity="fixed",
                old_value=value,
                new_value=cleaned,
                reason="Replaced ' - ' with ' — ' to avoid breaking filename field separator",
            ))
            setattr(proposal, field_name, cleaned)
    return issues


# Characters that break pathlib.Path.rename() when present in a filename field:
# "/" and "\" are path separators on POSIX / Windows respectively; NUL terminates
# C strings and is rejected by the kernel. Replace with a safe alternative
# rather than failing the whole batch.
_PATH_UNSAFE_CHARS = ("/", "\\", "\x00")


def sanitize_field(value: str) -> str:
    """Strip path-unsafe characters from a single filename field.

    Replaces "/", "\\", and NUL with a space, then collapses runs of
    whitespace. Returns the value unchanged if no unsafe char is present.
    """
    if not any(c in value for c in _PATH_UNSAFE_CHARS):
        return value
    cleaned = value
    for c in _PATH_UNSAFE_CHARS:
        cleaned = cleaned.replace(c, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _check_path_unsafe_chars(proposal) -> list[Issue]:
    """Auto-fix path-unsafe characters (``/``, ``\\``, NUL) in filename fields.

    A raw ``/`` in a sender or topic causes pathlib to interpret the field as
    a directory separator at rename time, which either creates unwanted
    intermediate directories or raises FileNotFoundError and aborts the batch.
    """
    issues = []
    fields = ["sender", "topic"]
    if proposal.person:
        fields.append("person")
    for field_name in fields:
        value = getattr(proposal, field_name)
        cleaned = sanitize_field(value)
        if cleaned != value:
            issues.append(Issue(
                field=field_name,
                severity="fixed",
                old_value=value,
                new_value=cleaned,
                reason="Removed path-unsafe character(s) — '/', '\\\\', or NUL would break filesystem rename",
            ))
            setattr(proposal, field_name, cleaned)
    return issues


# ── Main validation entry point ─────────────────────────────────────────────


def validate_proposals(
    proposals: list,
    registry: dict[str, SenderEntry],
    config: ArchiveConfig,
) -> dict[int, list[Issue]]:
    """Run all validation rules on proposals. Returns {proposal_index: [issues]}.

    Auto-fixes are applied in-place on the proposal objects.
    Issues with severity="review" need human attention.
    Issues with severity="fixed" are informational — already corrected.
    """
    all_issues: dict[int, list[Issue]] = {}

    for i, p in enumerate(proposals):
        # Non-archive disposals keep the original filename and don't get filed —
        # the field-level naming/topic checks don't apply.
        if getattr(p, "non_archive_reason", None):
            continue
        issues = []
        issues.extend(_check_date_format(p))
        issues.extend(_check_person(p, config))
        issues.extend(_check_sender_consistency(p, registry, config))
        issues.extend(_normalize_document_type_topic(p))
        issues.extend(_check_tags_valid(p, config))
        issues.extend(_check_filename_separators(p))
        issues.extend(_check_path_unsafe_chars(p))
        if issues:
            all_issues[i] = issues

    for idx, issue in _check_batch_sender_drift(proposals):
        all_issues.setdefault(idx, []).append(issue)
    for idx, issue in _check_batch_topic_drift(proposals):
        all_issues.setdefault(idx, []).append(issue)

    return all_issues


def format_validation_report(
    proposals: list,
    issues: dict[int, list[Issue]],
) -> str:
    """Format validation results for display."""
    lines = []
    fixed_count = 0
    review_count = 0

    for i, p in enumerate(proposals):
        if i not in issues:
            continue

        lines.append(f"\n  [{i + 1}] {p.original_path.name}")
        for issue in issues[i]:
            if issue.severity == "fixed":
                fixed_count += 1
                lines.append(f"      FIXED  {issue.field}: {issue.reason}")
            else:
                review_count += 1
                lines.append(f"      REVIEW {issue.field}: {issue.reason}")

    summary = f"  Auto-fixed: {fixed_count}  |  Needs review: {review_count}"

    if not lines:
        return "── Validate ────────────────────────────────────────────────\n  All proposals passed validation.\n"

    header = "── Validate ────────────────────────────────────────────────"
    return header + "\n" + "\n".join(lines) + "\n\n" + summary + "\n"
