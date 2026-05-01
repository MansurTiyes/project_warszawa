"""
PyMuPDF-based STARS PDF text extractor.

Usage:
    from services.pdf_parser import extract_stars_text

    text = extract_stars_text(pdf_bytes)
"""

from __future__ import annotations

import logging
import re

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# STARS prints course codes with a space (e.g. "CSCI 103", "EE 364").
# Normalize to no-separator form ("CSCI103") so both LLM calls see consistent codes.
_COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,6})\s+(\d{3}[A-Z]{0,2})\b")


def extract_stars_text(pdf_bytes: bytes) -> str:
    """
    Extract the full text of a STARS PDF and return it as a single string.

    Pages are joined with a double newline. Course codes are normalized from
    "DEPT NNN" to "DEPTNNN" at extraction time so both LLM calls see consistent
    codes without prompt gymnastics.

    Raises ValueError for an invalid or empty PDF.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Could not open PDF: {exc}") from exc

    if doc.page_count == 0:
        raise ValueError("PDF has no pages.")

    pages: list[str] = [page.get_text("text") for page in doc]
    doc.close()

    raw = "\n\n".join(pages)
    normalized = _COURSE_CODE_RE.sub(r"\1-\2", raw)

    logger.debug(
        "Extracted %d pages, %d chars from STARS PDF.", len(pages), len(normalized)
    )
    return normalized
