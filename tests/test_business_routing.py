"""Business routing rule overrides."""

from pathlib import Path

from docorganizer.cli import Proposal, apply_business_routing
from docorganizer.config import ArchiveConfig, BusinessRoutingRule


def _make_config(rules: list[BusinessRoutingRule]) -> ArchiveConfig:
    return ArchiveConfig(
        name="Test",
        root_folder="Family",
        people={"Alexander": ["Alexander Kerner"]},
        countries=["Switzerland"],
        tags={"Tax": "", "ToDo": ""},
        mandatory_tags=[],
        prompt_context="",
        business_routing=rules,
    )


def _make_proposal(tmp_path: Path) -> Proposal:
    src = tmp_path / "Invoice.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    return Proposal(
        original_path=src,
        sender="Anthropic, PBC",
        topic="Invoice",
        person="Unknown",
        date="2026-03-31",
        country="Switzerland",
        folder_topic="Invoices",
        target_folder="Family/Switzerland/Invoices",
        tags=["Tax"],
        confidence="High",
        notes="",
    )


def test_matching_rule_overrides_folder_and_tags(tmp_path):
    rule = BusinessRoutingRule(
        name="Stratech business",
        match_strings=["Stratech GmbH"],
        target_folder="ToDo/Business",
        append_tags=["ToDo"],
        override_person="Alexander",
    )
    config = _make_config([rule])
    proposal = _make_proposal(tmp_path)
    text = "Bill to: Stratech GmbH, Appenzell, Switzerland."

    applied = apply_business_routing(proposal, text, config)

    assert applied == "Stratech business"
    assert proposal.target_folder == "ToDo/Business"
    assert proposal.folder_topic == "Business"
    assert proposal.tags == ["Tax", "ToDo"]
    assert proposal.person == "Alexander"


def test_no_match_leaves_proposal_untouched(tmp_path):
    rule = BusinessRoutingRule(
        name="Stratech business",
        match_strings=["Stratech GmbH"],
        target_folder="ToDo/Business",
    )
    config = _make_config([rule])
    proposal = _make_proposal(tmp_path)
    original = (
        proposal.target_folder,
        proposal.folder_topic,
        list(proposal.tags),
        proposal.person,
    )
    text = "Private consumer invoice for Alexander Kerner."

    applied = apply_business_routing(proposal, text, config)

    assert applied is None
    assert (
        proposal.target_folder,
        proposal.folder_topic,
        proposal.tags,
        proposal.person,
    ) == original


def test_match_is_case_insensitive(tmp_path):
    rule = BusinessRoutingRule(
        name="Stratech",
        match_strings=["Stratech GmbH"],
        target_folder="ToDo/Business",
    )
    config = _make_config([rule])
    proposal = _make_proposal(tmp_path)

    assert apply_business_routing(proposal, "billed to stratech gmbh", config) == "Stratech"


def test_tag_append_is_idempotent(tmp_path):
    rule = BusinessRoutingRule(
        name="Stratech",
        match_strings=["Stratech"],
        target_folder="ToDo/Business",
        append_tags=["Tax", "ToDo"],
    )
    config = _make_config([rule])
    proposal = _make_proposal(tmp_path)  # already has ["Tax"]

    apply_business_routing(proposal, "Stratech GmbH", config)

    assert proposal.tags == ["Tax", "ToDo"]


def test_empty_business_routing_is_noop(tmp_path):
    config = _make_config([])
    proposal = _make_proposal(tmp_path)

    assert apply_business_routing(proposal, "anything", config) is None
