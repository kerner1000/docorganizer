"""Shared test fixtures."""

import pytest
from pathlib import Path

from docorganizer.config import ArchiveConfig, ArchiveContext


@pytest.fixture
def test_config():
    """An ArchiveConfig matching the original family archive for test compatibility."""
    return ArchiveConfig(
        name="Test Archive",
        root_folder="Family",
        people={
            "Alexander": [
                "Alexander Kerner", "Philip Alexander Kerner",
                "Dr. Alexander Kerner", "Philip Kerner",
                "Kerner, Alexander", "Kerner, Philip Alexander",
                "Kerner Alexander",
            ],
            "Kristina": [
                "Kristina Fateeva", "Fateeva Kristina",
                "Fateeva-Kerner", "Kristina Fateeva-Kerner",
                "Fateeva, Kristina", "Kristina Vladislavovna Fateeva",
            ],
            "Karolina": [
                "Karolina Kerner", "Kerner Karolina", "Kerner, Karolina",
            ],
        },
        countries=["Germany", "Latvia", "Switzerland"],
        tags={
            "Tax": "Tax-relevant documents",
            "Insurance": "Policies, claims, correspondence",
            "Legal": "Contracts and official correspondence",
            "Expiring": "Documents requiring active monitoring",
        },
        mandatory_tags=[],
        prompt_context="Test archive.",
    )


@pytest.fixture
def test_ctx(tmp_path, test_config):
    """An ArchiveContext with temp directories."""
    (tmp_path / "inbox").mkdir()
    (tmp_path / "Family").mkdir()
    return ArchiveContext(config=test_config, root=tmp_path)


@pytest.fixture
def flat_config():
    """An ArchiveConfig with no countries — flat folder structure."""
    return ArchiveConfig(
        name="Flat Archive",
        root_folder="Documents",
        people={"Alice": ["Alice Smith"]},
        countries=[],
        tags={
            "Accounting": "Financial records",
            "Contract": "Signed agreements",
        },
        mandatory_tags=[],
        prompt_context="A flat archive without country grouping.",
    )


@pytest.fixture
def flat_ctx(tmp_path, flat_config):
    """An ArchiveContext with no countries."""
    (tmp_path / "inbox").mkdir()
    (tmp_path / "Documents").mkdir()
    return ArchiveContext(config=flat_config, root=tmp_path)


@pytest.fixture
def no_root_config():
    """An ArchiveConfig with no root_folder — filing root equals archive root."""
    return ArchiveConfig(
        name="No Root Archive",
        root_folder=None,
        people={"Alex": ["Alexander Kerner"]},
        countries=["Germany"],
        tags={
            "Tax": "Tax-relevant documents",
            "Contract": "Signed agreements",
        },
        mandatory_tags=[],
        prompt_context="Flat layout archive.",
    )


@pytest.fixture
def no_root_ctx(tmp_path, no_root_config):
    """An ArchiveContext where root_folder == root (flat layout)."""
    (tmp_path / "inbox").mkdir()
    (tmp_path / "Germany" / "Invoices").mkdir(parents=True)
    return ArchiveContext(config=no_root_config, root=tmp_path)
