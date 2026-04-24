"""Error-isolation behavior in execute_all.

A proposal whose rename fails (e.g. because a filename field slipped past the
validator with a path-unsafe character) must not abort the remaining proposals
in the batch. Failures are recorded on the proposal with status="error".
"""

from pathlib import Path
from unittest.mock import patch

from docorganizer.cli import Proposal, execute_all


def _make_proposal(tmp_path: Path, name: str, sender: str, topic: str) -> Proposal:
    src = tmp_path / "inbox" / name
    src.write_bytes(b"%PDF-1.4\n")
    return Proposal(
        original_path=src,
        sender=sender,
        topic=topic,
        person="Unknown",
        date="2021-12-27",
        country="Russia",
        folder_topic="Invoices",
        target_folder="Family/Russia/Invoices",
        tags=[],
        confidence="High",
        notes="",
        status="approved",
    )


def test_slash_in_sender_sanitized_at_execute(test_ctx):
    """Defensive: if a '/' slips past validation, execute_all strips it and succeeds."""
    root = test_ctx.root
    p = _make_proposal(root, "scan.pdf", "Fielmann / EyeKraft Optica", "Eyeglasses")

    executed = execute_all([p], test_ctx)

    assert p.status == "executed"
    assert executed == [p]
    assert p.sender == "Fielmann EyeKraft Optica"
    target = (
        root / "Family" / "Russia" / "Invoices"
        / "2021-12-27 - Fielmann EyeKraft Optica - Eyeglasses - Unknown.pdf"
    )
    assert target.exists()


def test_mid_batch_rename_failure_does_not_abort(test_ctx):
    """A single bad rename must not cascade — remaining proposals still move."""
    root = test_ctx.root
    (root / "Family" / "Russia" / "Invoices").mkdir(parents=True)

    first = _make_proposal(root, "first.pdf", "Good Sender", "First Topic")
    bad = _make_proposal(root, "bad.pdf", "Bad Sender", "Bad Topic")
    third = _make_proposal(root, "third.pdf", "Good Sender", "Third Topic")

    real_rename = Path.rename

    def flaky_rename(self: Path, target):
        if self.name == "bad.pdf":
            raise OSError(2, "injected failure for test")
        return real_rename(self, target)

    with patch.object(Path, "rename", flaky_rename):
        execute_all([first, bad, third], test_ctx)

    assert first.status == "executed"
    assert bad.status == "error"
    assert "injected failure" in bad.notes
    assert third.status == "executed"

    inv_dir = root / "Family" / "Russia" / "Invoices"
    names = sorted(f.name for f in inv_dir.iterdir())
    assert names == [
        "2021-12-27 - Good Sender - First Topic - Unknown.pdf",
        "2021-12-27 - Good Sender - Third Topic - Unknown.pdf",
    ]
    # Bad file stays in the inbox for manual review
    assert bad.original_path.exists()
