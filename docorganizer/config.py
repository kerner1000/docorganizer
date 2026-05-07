"""Archive configuration loaded from docorganizer.yaml."""

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import yaml

from docorganizer.charset import FilenameCharset


CONFIG_FILENAME = "docorganizer.yaml"


def date_to_subfolder(date_str: str) -> str:
    """Derive a YYYY-MM subfolder name from a document date string.

    YYYY-MM-DD -> YYYY-MM
    YYYY-MM    -> YYYY-MM
    YYYY       -> YYYY
    Undated    -> Undated
    """
    if date_str == "Undated":
        return "Undated"
    parts = date_str.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return parts[0]


@dataclass
class BusinessRoutingRule:
    """Deterministic post-classification override for business entity routing.

    If the document's extracted text contains any of ``match_strings`` (case-
    insensitive), the matched proposal's ``target_folder``, ``tags``, and
    optionally ``person`` are overridden. The LLM still provides sender, topic,
    and date — this rule only redirects *where* and *how* the document is
    filed.
    """

    name: str
    match_strings: list[str]
    target_folder: str
    append_tags: list[str] = field(default_factory=list)
    override_person: str | None = None
    override_sender: str | None = None


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
    date_subfolders: bool = False

    # Optional destination for documents the classifier judges as non-archivable
    # (e.g. blank scans, working drafts, templates accidentally dropped in inbox).
    # Path is relative to archive root. When None, the option is not offered to
    # the classifier — every readable document must be filed.
    non_archive_dir: str | None = None

    # Translation: auto-translate documents in specified source languages via DeepL
    translation_target: str = ""  # e.g. "EN-US" — empty means translation disabled
    translation_sources: list[str] = field(default_factory=list)  # e.g. ["LV", "RU"]

    # Deterministic routing overrides applied after LLM classification
    business_routing: list[BusinessRoutingRule] = field(default_factory=list)

    # Charset enforcement for paths (filenames + folder segments). See ADR-0009.
    filename_charset: FilenameCharset = field(default_factory=FilenameCharset.default)

    @property
    def use_person(self) -> bool:
        """Whether person tracking is enabled (people section is configured)."""
        return bool(self.people)

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

    @property
    def translation_enabled(self) -> bool:
        """Whether automatic translation is configured."""
        return bool(self.translation_target and self.translation_sources)


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

    @cached_property
    def plugin(self):
        """Lazy-load the per-archive ``docorganizer_plugin.py`` if present.

        Returns an empty ``Plugin`` (all hooks None) when no plugin file
        exists in the archive root. Import is lazy so tests that never
        touch a plugin pay no cost.
        """
        from docorganizer.plugin import load_plugin
        return load_plugin(self.root)


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

    translation = raw.get("translation", {})

    business_routing = [
        BusinessRoutingRule(
            name=r["name"],
            match_strings=r["match_strings"],
            target_folder=r["target_folder"],
            append_tags=r.get("append_tags", []),
            override_person=r.get("override_person"),
            override_sender=r.get("override_sender"),
        )
        for r in raw.get("business_routing", [])
    ]

    cs = raw.get("filename_charset")
    if cs is None:
        filename_charset = FilenameCharset.default()
    else:
        filename_charset = FilenameCharset(
            transliterate=dict(cs.get("transliterate", {})),
            strip_remaining_diacritics=cs.get("strip_remaining_diacritics", True),
            enforce_ascii=cs.get("enforce_ascii", True),
        )

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
        date_subfolders=raw.get("date_subfolders", False),
        translation_target=translation.get("target_language", ""),
        translation_sources=translation.get("source_languages", []),
        business_routing=business_routing,
        non_archive_dir=raw.get("non_archive_dir"),
        filename_charset=filename_charset,
    )
