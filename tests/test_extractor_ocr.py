"""Tests for the OCR fallback in the PDF extractor."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from docorganizer.extractor import (
    ExtractionError,
    _extract_pdf,
    _ocr_pdf,
    extract_text,
)


def _write_text_pdf(path: Path, text: str) -> None:
    """Write a minimal PDF whose text pdfplumber can extract."""
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    c.drawString(72, 720, text)
    c.save()


def _write_image_only_pdf(path: Path, text: str) -> None:
    """Write a PDF whose page is a rasterised image — no selectable text,
    mirroring the real-world case of scanned receipts or vector-outlined invoices.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1200, 300), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 60)
    except OSError:
        font = ImageFont.load_default()
    draw.text((40, 100), text, fill="black", font=font)
    img.save(str(path), "PDF", resolution=200.0)


def test_ocr_pdf_returns_empty_when_tools_missing(tmp_path, monkeypatch):
    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert _ocr_pdf(pdf) == ""


def test_ocr_pdf_returns_empty_when_pdftoppm_fails(tmp_path, monkeypatch):
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"not a real pdf")

    monkeypatch.setattr(shutil, "which", lambda cmd: f"/fake/{cmd}")

    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _ocr_pdf(pdf) == ""


def test_extract_pdf_raises_when_no_text_and_no_ocr(tmp_path, monkeypatch):
    """When extraction yields nothing and OCR can't run, raise ExtractionError."""
    pdf = tmp_path / "empty.pdf"
    _write_text_pdf(pdf, "")  # empty text page

    monkeypatch.setattr(shutil, "which", lambda _: None)

    with pytest.raises(ExtractionError, match="OCR yielded nothing"):
        _extract_pdf(pdf)


def test_extract_pdf_uses_pdfplumber_when_text_present(tmp_path):
    pytest.importorskip("reportlab")

    pdf = tmp_path / "hello.pdf"
    _write_text_pdf(pdf, "Hello world")

    result = extract_text(pdf)
    assert "Hello world" in result


@pytest.mark.skipif(
    not (shutil.which("tesseract") and shutil.which("pdftoppm")),
    reason="tesseract and/or pdftoppm not installed",
)
def test_extract_pdf_ocr_fallback_end_to_end(tmp_path):
    """If pdfplumber finds nothing, OCR should pick up rendered text."""
    pytest.importorskip("reportlab")

    pdf = tmp_path / "image_only.pdf"
    _write_image_only_pdf(pdf, "OCR FALLBACK TEST")

    # Confirm pdfplumber finds no text on this PDF.
    import pdfplumber

    with pdfplumber.open(pdf) as p:
        assert not any((page.extract_text() or "").strip() for page in p.pages)

    # extract_text should still succeed via OCR.
    result = extract_text(pdf)
    assert "OCR" in result.upper() or "FALLBACK" in result.upper()
