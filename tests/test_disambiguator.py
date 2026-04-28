"""Tests for the optional disambiguator field on Proposal."""

from pathlib import Path

from docorganizer.cli import Proposal, _build_proposal


def _make_proposal(**overrides) -> Proposal:
    defaults = dict(
        original_path=Path("inbox/x.pdf"),
        sender="Acme Corp",
        topic="Invoice",
        person="",
        date="2026-04-01",
        country="",
        folder_topic="Acme Corp",
        target_folder="Acme Corp/2026-04",
        tags=[],
        confidence="High",
        notes="",
    )
    defaults.update(overrides)
    return Proposal(**defaults)


class TestSynthesizedFilename:
    def test_omitted_when_none(self):
        p = _make_proposal()
        assert p.synthesized_filename == "2026-04-01 - Acme Corp - Invoice.pdf"

    def test_appended_to_topic_with_space(self):
        p = _make_proposal(disambiguator="USD 11.35")
        assert p.synthesized_filename == "2026-04-01 - Acme Corp - Invoice USD 11.35.pdf"

    def test_appended_before_person(self):
        p = _make_proposal(person="Alice", disambiguator="USD 11.35")
        assert (
            p.synthesized_filename
            == "2026-04-01 - Acme Corp - Invoice USD 11.35 - Alice.pdf"
        )

    def test_filename_override_bypasses_disambiguator(self):
        p = _make_proposal(disambiguator="USD 11.35", filename_override="custom.pdf")
        assert p.filename == "custom.pdf"

    def test_empty_string_treated_as_unset_via_build_proposal(self):
        # Simulates the LLM returning "" instead of null
        from docorganizer.config import ArchiveConfig

        config = ArchiveConfig(
            name="t", root_folder=None, people={}, countries=[],
            tags={}, mandatory_tags=[], prompt_context="",
        )
        path = Path("/tmp/foo.pdf")
        p = _build_proposal(
            path,
            {
                "date": "2026-04-01", "sender": "A", "topic": "Invoice",
                "country": "none", "folder_topic": "A",
                "tags": [], "confidence": "High", "notes": "",
                "disambiguator": "   ",
            },
            config,
        )
        assert p.disambiguator is None


class TestSerialization:
    def test_to_dict_omits_when_none(self):
        p = _make_proposal()
        d = p.to_dict()
        assert "disambiguator" not in d

    def test_to_dict_includes_when_set(self):
        p = _make_proposal(disambiguator="USD 11.35")
        d = p.to_dict()
        assert d["disambiguator"] == "USD 11.35"

    def test_round_trip_preserves_value(self):
        p = _make_proposal(disambiguator="Q1 2026")
        # to_dict includes proposed_filename — drop it for from_dict
        d = p.to_dict()
        d.pop("proposed_filename", None)
        d["original_path"] = str(p.original_path)
        restored = Proposal.from_dict(d)
        assert restored.disambiguator == "Q1 2026"
        assert restored.synthesized_filename == p.synthesized_filename

    def test_round_trip_legacy_proposal_without_field(self):
        # Existing proposals.json files have no "disambiguator" key
        d = {
            "original_path": "inbox/legacy.pdf",
            "sender": "Acme", "topic": "Invoice", "person": "",
            "date": "2026-01-01", "country": "", "folder_topic": "Acme",
            "target_folder": "Acme/2026-01",
            "tags": [], "confidence": "High", "notes": "",
        }
        p = Proposal.from_dict(d)
        assert p.disambiguator is None
