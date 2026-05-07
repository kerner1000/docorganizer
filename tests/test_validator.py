"""Tests for the validator module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from docorganizer.config import ArchiveConfig
from docorganizer.validator import (
    Issue,
    SenderEntry,
    _check_batch_sender_drift,
    _check_batch_topic_drift,
    _check_date_format,
    _check_filename_separators,
    _check_path_unsafe_chars,
    _check_person,
    _check_sender_consistency,
    _check_tags_valid,
    _normalize_document_type_topic,
    _parse_filename,
    build_sender_registry,
    sanitize_field,
    validate_proposals,
    format_validation_report,
)
from docorganizer.cli import Proposal


def _make_proposal(**overrides) -> Proposal:
    defaults = dict(
        original_path=Path("/inbox/test.pdf"),
        sender="Test Sender",
        topic="Test Topic",
        person="Alexander",
        date="2024-01-15",
        country="Germany",
        folder_topic="Unsorted",
        target_folder="Family/Germany/Unsorted",
        tags=["Tax"],
        confidence="High",
        notes="test",
    )
    defaults.update(overrides)
    return Proposal(**defaults)


# ── _parse_filename ──────────────────────────────────────────────────────────


class TestParseFilename:
    def test_valid_four_field_name(self):
        result = _parse_filename("2024-01-15 - Finanzamt HD - Steuerbescheid - Alex.pdf")
        assert result == {
            "date": "2024-01-15",
            "sender": "Finanzamt HD",
            "topic": "Steuerbescheid",
            "person": "Alex",
        }

    def test_valid_three_field_name(self):
        result = _parse_filename("2026-01-27 - UBS Switzerland AG - Debit Card Charge.pdf")
        assert result == {
            "date": "2026-01-27",
            "sender": "UBS Switzerland AG",
            "topic": "Debit Card Charge",
        }
        assert "person" not in result

    def test_returns_none_for_wrong_field_count(self):
        assert _parse_filename("no-dashes-here.pdf") is None
        assert _parse_filename("one - two.pdf") is None
        assert _parse_filename("a - b - c - d - e.pdf") is None

    def test_handles_spaces_in_fields(self):
        result = _parse_filename("2024-01 - Sky Dream Clinic - Dental Invoice - Kristina.pdf")
        assert result["sender"] == "Sky Dream Clinic"
        assert result["topic"] == "Dental Invoice"


# ── _check_date_format ───────────────────────────────────────────────────────


class TestCheckDateFormat:
    def test_valid_full_date(self):
        p = _make_proposal(date="2024-01-15")
        assert _check_date_format(p) == []

    def test_valid_year_month(self):
        p = _make_proposal(date="2024-01")
        assert _check_date_format(p) == []

    def test_valid_year_only(self):
        p = _make_proposal(date="2024")
        assert _check_date_format(p) == []

    def test_valid_undated(self):
        p = _make_proposal(date="Undated")
        assert _check_date_format(p) == []

    def test_invalid_date(self):
        p = _make_proposal(date="15.01.2024")
        issues = _check_date_format(p)
        assert len(issues) == 1
        assert issues[0].severity == "review"
        assert issues[0].field == "date"

    def test_invalid_date_text(self):
        p = _make_proposal(date="January 2024")
        issues = _check_date_format(p)
        assert len(issues) == 1


# ── _check_person ────────────────────────────────────────────────────────────


class TestCheckPerson:
    def test_valid_first_name(self, test_config):
        p = _make_proposal(person="Alexander")
        assert _check_person(p, test_config) == []

    def test_valid_unknown(self, test_config):
        p = _make_proposal(person="Unknown")
        assert _check_person(p, test_config) == []

    def test_normalizes_full_name(self, test_config):
        p = _make_proposal(person="Philip Alexander Kerner")
        issues = _check_person(p, test_config)
        assert len(issues) == 1
        assert issues[0].severity == "fixed"
        assert p.person == "Alexander"

    def test_normalizes_reversed_name(self, test_config):
        p = _make_proposal(person="Fateeva Kristina")
        issues = _check_person(p, test_config)
        assert len(issues) == 1
        assert issues[0].severity == "fixed"
        assert p.person == "Kristina"

    def test_normalizes_kerner_karolina(self, test_config):
        p = _make_proposal(person="Kerner, Karolina")
        issues = _check_person(p, test_config)
        assert len(issues) == 1
        assert p.person == "Karolina"

    def test_unknown_person_flagged(self, test_config):
        p = _make_proposal(person="Dr. Schmidt")
        issues = _check_person(p, test_config)
        assert len(issues) == 1
        assert issues[0].severity == "review"

    def test_normalizes_hyphenated_name(self, test_config):
        p = _make_proposal(person="Kristina Fateeva-Kerner")
        issues = _check_person(p, test_config)
        assert len(issues) == 1
        assert issues[0].severity == "fixed"
        assert p.person == "Kristina"

    def test_skipped_when_no_people_configured(self, no_person_config):
        p = _make_proposal(person="anything")
        issues = _check_person(p, no_person_config)
        assert issues == []


# ── Proposal.filename without person ───────────────────────────────────────


class TestProposalFilenameNoPerson:
    def test_three_field_filename(self):
        p = _make_proposal(person="")
        assert p.filename == "2024-01-15 - Test Sender - Test Topic.pdf"

    def test_four_field_filename(self):
        p = _make_proposal(person="Alexander")
        assert p.filename == "2024-01-15 - Test Sender - Test Topic - Alexander.pdf"


# ── _check_sender_consistency ────────────────────────────────────────────────


class TestCheckSenderConsistency:
    def test_known_sender_normalized(self, test_config):
        registry = {
            "synlab heidelberg": SenderEntry(
                canonical_name="SYNLAB Heidelberg",
                country="Germany",
                default_tags=["Tax"],
                folder_topic="Invoices",
                filing_count=5,
            )
        }
        p = _make_proposal(sender="Synlab Heidelberg", tags=[], folder_topic="Unsorted",
                           target_folder="Family/Germany/Unsorted")
        issues = _check_sender_consistency(p, registry, test_config)

        sender_fix = [i for i in issues if i.field == "sender"]
        tag_fix = [i for i in issues if i.field == "tags"]
        folder_fix = [i for i in issues if i.field == "folder_topic"]

        assert len(sender_fix) == 1
        assert p.sender == "SYNLAB Heidelberg"
        assert len(tag_fix) == 1
        assert "Tax" in p.tags
        assert len(folder_fix) == 1
        assert p.folder_topic == "Invoices"

    def test_unknown_sender_passes(self, test_config):
        p = _make_proposal(sender="Brand New Sender")
        issues = _check_sender_consistency(p, {}, test_config)
        assert issues == []

    def test_country_inferred_when_missing(self, test_config):
        registry = {
            "test sender": SenderEntry(
                canonical_name="Test Sender",
                country="Latvia",
                filing_count=3,
            )
        }
        p = _make_proposal(sender="Test Sender", country="")
        issues = _check_sender_consistency(p, registry, test_config)
        country_fix = [i for i in issues if i.field == "country"]
        assert len(country_fix) == 1
        assert p.country == "Latvia"

    def test_no_duplicate_tags(self, test_config):
        registry = {
            "test sender": SenderEntry(
                canonical_name="Test Sender",
                country="Germany",
                default_tags=["Tax"],
                filing_count=2,
            )
        }
        p = _make_proposal(sender="Test Sender", tags=["Tax"])
        issues = _check_sender_consistency(p, registry, test_config)
        tag_issues = [i for i in issues if i.field == "tags"]
        assert tag_issues == []
        assert p.tags.count("Tax") == 1


# ── _check_batch_sender_drift ───────────────────────────────────────────────


class TestCheckBatchSenderDrift:
    def test_detects_sender_drift(self):
        proposals = [
            _make_proposal(sender="SYNLAB Heidelberg"),
            _make_proposal(sender="SYNLAB Heidelberg"),
            _make_proposal(sender="Synlab Heidelberg"),
        ]
        issues = _check_batch_sender_drift(proposals)
        assert len(issues) == 1
        idx, issue = issues[0]
        assert idx == 2
        assert issue.severity == "fixed"
        assert proposals[2].sender == "SYNLAB Heidelberg"

    def test_no_drift(self):
        proposals = [
            _make_proposal(sender="SYNLAB Heidelberg"),
            _make_proposal(sender="SYNLAB Heidelberg"),
        ]
        issues = _check_batch_sender_drift(proposals)
        assert issues == []

    def test_different_senders_no_drift(self):
        proposals = [
            _make_proposal(sender="SYNLAB Heidelberg"),
            _make_proposal(sender="DZR GmbH"),
        ]
        issues = _check_batch_sender_drift(proposals)
        assert issues == []


# ── _normalize_document_type_topic ───────────────────────────────────────────


class TestNormalizeDocumentTypeTopic:
    # Pre-existing invoice cases ----------------------------------------------

    def test_bare_invoice_unchanged(self):
        p = _make_proposal(topic="Invoice")
        assert _normalize_document_type_topic(p) == []
        assert p.topic == "Invoice"

    def test_invoice_with_period_unchanged(self):
        p = _make_proposal(topic="Invoice 2021-02")
        assert _normalize_document_type_topic(p) == []
        assert p.topic == "Invoice 2021-02"

    def test_descriptive_prefix_stripped(self):
        p = _make_proposal(topic="Language Course Invoice")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert issues[0].severity == "fixed"
        assert p.topic == "Invoice"

    def test_descriptive_prefix_with_period_stripped(self):
        p = _make_proposal(topic="Medical Laboratory Invoice 2021-02")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert p.topic == "Invoice 2021-02"

    def test_legal_services_invoice_stripped(self):
        p = _make_proposal(topic="Legal Services Invoice")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert p.topic == "Invoice"

    def test_mobile_phone_invoice_stripped(self):
        p = _make_proposal(topic="Mobile Phone Invoice 2020-06")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert p.topic == "Invoice 2020-06"

    def test_non_invoice_topic_unchanged(self):
        p = _make_proposal(topic="Credit Report")
        assert _normalize_document_type_topic(p) == []
        assert p.topic == "Credit Report"

    def test_invoice_counter_preserved(self):
        p = _make_proposal(topic="Invoice 2")
        assert _normalize_document_type_topic(p) == []
        assert p.topic == "Invoice 2"

    # Batch regression cases (2026-04-24) -------------------------------------

    def test_interior_design_invoice_window_treatments(self):
        p = _make_proposal(topic="Interior design invoice — window treatments")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert issues[0].severity == "fixed"
        assert issues[0].field == "topic"
        assert p.topic == "Invoice"

    def test_interior_cleaning_service_volvo(self):
        p = _make_proposal(topic="Interior cleaning service — Volvo XC 60")
        issues = _normalize_document_type_topic(p)
        # "service" doesn't match, but no type word present -> no change.
        # Wait: "service" is not in list. Should remain unchanged.
        assert issues == []
        assert p.topic == "Interior cleaning service — Volvo XC 60"

    def test_purchase_contract_and_receipt_furniture(self):
        p = _make_proposal(topic="Purchase contract and receipt — furniture and household items")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert issues[0].severity == "fixed"
        # Multi-word type matches as a unit, not reduced to 'Contract'.
        assert p.topic == "Purchase contract"

    def test_eyeglasses_order_and_warranty(self):
        p = _make_proposal(topic="Eyeglasses order and warranty — Fielmann BD481 CL")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert issues[0].severity == "fixed"
        assert p.topic == "Order"

    def test_purchase_contract_bed_frame(self):
        p = _make_proposal(topic="Purchase contract — bed frame and delivery")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert issues[0].severity == "fixed"
        assert p.topic == "Purchase contract"

    def test_visa_mediation_services_invoice(self):
        p = _make_proposal(topic="Visa mediation services invoice — Russia tourist visa")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert issues[0].severity == "fixed"
        assert p.topic == "Invoice"

    # Additional edge cases from the spec -------------------------------------

    def test_tax_invoice_qualifier_before(self):
        p = _make_proposal(topic="Tax Invoice")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert issues[0].severity == "fixed"
        assert p.topic == "Invoice"

    def test_invoice_em_dash_detail(self):
        p = _make_proposal(topic="Invoice — copy")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert issues[0].severity == "fixed"
        assert p.topic == "Invoice"

    def test_mri_head_scan_report_unchanged(self):
        # No matched document-type word — leave unchanged.
        p = _make_proposal(topic="MRI Head Scan Report")
        assert _normalize_document_type_topic(p) == []
        assert p.topic == "MRI Head Scan Report"

    def test_residence_permit_transfer_fee_unchanged(self):
        p = _make_proposal(topic="Residence permit transfer fee")
        assert _normalize_document_type_topic(p) == []
        assert p.topic == "Residence permit transfer fee"

    # New type-word coverage --------------------------------------------------

    def test_bare_order_unchanged(self):
        p = _make_proposal(topic="Order")
        assert _normalize_document_type_topic(p) == []
        assert p.topic == "Order"

    def test_bare_statement_unchanged(self):
        p = _make_proposal(topic="Statement")
        assert _normalize_document_type_topic(p) == []
        assert p.topic == "Statement"

    def test_checking_account_statement_stripped(self):
        p = _make_proposal(topic="Checking Account Statement — December 2025")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert p.topic == "Statement"

    def test_statement_with_period_preserved(self):
        p = _make_proposal(topic="Checking Account Statement 2025-12")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert p.topic == "Statement 2025-12"

    def test_liability_insurance_policy_stripped(self):
        p = _make_proposal(topic="Liability Insurance Policy — coverage details")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert p.topic == "Policy"

    def test_rental_contract_stripped_not_mistaken_for_purchase(self):
        # Single-word 'Contract' reduces to 'Contract', not 'Purchase contract'.
        p = _make_proposal(topic="Rental Contract — apartment lease")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert p.topic == "Contract"

    def test_purchase_contract_bare_unchanged(self):
        p = _make_proposal(topic="Purchase contract")
        assert _normalize_document_type_topic(p) == []
        assert p.topic == "Purchase contract"

    def test_receipt_qualifier_stripped(self):
        p = _make_proposal(topic="Grocery Receipt")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert p.topic == "Receipt"

    def test_case_insensitive_match_emits_canonical(self):
        p = _make_proposal(topic="tax INVOICE")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert p.topic == "Invoice"

    # Annual statement exception (German Jahresabrechnung) --------------------

    def test_annual_statement_with_year_preserved(self):
        p = _make_proposal(topic="Annual statement 2025")
        assert _normalize_document_type_topic(p) == []
        assert p.topic == "Annual statement 2025"

    def test_annual_statement_earlier_year(self):
        p = _make_proposal(topic="Annual statement 2021")
        assert _normalize_document_type_topic(p) == []
        assert p.topic == "Annual statement 2021"

    def test_annual_statement_casing_normalized(self):
        p = _make_proposal(topic="ANNUAL STATEMENT 2025")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert issues[0].field == "topic"
        assert issues[0].severity == "fixed"
        assert p.topic == "Annual statement 2025"

    def test_bare_statement_with_year_still_bare(self):
        # Plain 'Statement 2025' (no 'Annual' qualifier) is already a bare type
        # with a period suffix and should remain unchanged.
        p = _make_proposal(topic="Statement 2025")
        assert _normalize_document_type_topic(p) == []
        assert p.topic == "Statement 2025"

    def test_annual_statement_without_year_stripped(self):
        # No accounting year — falls back to the bare-type rule.
        p = _make_proposal(topic="Annual Statement")
        issues = _normalize_document_type_topic(p)
        assert len(issues) == 1
        assert p.topic == "Statement"


# ── _check_tags_valid ────────────────────────────────────────────────────────


class TestCheckTagsValid:
    def test_valid_tags_pass(self, test_config):
        p = _make_proposal(tags=["Tax", "Insurance"])
        assert _check_tags_valid(p, test_config) == []

    def test_invalid_tags_removed(self, test_config):
        p = _make_proposal(tags=["Tax", "Medical", "Dental"])
        issues = _check_tags_valid(p, test_config)
        assert len(issues) == 1
        assert p.tags == ["Tax"]

    def test_empty_tags_pass(self, test_config):
        p = _make_proposal(tags=[])
        assert _check_tags_valid(p, test_config) == []


# ── _check_filename_separators ───────────────────────────────────────────────


class TestCheckFilenameSeparators:
    def test_clean_fields_pass(self):
        p = _make_proposal(sender="Test GmbH", topic="Invoice", person="Alexander")
        assert _check_filename_separators(p) == []

    def test_dash_in_sender_replaced(self):
        p = _make_proposal(sender="Wacker - Hautärzte")
        issues = _check_filename_separators(p)
        assert len(issues) == 1
        assert p.sender == "Wacker — Hautärzte"

    def test_dash_in_topic_replaced(self):
        p = _make_proposal(topic="Medical - Dental Invoice")
        issues = _check_filename_separators(p)
        assert len(issues) == 1
        assert p.topic == "Medical — Dental Invoice"


# ── _check_path_unsafe_chars ─────────────────────────────────────────────────


class TestCheckPathUnsafeChars:
    def test_clean_fields_pass(self):
        p = _make_proposal(sender="Fielmann", topic="Eyeglasses", person="Alexander")
        assert _check_path_unsafe_chars(p) == []

    def test_slash_in_sender_replaced(self):
        p = _make_proposal(sender="Fielmann / EyeKraft Optica")
        issues = _check_path_unsafe_chars(p)
        assert len(issues) == 1
        assert issues[0].field == "sender"
        assert issues[0].severity == "fixed"
        assert p.sender == "Fielmann EyeKraft Optica"

    def test_slash_in_topic_replaced(self):
        p = _make_proposal(topic="Order / Warranty")
        issues = _check_path_unsafe_chars(p)
        assert len(issues) == 1
        assert issues[0].field == "topic"
        assert p.topic == "Order Warranty"

    def test_backslash_in_person_replaced(self):
        p = _make_proposal(person="Alex\\ander")
        issues = _check_path_unsafe_chars(p)
        assert len(issues) == 1
        assert issues[0].field == "person"
        assert p.person == "Alex ander"

    def test_null_byte_replaced(self):
        p = _make_proposal(sender="Bad\x00Sender")
        issues = _check_path_unsafe_chars(p)
        assert len(issues) == 1
        assert p.sender == "Bad Sender"

    def test_multiple_slashes_collapse_whitespace(self):
        p = _make_proposal(sender="A / B / C")
        _check_path_unsafe_chars(p)
        assert p.sender == "A B C"

    def test_sanitize_field_idempotent(self):
        assert sanitize_field("clean name") == "clean name"
        assert sanitize_field("a/b") == "a b"
        assert sanitize_field(sanitize_field("a/b")) == "a b"

    def test_resulting_filename_has_no_path_separator(self):
        p = _make_proposal(sender="Fielmann / EyeKraft Optica", topic="Order")
        _check_path_unsafe_chars(p)
        assert "/" not in p.filename
        assert "\\" not in p.filename


# ── build_sender_registry ────────────────────────────────────────────────────


class TestBuildSenderRegistry:
    def test_builds_from_filed_documents(self, tmp_path, test_config):
        germany = tmp_path / "Germany" / "Invoices"
        germany.mkdir(parents=True)
        (germany / "2024-01-15 - SYNLAB Heidelberg - Lab Invoice - Kristina.pdf").touch()
        (germany / "2024-02-10 - SYNLAB Heidelberg - Lab Invoice - Kristina.pdf").touch()

        with patch("docorganizer.validator._read_tags", return_value=["Tax"]):
            registry = build_sender_registry(tmp_path, test_config)

        assert "synlab heidelberg" in registry
        entry = registry["synlab heidelberg"]
        assert entry.canonical_name == "SYNLAB Heidelberg"
        assert entry.country == "Germany"
        assert entry.folder_topic == "Invoices"
        assert entry.filing_count == 2
        assert "Tax" in entry.default_tags

    def test_empty_family_root(self, tmp_path, test_config):
        registry = build_sender_registry(tmp_path, test_config)
        assert registry == {}

    def test_nonexistent_path(self, tmp_path, test_config):
        registry = build_sender_registry(tmp_path / "nonexistent", test_config)
        assert registry == {}

    def test_ignores_dotfiles(self, tmp_path, test_config):
        germany = tmp_path / "Germany"
        germany.mkdir()
        (germany / ".DS_Store").touch()

        with patch("docorganizer.validator._read_tags", return_value=[]):
            registry = build_sender_registry(tmp_path, test_config)

        assert registry == {}

    def test_ignores_non_country_dirs(self, tmp_path, test_config):
        other = tmp_path / "SomeOtherDir"
        other.mkdir()
        (other / "2024-01-01 - Test - Topic - Person.pdf").touch()

        with patch("docorganizer.validator._read_tags", return_value=[]):
            registry = build_sender_registry(tmp_path, test_config)

        assert registry == {}

    def test_flat_archive_without_countries(self, tmp_path, flat_config):
        invoices = tmp_path / "Invoices"
        invoices.mkdir()
        (invoices / "2024-01-15 - Acme Corp - Invoice - Alice.pdf").touch()
        (invoices / "2024-02-10 - Acme Corp - Invoice - Alice.pdf").touch()

        with patch("docorganizer.validator._read_tags", return_value=[]):
            registry = build_sender_registry(tmp_path, flat_config)

        assert "acme corp" in registry
        entry = registry["acme corp"]
        assert entry.canonical_name == "Acme Corp"
        assert entry.country == ""
        assert entry.folder_topic == "Invoices"
        assert entry.filing_count == 2


# ── _check_sender_consistency (no-countries) ─────────────────────────────────


class TestCheckSenderConsistencyNoCountries:
    def test_folder_inferred_without_country(self, flat_config):
        registry = {
            "acme corp": SenderEntry(
                canonical_name="Acme Corp",
                country="",
                folder_topic="Invoices",
                filing_count=5,
            )
        }
        p = _make_proposal(
            sender="Acme Corp", country="", folder_topic="Unsorted",
            target_folder="Documents/Unsorted",
        )
        issues = _check_sender_consistency(p, registry, flat_config)
        folder_fix = [i for i in issues if i.field == "folder_topic"]
        assert len(folder_fix) == 1
        assert p.folder_topic == "Invoices"
        assert p.target_folder == "Documents/Invoices"


# ── validate_proposals (integration) ─────────────────────────────────────────


class TestValidateProposals:
    def test_clean_proposals_pass(self, test_config):
        proposals = [
            _make_proposal(sender="Test Sender", person="Alexander", date="2024-01-15"),
        ]
        issues = validate_proposals(proposals, {}, test_config)
        assert issues == {}

    def test_multiple_issues_collected(self, test_config):
        proposals = [
            _make_proposal(
                person="Philip Alexander Kerner",
                date="15.01.2024",
                tags=["Tax", "Bogus"],
            ),
        ]
        issues = validate_proposals(proposals, {}, test_config)
        assert 0 in issues
        fields = {i.field for i in issues[0]}
        assert "person" in fields
        assert "date" in fields
        assert "tags" in fields


# ── format_validation_report ─────────────────────────────────────────────────


class TestFormatValidationReport:
    def test_no_issues(self):
        proposals = [_make_proposal()]
        report = format_validation_report(proposals, {})
        assert "passed validation" in report

    def test_with_issues(self):
        proposals = [_make_proposal()]
        issues = {
            0: [Issue("sender", "fixed", "old", "new", "normalized sender")]
        }
        report = format_validation_report(proposals, issues)
        assert "FIXED" in report
        assert "Auto-fixed: 1" in report
