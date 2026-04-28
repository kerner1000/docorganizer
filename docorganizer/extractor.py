"""Text extraction from PDF and DOCX files."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import docx
import pdfplumber


class ExtractionError(Exception):
    """Raised when a file cannot be read (encrypted, corrupted, empty)."""


class UnsupportedFileType(Exception):
    """Raised for file types we don't handle."""


SUPPORTED_EXTENSIONS = {".pdf", ".docx"}

OCR_LANGUAGES = "deu+eng+lav+rus"
OCR_DPI = 300


def extract_text(path: Path) -> str:
    """Extract plain text from a PDF or DOCX file.

    Returns the full extracted text as a string.

    Raises:
        FileNotFoundError: if path does not exist.
        UnsupportedFileType: if the file extension is not .pdf or .docx.
        ExtractionError: if the file exists but cannot be read
            (encrypted, corrupted, or contains no extractable text even after OCR).
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileType(
            f"Unsupported file type: {suffix} (supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))})"
        )

    if suffix == ".pdf":
        return _extract_pdf(path)
    return _extract_docx(path)


def _extract_pdf(path: Path) -> str:
    """Extract text from a PDF using pdfplumber, falling back to OCR if empty.

    OCR fallback handles scanned PDFs and PDFs with text rendered as vector
    outlines (no embedded glyphs). Requires pdftoppm + tesseract on PATH.
    """
    try:
        with pdfplumber.open(path) as pdf:
            if not pdf.pages:
                raise ExtractionError(f"PDF has no pages: {path}")

            pages = [page.extract_text() for page in pdf.pages]
            full_text = "\n\n".join(t for t in pages if t).strip()

            if full_text:
                return full_text

    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"Failed to read PDF: {path} — {e}") from e

    ocr_text = _ocr_pdf(path)
    if ocr_text:
        return ocr_text

    raise ExtractionError(
        f"No extractable text (likely image-only / scanned) and OCR yielded nothing: {path}"
    )


def _ocr_pdf(path: Path) -> str:
    """OCR a PDF by rendering pages to PNG and running tesseract.

    Returns empty string if tesseract or pdftoppm are unavailable, or if OCR
    produces no text. Never raises — OCR is a best-effort fallback.
    """
    if not (shutil.which("tesseract") and shutil.which("pdftoppm")):
        return ""

    with tempfile.TemporaryDirectory(prefix="docorganizer-ocr-") as tmp:
        tmp_dir = Path(tmp)
        prefix = tmp_dir / "page"

        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", str(OCR_DPI), str(path), str(prefix)],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ""

        page_texts = []
        for png in sorted(tmp_dir.glob("page-*.png")):
            try:
                result = subprocess.run(
                    ["tesseract", str(png), "-", "-l", OCR_LANGUAGES],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                page_texts.append(result.stdout)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue

        return "\n\n".join(t for t in page_texts if t).strip()


def _extract_docx(path: Path) -> str:
    """Extract text from a DOCX using python-docx."""
    try:
        doc = docx.Document(path)

        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        full_text = "\n".join(paragraphs).strip()

        if not full_text:
            raise ExtractionError(f"DOCX contains no text: {path}")

        return full_text

    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"Failed to read DOCX: {path} — {e}") from e
