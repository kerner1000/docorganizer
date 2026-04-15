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

from docorganizer.config import ArchiveConfig

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

    Expected: 'Date - Sender - Topic - Person.ext'
    Returns dict with date, sender, topic, person or None if unparseable.
    """
    stem = Path(name).stem
    parts = stem.split(" - ")
    if len(parts) != 4:
        return None
    return {
        "date": parts[0].strip(),
        "sender": parts[1].strip(),
        "topic": parts[2].strip(),
        "person": parts[3].strip(),
    }


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
            proposal.target_folder = (
                f"{prefix}{proposal.country}/{entry.folder_topic}"
            )
        else:
            proposal.target_folder = f"{prefix}{entry.folder_topic}"

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
        key = (p.sender.lower(), p.person.lower())
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


def _normalize_invoice_topic(proposal) -> list[Issue]:
    """Strip descriptive qualifiers from invoice topics — use bare 'Invoice' only.

    E.g. 'Language Course Invoice' -> 'Invoice',
         'Medical Laboratory Invoice 2021-02' -> 'Invoice 2021-02'.
    Sender field already provides context; topic should not duplicate it.
    """
    issues = []
    topic = proposal.topic
    m = re.match(r"^(.+\s)?(Invoice(?:\s+\d[\d\-]*)?)$", topic, re.IGNORECASE)
    if m and m.group(1):
        new_topic = m.group(2)
        new_topic = "Invoice" + new_topic[len("Invoice"):]
        issues.append(Issue(
            field="topic",
            severity="fixed",
            old_value=topic,
            new_value=new_topic,
            reason=f"Stripped descriptive qualifier — topic reduced to bare '{new_topic}'",
        ))
        proposal.topic = new_topic
    return issues


def _check_filename_separators(proposal) -> list[Issue]:
    """Check for problematic characters in fields that break the naming convention."""
    issues = []
    for field_name in ("sender", "topic", "person"):
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
        issues = []
        issues.extend(_check_date_format(p))
        issues.extend(_check_person(p, config))
        issues.extend(_check_sender_consistency(p, registry, config))
        issues.extend(_normalize_invoice_topic(p))
        issues.extend(_check_tags_valid(p, config))
        issues.extend(_check_filename_separators(p))
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
