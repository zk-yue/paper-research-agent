"""PDF text extraction shared by evidence preparation and reference cards."""

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import DependencyError, PyPdfError


def read_pdf_pages(path: str | Path) -> list[dict]:
    """Return every physical page, including blank pages, without performing OCR.

    Normalize parser-specific failures to the existing evidence fallback
    boundary. The document and input stream are not retained after extraction.
    """
    try:
        with Path(path).open("rb") as stream:
            document = PdfReader(stream)
            return [
                {"page": index + 1, "text": page.extract_text() or ""}
                for index, page in enumerate(document.pages)
            ]
    except (PyPdfError, DependencyError) as error:
        raise ValueError("PDF text could not be read") from error
