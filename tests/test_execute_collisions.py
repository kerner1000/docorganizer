"""Collision-counter behavior in execute_all."""

from pathlib import Path

from docorganizer.cli import Proposal, execute_all


def _make_proposal(tmp_path: Path, name: str, topic: str) -> Proposal:
    src = tmp_path / "inbox" / name
    src.write_bytes(b"%PDF-1.4\n")
    return Proposal(
        original_path=src,
        sender="Anthropic, PBC",
        topic=topic,
        person="Alexander",
        date="2026-03-31",
        country="Switzerland",
        folder_topic="Business",
        target_folder="Family/Switzerland/Invoices",
        tags=["Tax"],
        confidence="High",
        notes="",
        status="approved",
    )


def test_collision_appends_counter(test_ctx):
    root = test_ctx.root
    (root / "Family" / "Switzerland" / "Invoices").mkdir(parents=True)

    first = _make_proposal(root, "first.pdf", "Invoice")
    second = _make_proposal(root, "second.pdf", "Invoice")
    third = _make_proposal(root, "third.pdf", "Invoice")

    execute_all([first, second, third], test_ctx)

    inv_dir = root / "Family" / "Switzerland" / "Invoices"
    files = sorted(p.name for p in inv_dir.iterdir())
    assert files == [
        "2026-03-31 - Anthropic, PBC - Invoice (2) - Alexander.pdf",
        "2026-03-31 - Anthropic, PBC - Invoice (3) - Alexander.pdf",
        "2026-03-31 - Anthropic, PBC - Invoice - Alexander.pdf",
    ]
    assert all(p.status == "executed" for p in [first, second, third])
    assert second.topic == "Invoice (2)"
    assert third.topic == "Invoice (3)"


def test_no_collision_keeps_topic(test_ctx):
    root = test_ctx.root
    (root / "Family" / "Switzerland" / "Invoices").mkdir(parents=True)

    only = _make_proposal(root, "only.pdf", "Invoice")
    execute_all([only], test_ctx)

    inv_dir = root / "Family" / "Switzerland" / "Invoices"
    assert [p.name for p in inv_dir.iterdir()] == [
        "2026-03-31 - Anthropic, PBC - Invoice - Alexander.pdf"
    ]
    assert only.topic == "Invoice"
    assert only.status == "executed"
