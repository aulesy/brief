"""PDF extractor — extract text content from PDF files and URLs.

Uses pymupdf for fast, dependency-light PDF text extraction.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def _download_pdf(uri: str) -> str | None:
    """Download a PDF URL to a temp file."""
    try:
        request = Request(uri, headers={"User-Agent": "brief/0.3"})
        with urlopen(request, timeout=30) as response, tempfile.NamedTemporaryFile(
            delete=False, suffix=".pdf"
        ) as handle:
            max_bytes = 50 * 1024 * 1024  # 50MB
            total = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    os.remove(handle.name)
                    return None
                handle.write(chunk)
            return handle.name
    except Exception as exc:
        logger.warning("PDF download failed for %s: %s", uri, exc)
        return None


def extract(uri: str) -> list[dict[str, Any]]:
    """Extract text content from a PDF, return as chunks."""
    try:
        import pymupdf
    except ImportError:
        logger.warning("pymupdf not installed. Run: pip install pymupdf")
        return []

    # If it's a URL, download first
    parsed = urlparse(uri)
    if parsed.scheme in ("http", "https"):
        file_path = _download_pdf(uri)
        if not file_path:
            return []
        cleanup = True
    else:
        file_path = uri
        cleanup = False

    try:
        doc = pymupdf.open(file_path)
        chunks = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text().strip()
            if len(text) < 20:
                continue
            # Keep full page text — summarizer handles length downstream
            clean = text[:3000].rsplit(" ", 1)[0] + "..." if len(text) > 3000 else text
            chunks.append({
                "text": clean,
                "start_sec": float(page_num),
                "end_sec": float(page_num + 1),
            })
        doc.close()
        logger.info("Extracted %d pages from %s", len(chunks), uri)
        return chunks
    except Exception as exc:
        logger.warning("PDF extraction failed for %s: %s", uri, exc)
        return []
    finally:
        if cleanup and file_path:
            try:
                os.remove(file_path)
            except OSError:
                pass
