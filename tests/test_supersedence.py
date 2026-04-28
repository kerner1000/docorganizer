"""Tests for document_id-based supersedence detection and routing."""

from pathlib import Path

import pytest

from docorganizer.cli import (
    Proposal,
    _archive_superseded,
    _build_proposal,
    _parse_filename_date,
    detect_supersedence,
    execute_all,
)
from docorganizer.config import ArchiveConfig, ArchiveContext


@pytest.fixture
def sup_config():
    return ArchiveConfig(
        name="t", root_folder=None, people={}, countries=[],
        tags={}, mandatory_tags=[], prompt_context="",
    )


@pytest.fixture
def sup_ctx(tmp_path, sup_config):
    (tmp_path / "inbox").mkdir()
    return ArchiveContext(config=sup_config, root=tmp_path)


def _data(**overrides):
    base = {
        "date": "2026-04-27", "sender": "Acme Corp", "topic": "Offer",
        "country": "none", "folder_topic": "Acme Corp",
        "tags": [], "confidence": "High", "notes": "",
    }
    base.update(overrides)
    return base


def _make_proposal(path: Path, **fields) -> Proposal:
    defaults = dict(
        sender="Acme Corp", topic="Offer", person="", date="2026-04-27",
        country="", folder_topic="Acme Corp",
        target_folder="Acme Corp/2026-04",
        tags=[], confidence="High", notes="",
    )
    defaults.update(fields)
    return Proposal(original_path=path, **defaults)


# ── Date parsing ───────────────────────────────────────────────────────────


class TestParseFilenameDate:
    def test_full_date(self):
        assert _parse_filename_date("2026-04-22 - Acme - Offer 362283.pdf") == "2026-04-22"

    def test_year_month(self):
        assert _parse_filename_date("2026-04 - Acme - Offer.pdf") == "2026-04"

    def test_year_only(self):
        assert _parse_filename_date("2026 - Acme - Offer.pdf") == "2026"

    def test_no_date_returns_none(self):
        assert _parse_filename_date("Acme Offer.pdf") is None

    def test_non_iso_prefix_returns_none(self):
        assert _parse_filename_date("April 2026 - Acme.pdf") is None


# ── Detection ──────────────────────────────────────────────────────────────


class TestDetectSupersedence:
    def test_finds_older_archived_with_same_id(self, sup_ctx):
        old = sup_ctx.root / "Acme Corp/2026-04/2026-04-22 - Acme Corp - Offer 362283.pdf"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("old version")

        new_path = sup_ctx.inbox / "new.pdf"
        new_path.write_text("new version")
        p = _make_proposal(new_path, document_id="362283", date="2026-04-27")

        detect_supersedence([p], sup_ctx)
        assert p.supersedes == [old]

    def test_does_not_supersede_when_archived_is_newer(self, sup_ctx):
        new_archived = sup_ctx.root / "Acme/2026-04/2026-04-29 - Acme - Offer 362283.pdf"
        new_archived.parent.mkdir(parents=True, exist_ok=True)
        new_archived.write_text("newer than incoming")

        new_path = sup_ctx.inbox / "older.pdf"
        new_path.write_text("incoming")
        p = _make_proposal(new_path, document_id="362283", date="2026-04-22")

        detect_supersedence([p], sup_ctx)
        assert p.supersedes == []

    def test_does_not_supersede_when_dates_equal(self, sup_ctx):
        same = sup_ctx.root / "Acme/2026-04/2026-04-27 - Acme - Offer 362283.pdf"
        same.parent.mkdir(parents=True, exist_ok=True)
        same.write_text("same date")

        new_path = sup_ctx.inbox / "today.pdf"
        new_path.write_text("incoming")
        p = _make_proposal(new_path, document_id="362283", date="2026-04-27")

        detect_supersedence([p], sup_ctx)
        assert p.supersedes == []

    def test_short_id_skipped(self, sup_ctx):
        # ID too short — substring match would false-positive on dates etc.
        old = sup_ctx.root / "Acme/2026-04/2026-04-22 - Acme - Offer 12.pdf"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("old")

        new_path = sup_ctx.inbox / "new.pdf"
        new_path.write_text("new")
        p = _make_proposal(new_path, document_id="12", date="2026-04-27")

        detect_supersedence([p], sup_ctx)
        assert p.supersedes == []

    def test_no_document_id_skipped(self, sup_ctx):
        old = sup_ctx.root / "Acme/2026-04/2026-04-22 - Acme - Offer.pdf"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("old")

        new_path = sup_ctx.inbox / "new.pdf"
        new_path.write_text("new")
        p = _make_proposal(new_path, date="2026-04-27")  # no document_id

        detect_supersedence([p], sup_ctx)
        assert p.supersedes == []

    def test_skips_non_archive_proposal(self, sup_ctx):
        old = sup_ctx.root / "Acme/2026-04/2026-04-22 - Acme - Offer 362283.pdf"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("old")

        new_path = sup_ctx.inbox / "new.pdf"
        new_path.write_text("new")
        p = _make_proposal(
            new_path, document_id="362283", date="2026-04-27",
            non_archive_reason="not relevant",
        )

        detect_supersedence([p], sup_ctx)
        assert p.supersedes == []

    def test_unparseable_archive_date_skipped(self, sup_ctx):
        old = sup_ctx.root / "Acme/Misc/Acme Offer 362283.pdf"  # no date prefix
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("old")

        new_path = sup_ctx.inbox / "new.pdf"
        new_path.write_text("new")
        p = _make_proposal(new_path, document_id="362283", date="2026-04-27")

        detect_supersedence([p], sup_ctx)
        assert p.supersedes == []


