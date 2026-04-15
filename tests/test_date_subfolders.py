"""Tests for the date_subfolders feature."""

from collections import Counter
from pathlib import Path

import pytest
import yaml

from docorganizer.config import ArchiveConfig, ArchiveContext, date_to_subfolder, load_config
from docorganizer.cli import _build_proposal, apply_three_document_rule


# ── date_to_subfolder ───────────────────────────────────────────────────────


class TestDateToSubfolder:
    def test_full_date(self):
        assert date_to_subfolder("2026-01-15") == "2026-01"

    def test_month_date(self):
        assert date_to_subfolder("2026-01") == "2026-01"

    def test_year_only(self):
        assert date_to_subfolder("2026") == "2026"

    def test_undated(self):
        assert date_to_subfolder("Undated") == "Undated"


# ── config loading ──────────────────────────────────────────────────────────


class TestDateSubfoldersConfig:
    def test_default_is_false(self):
        config = ArchiveConfig(
            name="Test", root_folder=None,
            people={}, countries=[], mandatory_tags=[],
            tags={}, prompt_context="",
        )
        assert config.date_subfolders is False

    def test_enabled(self):
        config = ArchiveConfig(
            name="Test", root_folder=None,
            people={}, countries=[], mandatory_tags=[],
            tags={}, prompt_context="",
            date_subfolders=True,
        )
        assert config.date_subfolders is True

    def test_loads_from_yaml(self, tmp_path):
        config_data = {"name": "Test", "date_subfolders": True}
        (tmp_path / "docorganizer.yaml").write_text(yaml.dump(config_data))

        config = load_config(tmp_path)
        assert config.date_subfolders is True

    def test_defaults_false_in_yaml(self, tmp_path):
        config_data = {"name": "Test"}
        (tmp_path / "docorganizer.yaml").write_text(yaml.dump(config_data))

        config = load_config(tmp_path)
        assert config.date_subfolders is False


# ── _build_proposal ─────────────────────────────────────────────────────────


def _make_config(*, date_subfolders=False, root_folder=None, countries=None):
    return ArchiveConfig(
        name="Test", root_folder=root_folder,
        people={}, countries=countries or [], mandatory_tags=[],
        tags={"Banking": "desc", "Tax": "desc"},
        prompt_context="",
        date_subfolders=date_subfolders,
    )


def _make_claude_data(**overrides):
    data = {
        "sender": "UBS Switzerland AG",
        "topic": "Bank Statement",
        "date": "2026-01-31",
        "country": "none",
        "folder_topic": "UBS",
        "tags": ["Banking"],
        "confidence": "High",
        "notes": "",
    }
    data.update(overrides)
    return data


class TestBuildProposalDateSubfolders:
    def test_disabled_flat(self):
        config = _make_config(date_subfolders=False)
        data = _make_claude_data()
        p = _build_proposal(Path("test.pdf"), data, config)
        assert p.target_folder == "UBS"

    def test_enabled_flat(self):
        config = _make_config(date_subfolders=True)
        data = _make_claude_data()
        p = _build_proposal(Path("test.pdf"), data, config)
        assert p.target_folder == "UBS/2026-01"

    def test_enabled_preserves_folder_topic(self):
        config = _make_config(date_subfolders=True)
        data = _make_claude_data()
        p = _build_proposal(Path("test.pdf"), data, config)
        assert p.folder_topic == "UBS"

    def test_enabled_with_country(self):
        config = _make_config(date_subfolders=True, countries=["Switzerland"])
        data = _make_claude_data(country="Switzerland")
        p = _build_proposal(Path("test.pdf"), data, config)
        assert p.target_folder == "Switzerland/UBS/2026-01"

    def test_enabled_with_root_folder(self):
        config = _make_config(date_subfolders=True, root_folder="Documents")
        data = _make_claude_data()
        p = _build_proposal(Path("test.pdf"), data, config)
        assert p.target_folder == "Documents/UBS/2026-01"

    def test_enabled_undated(self):
        config = _make_config(date_subfolders=True)
        data = _make_claude_data(date="Undated")
        p = _build_proposal(Path("test.pdf"), data, config)
        assert p.target_folder == "UBS/Undated"

    def test_enabled_year_only(self):
        config = _make_config(date_subfolders=True)
        data = _make_claude_data(date="2026")
        p = _build_proposal(Path("test.pdf"), data, config)
        assert p.target_folder == "UBS/2026"


# ── three-document rule ─────────────────────────────────────────────────────


class TestThreeDocRuleWithDateSubfolders:
    def test_demotion_includes_date_subfolder(self, tmp_path):
        config = _make_config(date_subfolders=True)
        ctx = ArchiveContext(config=config, root=tmp_path)
        (tmp_path / "inbox").mkdir()

        proposals = [
            _build_proposal(Path("a.pdf"), _make_claude_data(), config),
        ]
        apply_three_document_rule(proposals, ctx)

        # Single doc demoted to Unsorted, with date subfolder
        assert proposals[0].target_folder == "Unsorted/2026-01"
        assert proposals[0].folder_topic == "Unsorted"

    def test_demotion_without_date_subfolders(self, tmp_path):
        config = _make_config(date_subfolders=False)
        ctx = ArchiveContext(config=config, root=tmp_path)
        (tmp_path / "inbox").mkdir()

        proposals = [
            _build_proposal(Path("a.pdf"), _make_claude_data(), config),
        ]
        apply_three_document_rule(proposals, ctx)

        # Without date_subfolders, just "Unsorted"
        assert proposals[0].target_folder == "Unsorted"

    def test_existing_partner_folder_keeps_assignment(self, tmp_path):
        config = _make_config(date_subfolders=True)
        ctx = ArchiveContext(config=config, root=tmp_path)
        (tmp_path / "inbox").mkdir()
        (tmp_path / "UBS").mkdir()  # partner folder exists

        proposals = [
            _build_proposal(Path("a.pdf"), _make_claude_data(), config),
        ]
        apply_three_document_rule(proposals, ctx)

        # Folder exists at partner level — assignment kept
        assert proposals[0].target_folder == "UBS/2026-01"
        assert proposals[0].folder_topic == "UBS"
