"""Text extraction from PDF and DOCX files."""

from pathlib import Path

import docx
import pdfplumber


class ExtractionError(Exception):
    """Raised when a file cannot be read (encrypted, corrupted, empty)."""


class UnsupportedFileType(Exception):
    """Raised for file types we don't handle."""


SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


def extract_text(path: Path) -> str:
    """Extract plain text from a PDF or DOCX file.

    Returns the full extracted text as a string.

    Raises:
        FileNotFoundError: if path does not exist.
        UnsupportedFileType: if the file extension is not .pdf or .docx.
        ExtractionError: if the file exists but cannot be read
            (encrypted, corrupted, or contains no extractable text).
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
    """Extract text from a PDF using pdfplumber."""
    try:
        with pdfplumber.open(path) as pdf:
            if not pdf.pages:
                raise ExtractionError(f"PDF has no pages: {path}")

            pages = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)

            full_text = "\n\n".join(pages).strip()

            if not full_text:
                raise ExtractionError(
                    f"No extractable text (likely image-only / scanned): {path}"
                )

            return full_text

    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"Failed to read PDF: {path} — {e}") from e


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
