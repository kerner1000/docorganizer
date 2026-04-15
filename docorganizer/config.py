"""Archive configuration loaded from docorganizer.yaml."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


CONFIG_FILENAME = "docorganizer.yaml"


@dataclass
class ArchiveConfig:
    """Domain-specific configuration for a document archive.

    Loaded from docorganizer.yaml in the archive root directory.
    """

    name: str
    root_folder: str | None  # top-level folder for filed documents, or None for flat layout

    people: dict[str, list[str]]  # first_name -> [full name variants]
    countries: list[str]
    tags: dict[str, str]  # tag_name -> description (for classification prompt)
    mandatory_tags: list[str]  # applied to every filed document
    prompt_context: str  # injected into classification prompt

    inbox_dir: str = "inbox"
    archive_dir: str = "_archive"
    intake_log: str = "intake-log.md"
    todo_dir: str = "ToDo"

    @property
    def root_folder_prefix(self) -> str:
        """Prefix for building target_folder path strings.

        Returns 'root_folder/' when set, '' when flat layout.
        """
        return f"{self.root_folder}/" if self.root_folder else ""

    @property
    def controlled_tags(self) -> frozenset[str]:
        """Tag names as a frozenset (the controlled vocabulary)."""
        return frozenset(self.tags.keys())

    @property
    def countries_set(self) -> frozenset[str]:
        """Countries as a frozenset for efficient lookup."""
        return frozenset(self.countries)


@dataclass
class ArchiveContext:
    """Runtime context: config + resolved paths for a specific archive."""

    config: ArchiveConfig
    root: Path

    @property
    def inbox(self) -> Path:
        return self.root / self.config.inbox_dir

    @property
    def root_folder(self) -> Path:
        """Filing root. Same as archive root when root_folder is None (flat layout)."""
        if self.config.root_folder:
            return self.root / self.config.root_folder
        return self.root

    @property
    def tool_dir_names(self) -> frozenset[str]:
        """Directory names used by docorganizer (excluded from filing scans)."""
        return frozenset({
            self.config.inbox_dir,
            self.config.archive_dir,
            self.config.todo_dir,
        })

    @property
    def archive(self) -> Path:
        return self.root / self.config.archive_dir

    @property
    def intake_log(self) -> Path:
        return self.root / self.config.intake_log

    @property
    def todo(self) -> Path:
        return self.root / self.config.todo_dir

    @property
    def proposals_file(self) -> Path:
        return self.root / "proposals.json"

    @property
    def refactor_file(self) -> Path:
        return self.root / "refactor.json"


def load_config(archive_root: Path) -> ArchiveConfig:
    """Load archive configuration from docorganizer.yaml.

    Raises FileNotFoundError if the config file is missing.
    """
    config_path = archive_root / CONFIG_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(
            f"No {CONFIG_FILENAME} found in {archive_root}. "
            f"Create one to configure this archive."
        )

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    return ArchiveConfig(
        name=raw["name"],
        root_folder=raw.get("root_folder"),
        people=raw.get("people", {}),
        countries=raw.get("countries", []),
        tags=raw.get("tags", {}),
        mandatory_tags=raw.get("mandatory_tags", []),
        prompt_context=raw.get("prompt_context", ""),
        inbox_dir=raw.get("inbox_dir", "inbox"),
        archive_dir=raw.get("archive_dir", "_archive"),
        intake_log=raw.get("intake_log", "intake-log.md"),
        todo_dir=raw.get("todo_dir", "ToDo"),
    )
