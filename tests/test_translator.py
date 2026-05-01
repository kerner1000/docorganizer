"""Tests for document translation via DeepL API."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from docorganizer.cli import Extraction, Proposal, _is_translation_companion, execute_all
from docorganizer.config import ArchiveConfig, ArchiveContext
from docorganizer.translator import (
    DETECTION_SNIPPET_LENGTH,
    TranslationError,
    detect_language,
    translate_document,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def translation_config():
    """ArchiveConfig with translation enabled (LV + RU → EN-US)."""
    return ArchiveConfig(
        name="Test Archive",
        root_folder="Family",
        people={"Kristina": ["Kristina Fateeva"]},
        countries=["Latvia"],
        tags={"Tax": "Tax-relevant"},
        mandatory_tags=[],
        prompt_context="Test.",
        translation_target="EN-US",
        translation_sources=["LV", "RU"],
    )


@pytest.fixture
def translation_ctx(tmp_path, translation_config):
    (tmp_path / "inbox").mkdir()
    (tmp_path / "Family").mkdir()
    return ArchiveContext(config=translation_config, root=tmp_path)


@pytest.fixture
def no_translation_config():
    """ArchiveConfig with translation disabled."""
    return ArchiveConfig(
        name="Test Archive",
        root_folder="Family",
        people={},
        countries=[],
        tags={},
        mandatory_tags=[],
        prompt_context="Test.",
    )


# ── detect_language ─────────────────────────────────────────────────────────


class TestDetectLanguage:
    def test_returns_detected_lang(self):
        translator = MagicMock()
        result = MagicMock()
        result.detected_source_lang = "LV"
        translator.translate_text.return_value = result

        lang = detect_language("Latvijas teksts ir šeit", "EN-US", translator)

        assert lang == "LV"
        translator.translate_text.assert_called_once()
        # Verify only a snippet was sent
        call_args = translator.translate_text.call_args
        assert len(call_args[0][0]) <= DETECTION_SNIPPET_LENGTH

    def test_sends_only_snippet(self):
        translator = MagicMock()
        result = MagicMock()
        result.detected_source_lang = "RU"
        translator.translate_text.return_value = result

        long_text = "x" * 500
        detect_language(long_text, "EN-US", translator)

        sent_text = translator.translate_text.call_args[0][0]
        assert len(sent_text) == DETECTION_SNIPPET_LENGTH

    def test_returns_german_for_german_text(self):
        translator = MagicMock()
        result = MagicMock()
        result.detected_source_lang = "DE"
        translator.translate_text.return_value = result

        lang = detect_language("Dies ist ein deutscher Text", "EN-US", translator)
        assert lang == "DE"


# ── translate_document ──────────────────────────────────────────────────────


class TestTranslateDocument:
    def test_creates_file_with_lang_suffix(self, tmp_path):
        source = tmp_path / "doc.pdf"
        source.write_text("latvian content")

        translator = MagicMock()
        # Simulate DeepL writing the output file
        translator.translate_document_from_filepath.side_effect = (
            lambda src, out, **kw: Path(out).write_text("translated")
        )

        result = translate_document(source, "EN-US", translator)

        assert result == tmp_path / "doc [EN].pdf"
        assert result.exists()
        translator.translate_document_from_filepath.assert_called_once_with(
            source, result, target_lang="EN-US",
        )

    def test_strips_region_from_lang_tag(self, tmp_path):
        source = tmp_path / "report.docx"
        source.write_text("content")

        translator = MagicMock()
        translator.translate_document_from_filepath.side_effect = (
            lambda src, out, **kw: Path(out).write_text("translated")
        )

        result = translate_document(source, "EN-US", translator)
        assert result.stem == "report [EN]"

    def test_raises_translation_error_on_deepl_failure(self, tmp_path):
        import deepl

        source = tmp_path / "doc.pdf"
        source.write_text("content")

        translator = MagicMock()
        translator.translate_document_from_filepath.side_effect = (
            deepl.DeepLException("API error")
        )

        with pytest.raises(TranslationError, match="API error"):
            translate_document(source, "EN-US", translator)

    def test_raises_translation_error_on_timeout(self, tmp_path):
        source = tmp_path / "doc.pdf"
        source.write_text("content")

        translator = MagicMock()
        translator.translate_document_from_filepath.side_effect = (
            TimeoutError("Operation timed out")
        )

        with pytest.raises(TranslationError, match="Network/timeout"):
            translate_document(source, "EN-US", translator)

    def test_cleans_up_partial_output_on_failure(self, tmp_path):
        import deepl

        source = tmp_path / "doc.pdf"
        source.write_text("content")

        def write_partial_then_fail(src, out, **kw):
            Path(out).write_text("partial download...")
            raise deepl.DocumentTranslationException(
                "stalled mid-download", document_handle=None,
            )

        translator = MagicMock()
        translator.translate_document_from_filepath.side_effect = (
            write_partial_then_fail
        )

        expected_partial = tmp_path / "doc [EN].pdf"

        with pytest.raises(TranslationError):
            translate_document(source, "EN-US", translator)

        assert not expected_partial.exists(), (
            "Partial output file must be removed on failure"
        )


# ── Config ──────────────────────────────────────────────────────────────────


class TestTranslationConfig:
    def test_translation_enabled(self, translation_config):
        assert translation_config.translation_enabled is True

    def test_translation_disabled_when_no_target(self, no_translation_config):
        assert no_translation_config.translation_enabled is False

    def test_translation_disabled_when_empty_sources(self):
        config = ArchiveConfig(
            name="Test",
            root_folder=None,
            people={},
            countries=[],
            tags={},
            mandatory_tags=[],
            prompt_context="",
            translation_target="EN-US",
            translation_sources=[],
        )
        assert config.translation_enabled is False


# ── Translation companion filter ────────────────────────────────────────────


class TestIsTranslationCompanion:
    def test_detects_companion(self, translation_config):
        path = Path("inbox/doc [EN].pdf")
        assert _is_translation_companion(path, translation_config) is True

    def test_ignores_normal_file(self, translation_config):
        path = Path("inbox/doc.pdf")
        assert _is_translation_companion(path, translation_config) is False

    def test_ignores_when_translation_disabled(self, no_translation_config):
        path = Path("inbox/doc [EN].pdf")
        assert _is_translation_companion(path, no_translation_config) is False


# ── Proposal serialization ──────────────────────────────────────────────────


class TestProposalTranslationSerialization:
    def test_round_trip_with_translated_path(self):
        p = Proposal(
            original_path=Path("inbox/doc.pdf"),
            sender="Latvijas Banka",
            topic="Account Statement",
            person="Kristina",
            date="2025-01-15",
            country="Latvia",
            folder_topic="Bank",
            target_folder="Family/Latvia/Bank",
            tags=["Tax"],
            confidence="High",
            notes="",
            translated_path=Path("inbox/doc [EN].pdf"),
        )
        d = p.to_dict()
        assert d["translated_path"] == "inbox/doc [EN].pdf"

        restored = Proposal.from_dict(d)
        assert restored.translated_path == Path("inbox/doc [EN].pdf")

    def test_round_trip_without_translated_path(self):
        p = Proposal(
            original_path=Path("inbox/doc.pdf"),
            sender="Finanzamt",
            topic="Steuerbescheid",
            person="Alexander",
            date="2025-01-15",
            country="Germany",
            folder_topic="Tax",
            target_folder="Family/Germany/Tax",
            tags=["Tax"],
            confidence="High",
            notes="",
        )
        d = p.to_dict()
        assert "translated_path" not in d

        restored = Proposal.from_dict(d)
        assert restored.translated_path is None

    def test_translated_filename(self):
        p = Proposal(
            original_path=Path("inbox/doc.pdf"),
            sender="Latvijas Banka",
            topic="Account Statement",
            person="Kristina",
            date="2025-01-15",
            country="Latvia",
            folder_topic="Bank",
            target_folder="Family/Latvia/Bank",
            tags=[],
            confidence="High",
            notes="",
            translated_path=Path("inbox/doc [EN].pdf"),
        )
        assert p.translated_filename == "2025-01-15 - Latvijas Banka - Account Statement - Kristina [EN].pdf"

    def test_translated_filename_none_when_no_translation(self):
        p = Proposal(
            original_path=Path("inbox/doc.pdf"),
            sender="Finanzamt",
            topic="Tax",
            person="Alexander",
            date="2025-01-15",
            country="Germany",
            folder_topic="Tax",
            target_folder="Family/Germany/Tax",
            tags=[],
            confidence="High",
            notes="",
        )
        assert p.translated_filename is None


# ── Execute with translation ────────────────────────────────────────────────


class TestExecuteWithTranslation:
    def test_moves_both_original_and_translation(self, translation_ctx):
        inbox = translation_ctx.inbox
        original = inbox / "doc.pdf"
        translated = inbox / "doc [EN].pdf"
        original.write_text("latvian content")
        translated.write_text("english content")

        target_dir = translation_ctx.root / "Family" / "Latvia" / "Bank"

        p = Proposal(
            original_path=original,
            sender="Latvijas Banka",
            topic="Account Statement",
            person="Kristina",
            date="2025-01-15",
            country="Latvia",
            folder_topic="Bank",
            target_folder="Family/Latvia/Bank",
            tags=[],
            confidence="High",
            notes="",
            status="approved",
            translated_path=translated,
        )

        with patch("docorganizer.cli.apply_tags"):
            executed = execute_all([p], translation_ctx)

        assert len(executed) == 1
        expected_original = target_dir / "2025-01-15 - Latvijas Banka - Account Statement - Kristina.pdf"
        expected_translation = target_dir / "2025-01-15 - Latvijas Banka - Account Statement - Kristina [EN].pdf"
        assert expected_original.exists()
        assert expected_translation.exists()

    def test_moves_original_even_when_translation_missing(self, translation_ctx):
        inbox = translation_ctx.inbox
        original = inbox / "doc.pdf"
        original.write_text("content")

        p = Proposal(
            original_path=original,
            sender="Latvijas Banka",
            topic="Account Statement",
            person="Kristina",
            date="2025-01-15",
            country="Latvia",
            folder_topic="Bank",
            target_folder="Family/Latvia/Bank",
            tags=[],
            confidence="High",
            notes="",
            status="approved",
            translated_path=Path(inbox / "nonexistent [EN].pdf"),
        )

        with patch("docorganizer.cli.apply_tags"):
            executed = execute_all([p], translation_ctx)

        assert len(executed) == 1
        target_dir = translation_ctx.root / "Family" / "Latvia" / "Bank"
        assert (target_dir / "2025-01-15 - Latvijas Banka - Account Statement - Kristina.pdf").exists()

    def test_no_translation_companion_moved_when_none(self, translation_ctx):
        inbox = translation_ctx.inbox
        original = inbox / "doc.pdf"
        original.write_text("german content")

        p = Proposal(
            original_path=original,
            sender="Finanzamt",
            topic="Steuerbescheid",
            person="Alexander",
            date="2025-01-15",
            country="Germany",
            folder_topic="Tax",
            target_folder="Family/Germany/Tax",
            tags=[],
            confidence="High",
            notes="",
            status="approved",
        )

        with patch("docorganizer.cli.apply_tags"):
            executed = execute_all([p], translation_ctx)

        assert len(executed) == 1
        target_dir = translation_ctx.root / "Family" / "Germany" / "Tax"
        # Only the original should exist
        files = list(target_dir.iterdir())
        assert len(files) == 1
