"""Webpage extractor — fetch and extract text content from web pages.

Uses trafilatura for high-quality main content extraction
(strips nav, ads, footers, scripts — keeps the article text).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def extract(uri: str) -> list[dict[str, Any]]:
    """Extract text content from a webpage, return as chunks."""
    try:
        import trafilatura
    except ImportError:
        logger.warning("trafilatura not installed. Run: pip install trafilatura")
        return []

    logger.info("Fetching webpage: %s", uri)

    try:
        downloaded = trafilatura.fetch_url(uri)
        if not downloaded:
            logger.warning("Could not fetch: %s", uri)
            return []

        text = trafilatura.extract(
            downloaded,
            include_links=False,
            include_images=False,
            include_tables=True,
            favor_recall=True,
        )

        if not text or len(text.strip()) < 50:
            logger.warning("Extracted text too short from: %s", uri)
            return []

    except Exception as exc:
        logger.warning("Extraction failed for %s: %s", uri, exc)
        return []

    # Split into paragraph-level chunks
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks = []
    for i, para in enumerate(paragraphs):
        if len(para) < 20:
            continue
        chunks.append({
            "text": para[:500],  # cap per-chunk size
            "start_sec": float(i),  # use index as position
            "end_sec": float(i + 1),
        })

    logger.info("Extracted %d chunks from %s", len(chunks), uri)
    return chunks
