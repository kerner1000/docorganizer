"""Tests for the config module."""

from pathlib import Path

import pytest
import yaml

from docorganizer.config import ArchiveConfig, ArchiveContext, load_config


class TestArchiveConfig:
    def test_controlled_tags_from_dict_keys(self):
        config = ArchiveConfig(
            name="Test", root_folder="Docs",
            people={}, countries=[], mandatory_tags=[],
            tags={"Tax": "desc", "Legal": "desc"},
            prompt_context="",
        )
        assert config.controlled_tags == frozenset({"Tax", "Legal"})

    def test_countries_set(self):
        config = ArchiveConfig(
            name="Test", root_folder="Docs",
            people={}, countries=["Germany", "Latvia"], mandatory_tags=[],
            tags={}, prompt_context="",
        )
        assert config.countries_set == frozenset({"Germany", "Latvia"})

    def test_defaults(self):
        config = ArchiveConfig(
            name="Test", root_folder="Docs",
            people={}, countries=[], mandatory_tags=[],
            tags={}, prompt_context="",
        )
        assert config.inbox_dir == "inbox"
        assert config.archive_dir == "_archive"
        assert config.intake_log == "intake-log.md"
        assert config.todo_dir == "ToDo"


class TestArchiveContext:
    def test_paths_resolve_from_root(self, tmp_path):
        config = ArchiveConfig(
            name="Test", root_folder="Family",
            people={}, countries=[], mandatory_tags=[],
            tags={}, prompt_context="",
        )
        ctx = ArchiveContext(config=config, root=tmp_path)

        assert ctx.inbox == tmp_path / "inbox"
        assert ctx.root_folder == tmp_path / "Family"
        assert ctx.archive == tmp_path / "_archive"
        assert ctx.intake_log == tmp_path / "intake-log.md"
        assert ctx.todo == tmp_path / "ToDo"
        assert ctx.proposals_file == tmp_path / "proposals.json"
        assert ctx.refactor_file == tmp_path / "refactor.json"

    def test_custom_dir_names(self, tmp_path):
        config = ArchiveConfig(
            name="Test", root_folder="Documents",
            people={}, countries=[], mandatory_tags=[],
            tags={}, prompt_context="",
            inbox_dir="incoming", archive_dir="old",
            intake_log="log.md", todo_dir="Review",
        )
        ctx = ArchiveContext(config=config, root=tmp_path)

        assert ctx.inbox == tmp_path / "incoming"
        assert ctx.root_folder == tmp_path / "Documents"
        assert ctx.archive == tmp_path / "old"
        assert ctx.intake_log == tmp_path / "log.md"
        assert ctx.todo == tmp_path / "Review"


class TestLoadConfig:
    def test_loads_valid_yaml(self, tmp_path):
        config_data = {
            "name": "My Archive",
            "root_folder": "Docs",
            "people": {"Alice": ["Alice Smith"]},
            "countries": ["Germany"],
            "tags": {"Tax": "Tax stuff"},
            "mandatory_tags": ["processed"],
            "prompt_context": "A test archive.",
        }
        (tmp_path / "docorganizer.yaml").write_text(yaml.dump(config_data))

        config = load_config(tmp_path)

        assert config.name == "My Archive"
        assert config.root_folder == "Docs"
        assert config.people == {"Alice": ["Alice Smith"]}
        assert config.countries == ["Germany"]
        assert config.tags == {"Tax": "Tax stuff"}
        assert config.mandatory_tags == ["processed"]
        assert config.prompt_context == "A test archive."

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="docorganizer.yaml"):
            load_config(tmp_path)

    def test_defaults_for_optional_fields(self, tmp_path):
        config_data = {"name": "Minimal", "root_folder": "Files"}
        (tmp_path / "docorganizer.yaml").write_text(yaml.dump(config_data))

        config = load_config(tmp_path)

        assert config.people == {}
        assert config.countries == []
        assert config.tags == {}
        assert config.mandatory_tags == []
        assert config.prompt_context == ""
        assert config.inbox_dir == "inbox"
