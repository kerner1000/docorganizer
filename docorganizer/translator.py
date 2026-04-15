"""Document translation via DeepL API.

Detects source language from extracted text and translates documents
(PDF, DOCX) while preserving formatting.
"""

from __future__ import annotations

from pathlib import Path

import deepl

from .config import ArchiveConfig

# Only send a small snippet for language detection to minimise API cost.
DETECTION_SNIPPET_LENGTH = 200


class TranslationError(Exception):
    """Raised when document translation fails."""


def detect_language(
    text: str,
    target_lang: str,
    translator: deepl.Translator,
) -> str | None:
    """Detect source language by translating a small text snippet.

    Returns the detected language code (e.g. ``"LV"``, ``"RU"``) or *None*
    if detection fails.
    """
    snippet = text[:DETECTION_SNIPPET_LENGTH]
    result = translator.translate_text(snippet, target_lang=target_lang)
    return result.detected_source_lang


def translate_document(
    source_path: Path,
    target_lang: str,
    translator: deepl.Translator,
) -> Path:
    """Translate a PDF or DOCX via the DeepL document API.

    The translated file is written next to the source with a language suffix,
    e.g. ``report.pdf`` -> ``report [EN].pdf``.

    Returns the path to the translated file.
    Raises :class:`TranslationError` on failure.
    """
    # Build "[EN]" style suffix from target_lang (strip region: "EN-US" -> "EN")
    lang_tag = target_lang.split("-")[0]
    output_path = source_path.with_stem(f"{source_path.stem} [{lang_tag}]")

    try:
        translator.translate_document_from_filepath(
            source_path,
            output_path,
            target_lang=target_lang,
        )
    except deepl.DocumentTranslationException as exc:
        raise TranslationError(
            f"DeepL document translation failed for {source_path.name}: {exc}"
        ) from exc
    except deepl.DeepLException as exc:
        raise TranslationError(
            f"DeepL API error for {source_path.name}: {exc}"
        ) from exc

    return output_path
