"""Tests for the per-archive Python plugin system."""

import textwrap
from pathlib import Path

import pytest

from docorganizer.cli import Proposal, propose_all
from docorganizer.config import ArchiveConfig, ArchiveContext
from docorganizer.plugin import Plugin, PLUGIN_FILENAME, load_plugin


@pytest.fixture
def archive_root(tmp_path):
    (tmp_path / "inbox").mkdir()
    return tmp_path


@pytest.fixture
def config():
    return ArchiveConfig(
        name="t", root_folder=None, people={}, countries=[],
        tags={}, mandatory_tags=[], prompt_context="",
    )


def _write_plugin(root: Path, body: str) -> None:
    (root / PLUGIN_FILENAME).write_text(textwrap.dedent(body))


def _make_proposal(path: Path, sender="Acme") -> Proposal:
    return Proposal(
        original_path=path,
        sender=sender, topic="Invoice", person="", date="2026-04-28",
        country="", folder_topic="Acme",
        target_folder="Acme/2026-04",
        tags=[], confidence="High", notes="",
    )


# ── Loader ─────────────────────────────────────────────────────────────────


class TestLoadPlugin:
    def test_no_file_returns_empty(self, archive_root):
        p = load_plugin(archive_root)
        assert isinstance(p, Plugin)
        assert p.post_propose is None
        assert p.post_batch is None

    def test_post_propose_loaded(self, archive_root):
        _write_plugin(archive_root, """
            def post_propose(proposal, text):
                return proposal
        """)
        p = load_plugin(archive_root)
        assert callable(p.post_propose)
        assert p.post_batch is None

    def test_post_batch_loaded(self, archive_root):
        _write_plugin(archive_root, """
            def post_batch(proposals):
                return proposals
        """)
        p = load_plugin(archive_root)
        assert p.post_propose is None
        assert callable(p.post_batch)

    def test_both_loaded(self, archive_root):
        _write_plugin(archive_root, """
            def post_propose(proposal, text):
                return proposal

            def post_batch(proposals):
                return proposals
        """)
        p = load_plugin(archive_root)
        assert callable(p.post_propose)
        assert callable(p.post_batch)

    def test_broken_plugin_does_not_raise(self, archive_root, capsys):
        _write_plugin(archive_root, "this is not valid python {{{")
        p = load_plugin(archive_root)
        assert p.post_propose is None
        assert p.post_batch is None
        err = capsys.readouterr().err
        assert "failed to load" in err.lower()


# ── Lazy loading via ArchiveContext.plugin ─────────────────────────────────


class TestArchiveContextPlugin:
    def test_no_plugin_returns_empty(self, archive_root, config):
        ctx = ArchiveContext(config=config, root=archive_root)
        assert ctx.plugin.post_propose is None
        assert ctx.plugin.post_batch is None

    def test_cached_after_first_access(self, archive_root, config):
        _write_plugin(archive_root, """
            def post_propose(proposal, text):
                return proposal
        """)
        ctx = ArchiveContext(config=config, root=archive_root)
        first = ctx.plugin
        second = ctx.plugin
        assert first is second  # cached_property → same object


# ── Pipeline integration ───────────────────────────────────────────────────


class TestPipelineIntegration:
    def test_post_propose_called_per_proposal(self, archive_root, config, monkeypatch):
        _write_plugin(archive_root, """
            def post_propose(proposal, text):
                proposal.notes = "rewritten by plugin"
                return proposal
        """)
        ctx = ArchiveContext(config=config, root=archive_root)

        # Bypass actual LLM call
        from docorganizer import cli as cli_mod
        monkeypatch.setattr(cli_mod, "_call_claude", lambda text, structure, cfg: {
            "date": "2026-04-28", "sender": "Acme", "topic": "Invoice",
            "country": "none", "folder_topic": "Acme",
            "tags": [], "confidence": "High", "notes": "original",
        })
        monkeypatch.setattr(cli_mod, "get_existing_structure", lambda ctx: {})

        from docorganizer.cli import Extraction
        ext = Extraction(path=archive_root / "inbox/x.pdf", text="some content")
        proposals = propose_all([ext], ctx)
        assert len(proposals) == 1
        assert proposals[0].notes == "rewritten by plugin"

    def test_post_batch_called_once(self, archive_root, config, monkeypatch):
        _write_plugin(archive_root, """
            def post_batch(proposals):
                for p in proposals:
                    p.notes = "batch-tagged"
                return proposals
        """)
        ctx = ArchiveContext(config=config, root=archive_root)

        from docorganizer import cli as cli_mod
        monkeypatch.setattr(cli_mod, "_call_claude", lambda text, structure, cfg: {
            "date": "2026-04-28", "sender": "Acme", "topic": "Invoice",
            "country": "none", "folder_topic": "Acme",
            "tags": [], "confidence": "High", "notes": "x",
        })
        monkeypatch.setattr(cli_mod, "get_existing_structure", lambda ctx: {})

        from docorganizer.cli import Extraction
        exts = [
            Extraction(path=archive_root / f"inbox/x{i}.pdf", text="data")
            for i in range(3)
        ]
        proposals = propose_all(exts, ctx)
        assert all(p.notes == "batch-tagged" for p in proposals)

    def test_post_propose_exception_does_not_block(self, archive_root, config, monkeypatch, capsys):
        _write_plugin(archive_root, """
            def post_propose(proposal, text):
                raise RuntimeError("boom")
        """)
        ctx = ArchiveContext(config=config, root=archive_root)

        from docorganizer import cli as cli_mod
        monkeypatch.setattr(cli_mod, "_call_claude", lambda text, structure, cfg: {
            "date": "2026-04-28", "sender": "Acme", "topic": "Invoice",
            "country": "none", "folder_topic": "Acme",
            "tags": [], "confidence": "High", "notes": "x",
        })
        monkeypatch.setattr(cli_mod, "get_existing_structure", lambda ctx: {})

        from docorganizer.cli import Extraction
        ext = Extraction(path=archive_root / "inbox/x.pdf", text="data")
        proposals = propose_all([ext], ctx)
        assert len(proposals) == 1
        assert "boom" in capsys.readouterr().err

    def test_post_batch_exception_does_not_block(self, archive_root, config, monkeypatch, capsys):
        _write_plugin(archive_root, """
            def post_batch(proposals):
                raise RuntimeError("batch boom")
        """)
        ctx = ArchiveContext(config=config, root=archive_root)

        from docorganizer import cli as cli_mod
        monkeypatch.setattr(cli_mod, "_call_claude", lambda text, structure, cfg: {
            "date": "2026-04-28", "sender": "Acme", "topic": "Invoice",
            "country": "none", "folder_topic": "Acme",
            "tags": [], "confidence": "High", "notes": "x",
        })
        monkeypatch.setattr(cli_mod, "get_existing_structure", lambda ctx: {})

        from docorganizer.cli import Extraction
        ext = Extraction(path=archive_root / "inbox/x.pdf", text="data")
        proposals = propose_all([ext], ctx)
        assert len(proposals) == 1
        assert "batch boom" in capsys.readouterr().err
