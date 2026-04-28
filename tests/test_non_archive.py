"""Tests for the optional non-archive routing destination."""

from pathlib import Path

import pytest

from docorganizer.cli import (
    Proposal,
    _build_proposal,
    apply_business_routing,
    apply_three_document_rule,
    execute_all,
)
from docorganizer.config import (
    ArchiveConfig,
    ArchiveContext,
    BusinessRoutingRule,
    load_config,
)
from docorganizer.validator import validate_proposals


@pytest.fixture
def na_config():
    return ArchiveConfig(
        name="t", root_folder=None, people={}, countries=[],
        tags={"Bookkeeping": "Vendor receipts"}, mandatory_tags=[],
        prompt_context="",
        non_archive_dir="inbox/processed",
    )


@pytest.fixture
def na_ctx(tmp_path, na_config):
    (tmp_path / "inbox").mkdir()
    return ArchiveContext(config=na_config, root=tmp_path)


def _data(**overrides) -> dict:
    base = {
        "date": "2026-04-28", "sender": "Drafts", "topic": "Template",
        "country": "none", "folder_topic": "Drafts",
        "tags": [], "confidence": "Medium", "notes": "",
    }
    base.update(overrides)
    return base


class TestConfigLoading:
    def test_default_is_none(self, tmp_path):
        (tmp_path / "docorganizer.yaml").write_text("name: t\n")
        cfg = load_config(tmp_path)
        assert cfg.non_archive_dir is None

    def test_loaded_from_yaml(self, tmp_path):
        (tmp_path / "docorganizer.yaml").write_text(
            "name: t\nnon_archive_dir: inbox/processed\n"
        )
        cfg = load_config(tmp_path)
        assert cfg.non_archive_dir == "inbox/processed"


class TestBuildProposal:
    def test_routes_to_non_archive_dir(self, na_config, tmp_path):
        path = tmp_path / "Privatbrief Form B DE.docx"
        p = _build_proposal(
            path, _data(non_archive_reason="Letter template, not a partner doc"),
            na_config,
        )
        assert p.non_archive_reason == "Letter template, not a partner doc"
        assert p.target_folder == "inbox/processed"
        # Original filename preserved (no synthesis)
        assert p.filename == "Privatbrief Form B DE.docx"

    def test_no_op_when_config_unset(self, tmp_path):
        cfg = ArchiveConfig(
            name="t", root_folder=None, people={}, countries=[],
            tags={}, mandatory_tags=[], prompt_context="",
        )
        path = tmp_path / "x.pdf"
        p = _build_proposal(
            path, _data(non_archive_reason="should be ignored"), cfg,
        )
        # Reason captured but routing not changed (no destination configured)
        assert p.non_archive_reason == "should be ignored"
        assert p.target_folder == "Drafts"
        assert p.filename_override is None

    def test_empty_string_treated_as_none(self, na_config, tmp_path):
        path = tmp_path / "x.pdf"
        p = _build_proposal(path, _data(non_archive_reason="   "), na_config)
        assert p.non_archive_reason is None


class TestThreeDocumentRule:
    def test_skips_non_archive_proposals(self, na_ctx):
        # A proposal targeting a brand-new folder would normally be demoted to
        # Unsorted, but non-archive proposals are exempt.
        p = _build_proposal(
            na_ctx.inbox / "x.pdf",
            _data(non_archive_reason="template", folder_topic="WhateverFolder"),
            na_ctx.config,
        )
        original_target = p.target_folder
        apply_three_document_rule([p], na_ctx)
        assert p.target_folder == original_target == "inbox/processed"


class TestBusinessRouting:
    def test_skips_non_archive_proposals(self):
        cfg = ArchiveConfig(
            name="t", root_folder=None, people={}, countries=[],
            tags={}, mandatory_tags=[], prompt_context="",
            non_archive_dir="inbox/processed",
            business_routing=[
                BusinessRoutingRule(
                    name="rule", match_strings=["MATCH"],
                    target_folder="Routed/Folder",
                ),
            ],
        )
        p = _build_proposal(
            Path("/tmp/x.pdf"),
            _data(non_archive_reason="template"),
            cfg,
        )
        applied = apply_business_routing(p, "this contains MATCH text", cfg)
        assert applied is None
        assert p.target_folder == "inbox/processed"


class TestValidator:
    def test_validator_skips_non_archive(self, na_config):
        # A proposal with placeholder/empty fields should not produce issues
        # when it's a non-archive disposal.
        p = Proposal(
            original_path=Path("/tmp/x.pdf"),
            sender="", topic="", person="", date="",
            country="", folder_topic="", target_folder="inbox/processed",
            tags=[], confidence="Medium", notes="",
            non_archive_reason="template",
        )
        issues = validate_proposals([p], registry={}, config=na_config)
        assert issues == {}


class TestExecute:
    def test_moves_to_non_archive_dir_no_tags(self, na_ctx, monkeypatch):
        # Capture apply_tags calls — should not be invoked for non-archive
        called = []
        from docorganizer import cli as cli_mod
        monkeypatch.setattr(
            cli_mod, "apply_tags", lambda path, tags: called.append((path, tags)),
        )

        src = na_ctx.inbox / "Some Template.docx"
        src.write_text("template content")

        p = _build_proposal(
            src,
            _data(non_archive_reason="working draft"),
            na_ctx.config,
        )
        p.status = "approved"

        executed = execute_all([p], na_ctx)
        assert len(executed) == 1
        target = na_ctx.root / "inbox/processed/Some Template.docx"
        assert target.exists()
        assert not src.exists()
        # No tags written for non-archive disposals
        assert called == []


class TestSerialization:
    def test_round_trip(self, tmp_path):
        p = Proposal(
            original_path=tmp_path / "x.pdf",
            sender="A", topic="T", person="", date="2026-01-01",
            country="", folder_topic="A", target_folder="inbox/processed",
            tags=[], confidence="High", notes="",
            non_archive_reason="template",
            filename_override="x.pdf",
        )
        d = p.to_dict()
        d.pop("proposed_filename", None)
        d["original_path"] = str(p.original_path)
        restored = Proposal.from_dict(d)
        assert restored.non_archive_reason == "template"
        assert restored.filename == "x.pdf"
