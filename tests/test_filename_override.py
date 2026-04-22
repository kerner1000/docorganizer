"""Honor ``proposed_filename`` / ``filename_override`` at execute time.

When a user edits the ``proposed_filename`` field of ``proposals.json`` to a
name that doesn't match the synthesized ``date - sender - topic - person``
pattern, the executor must file the document under that exact name instead
of re-synthesizing it from the structured fields.
"""

from pathlib import Path

from docorganizer.cli import Proposal, execute_all


def _make(tmp_path: Path, name: str, **overrides) -> Proposal:
    src = tmp_path / "inbox" / name
    src.write_bytes(b"%PDF-1.4\n")
    kwargs = dict(
        original_path=src,
        sender="Stadt Heidelberg",
        topic="Rent comparison questionnaire",
        person="Unknown",
        date="2025",
        country="Germany",
        folder_topic="Heidelberg/Flats",
        target_folder="Family/Germany/Heidelberg/Flats",
        tags=["Legal"],
        confidence="Medium",
        notes="",
        status="approved",
    )
    kwargs.update(overrides)
    return Proposal(**kwargs)


def test_synthesized_filename_when_no_override(test_ctx):
    root = test_ctx.root
    (root / "Family" / "Germany" / "Heidelberg" / "Flats").mkdir(parents=True)

    p = _make(root, "src.pdf")
    execute_all([p], test_ctx)

    files = [x.name for x in (root / "Family" / "Germany" / "Heidelberg" / "Flats").iterdir()]
    assert files == [
        "2025 - Stadt Heidelberg - Rent comparison questionnaire - Unknown.pdf"
    ]


def test_filename_override_is_honored(test_ctx):
    """User-set name bypasses the synthesis pattern entirely."""
    root = test_ctx.root
    (root / "Family" / "Germany" / "Heidelberg" / "Flats").mkdir(parents=True)

    p = _make(
        root, "src.pdf",
        filename_override="Fragebogen Heidelberger Mietspiegel 2025.pdf",
    )
    execute_all([p], test_ctx)

    files = [x.name for x in (root / "Family" / "Germany" / "Heidelberg" / "Flats").iterdir()]
    assert files == ["Fragebogen Heidelberger Mietspiegel 2025.pdf"]
    assert p.status == "executed"


def test_proposed_filename_in_json_sets_override(test_ctx):
    """Round-trip via ``Proposal.from_dict``: a non-matching ``proposed_filename``
    is stored as an override so the executor honors it.
    """
    root = test_ctx.root
    (root / "inbox").mkdir(exist_ok=True)
    src = root / "inbox" / "src.pdf"
    src.write_bytes(b"%PDF-1.4\n")

    d = {
        "original_path": str(src),
        "sender": "Stadt Heidelberg",
        "topic": "Rent comparison questionnaire",
        "person": "Unknown",
        "date": "2025",
        "country": "Germany",
        "folder_topic": "Heidelberg/Flats",
        "target_folder": "Family/Germany/Heidelberg/Flats",
        "tags": ["Legal"],
        "confidence": "Medium",
        "notes": "",
        "status": "approved",
        "proposed_filename": "Fragebogen Heidelberger Mietspiegel 2025.pdf",
    }
    p = Proposal.from_dict(d)
    assert p.filename_override == "Fragebogen Heidelberger Mietspiegel 2025.pdf"
    assert p.filename == "Fragebogen Heidelberger Mietspiegel 2025.pdf"


def test_proposed_filename_matching_synthesis_sets_no_override(test_ctx):
    """When ``proposed_filename`` equals the synthesized form, no override is stored."""
    root = test_ctx.root
    src = root / "inbox" / "src.pdf"
    src.write_bytes(b"%PDF-1.4\n")

    d = {
        "original_path": str(src),
        "sender": "Stadt Heidelberg",
        "topic": "Rent comparison questionnaire",
        "person": "Unknown",
        "date": "2025",
        "country": "Germany",
        "folder_topic": "Heidelberg/Flats",
        "target_folder": "Family/Germany/Heidelberg/Flats",
        "tags": ["Legal"],
        "confidence": "Medium",
        "notes": "",
        "status": "approved",
        "proposed_filename":
            "2025 - Stadt Heidelberg - Rent comparison questionnaire - Unknown.pdf",
    }
    p = Proposal.from_dict(d)
    assert p.filename_override is None


def test_override_collision_appends_counter_to_stem(test_ctx):
    """Name collisions on overridden filenames get a ``(N)`` counter before the extension."""
    root = test_ctx.root
    target_dir = root / "Family" / "Germany" / "Heidelberg" / "Flats"
    target_dir.mkdir(parents=True)
    # Pre-existing file with the exact override name
    (target_dir / "Fragebogen Heidelberger Mietspiegel 2025.pdf").write_bytes(b"%PDF-1.4\n")

    first = _make(
        root, "first.pdf",
        filename_override="Fragebogen Heidelberger Mietspiegel 2025.pdf",
    )
    second = _make(
        root, "second.pdf",
        filename_override="Fragebogen Heidelberger Mietspiegel 2025.pdf",
    )
    execute_all([first, second], test_ctx)

    files = sorted(x.name for x in target_dir.iterdir())
    assert files == [
        "Fragebogen Heidelberger Mietspiegel 2025 (2).pdf",
        "Fragebogen Heidelberger Mietspiegel 2025 (3).pdf",
        "Fragebogen Heidelberger Mietspiegel 2025.pdf",
    ]


def test_to_dict_roundtrip_preserves_override(test_ctx):
    """Serializing and re-parsing a proposal preserves the override."""
    root = test_ctx.root
    src = root / "inbox" / "src.pdf"
    src.write_bytes(b"%PDF-1.4\n")

    p = _make(
        root, "src.pdf",
        filename_override="Fragebogen Heidelberger Mietspiegel 2025.pdf",
    )
    # Can't reuse src.pdf because _make already created it; rebuild via dict path
    d = p.to_dict()
    assert d["filename_override"] == "Fragebogen Heidelberger Mietspiegel 2025.pdf"

    restored = Proposal.from_dict(d)
    assert restored.filename_override == "Fragebogen Heidelberger Mietspiegel 2025.pdf"
    assert restored.filename == "Fragebogen Heidelberger Mietspiegel 2025.pdf"