# ── Archive move ───────────────────────────────────────────────────────────


class TestArchiveSuperseded:
    def test_mirrors_path_under_archive(self, sup_ctx):
        old = sup_ctx.root / "Acme/2026-04/2026-04-22 - Acme - Offer 362283.pdf"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("data")

        new_path = _archive_superseded(old, sup_ctx)
        assert not old.exists()
        expected = sup_ctx.archive / "Acme/2026-04/2026-04-22 - Acme - Offer 362283 Superseded.pdf"
        assert new_path == expected
        assert new_path.exists()

    def test_collision_appends_counter(self, sup_ctx):
        old1 = sup_ctx.root / "Acme/2026-04/file.pdf"
        old1.parent.mkdir(parents=True, exist_ok=True)
        old1.write_text("first")
        # Pre-create the would-be target to force collision handling
        existing = sup_ctx.archive / "Acme/2026-04/file Superseded.pdf"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("already there")

        new_path = _archive_superseded(old1, sup_ctx)
        assert new_path.name == "file Superseded (2).pdf"


# ── Execute integration ────────────────────────────────────────────────────


class TestExecuteSupersedence:
    def test_supersedes_archived_then_files_new(self, sup_ctx):
        old = sup_ctx.root / "Acme Corp/2026-04/2026-04-22 - Acme Corp - Offer 362283.pdf"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("old offer")

        src = sup_ctx.inbox / "incoming.pdf"
        src.write_text("new offer")

        p = _make_proposal(
            src,
            sender="Acme Corp", topic="Offer", date="2026-04-27",
            target_folder="Acme Corp/2026-04",
            document_id="362283", disambiguator="362283",
        )
        p.supersedes = [old]
        p.status = "approved"

        execute_all([p], sup_ctx)

        # Old file moved into _archive with Superseded suffix
        archived = sup_ctx.archive / "Acme Corp/2026-04/2026-04-22 - Acme Corp - Offer 362283 Superseded.pdf"
        assert archived.exists()
        assert not old.exists()

        # New file filed at expected target
        expected_new = sup_ctx.root / "Acme Corp/2026-04/2026-04-27 - Acme Corp - Offer 362283.pdf"
        assert expected_new.exists()
        assert not src.exists()


# ── _build_proposal extracts document_id ───────────────────────────────────


class TestBuildProposalDocumentId:
    def test_document_id_passes_through(self, sup_config, tmp_path):
        path = tmp_path / "x.pdf"
        p = _build_proposal(path, _data(document_id="362283"), sup_config)
        assert p.document_id == "362283"

    def test_empty_document_id_normalized_to_none(self, sup_config, tmp_path):
        path = tmp_path / "x.pdf"
        p = _build_proposal(path, _data(document_id="   "), sup_config)
        assert p.document_id is None


# ── Serialization ──────────────────────────────────────────────────────────


class TestSerialization:
    def test_round_trip_preserves_document_id_and_supersedes(self, tmp_path):
        old = tmp_path / "Acme/2026-04/old.pdf"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("x")
        p = _make_proposal(
            tmp_path / "inbox/new.pdf",
            document_id="362283", supersedes=[old],
        )
        d = p.to_dict()
        d.pop("proposed_filename", None)
        d["original_path"] = str(p.original_path)
        restored = Proposal.from_dict(d)
        assert restored.document_id == "362283"
        assert restored.supersedes == [old]
