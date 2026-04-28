"""Recovery from corrupted ``original_path`` via content-hash lookup.

When a user or agent edits proposals.json with a global replace-all and the
search string also appears in a source filename, ``original_path`` is
silently rewritten to point at a file that doesn't exist. The executor
falls back to scanning the inbox for a file whose SHA-256 matches the
``file_hash`` stored on the proposal at propose-time.
"""

from pathlib import Path

from docorganizer.cli import (
    Proposal,
    compute_file_hash,
    execute_all,
)


def _make_proposal(
    src: Path, sender: str, topic: str, *, file_hash: str | None
) -> Proposal:
    return Proposal(
        original_path=src,
        sender=sender,
        topic=topic,
        person="Alexander",
        date="2026-01-15",
        country="Germany",
        folder_topic="Banking",
        target_folder="Family/Germany/Banking",
        tags=[],
        confidence="High",
        notes="",
        status="approved",
        file_hash=file_hash,
    )


def test_recovers_when_original_path_corrupted(test_ctx):
    """original_path no longer exists; hash matches a real inbox file → recover."""
    root = test_ctx.root
    real_file = root / "inbox" / "Girokonto_Kontoauszug_20260403.pdf"
    real_file.write_bytes(b"%PDF-1.4\nactual bytes\n")
    real_hash = compute_file_hash(real_file)

    # The proposal's original_path was corrupted by an accidental string
    # replace ("Kontoauszug" → "Account Statement") — points at a name
    # that does not exist on disk.
    bogus = root / "inbox" / "Girokonto_Account Statement_20260403.pdf"
    p = _make_proposal(bogus, "ING-DiBa AG", "Account Statement", file_hash=real_hash)

    execute_all([p], test_ctx)

    assert p.status == "executed"
    target = (
        root / "Family" / "Germany" / "Banking"
        / "2026-01-15 - ING-DiBa AG - Account Statement - Alexander.pdf"
    )
    assert target.exists()
    assert not real_file.exists()  # the real file was moved


def test_missing_when_no_hash_stored(test_ctx):
    """Older proposals without file_hash retain the legacy MISSING behavior."""
    root = test_ctx.root
    bogus = root / "inbox" / "does_not_exist.pdf"
    p = _make_proposal(bogus, "ING-DiBa AG", "Account Statement", file_hash=None)

    execute_all([p], test_ctx)

    assert p.status == "skipped"


def test_missing_when_hash_does_not_match(test_ctx):
    """Hash stored but no inbox file matches → still MISSING, no false recovery."""
    root = test_ctx.root
    decoy = root / "inbox" / "decoy.pdf"
    decoy.write_bytes(b"%PDF-1.4\ndifferent bytes\n")

    bogus = root / "inbox" / "missing.pdf"
    p = _make_proposal(
        bogus, "ING-DiBa AG", "Account Statement",
        file_hash="0" * 64,  # hash of nothing real
    )

    execute_all([p], test_ctx)

    assert p.status == "skipped"
    assert decoy.exists()  # decoy untouched
