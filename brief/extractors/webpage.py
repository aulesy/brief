"""Webpage extractor — fetch and extract text content from web pages.

Uses trafilatura for high-quality main content extraction
(strips nav, ads, footers, scripts — keeps the article text).

Fallback chain:
  1. trafilatura (fast, handles most sites)
  2. httpx + html parsing (handles ZSTD, CDN blocks, encoding issues)
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _truncate_clean(text: str, max_len: int) -> str:
    """Truncate at word boundary, never mid-word."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:!?") + "..."


def _text_to_chunks(text: str) -> list[dict[str, Any]]:
    """Split extracted text into paragraph-level chunks."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks = []
    for i, para in enumerate(paragraphs):
        if len(para) < 20:
            continue
        chunks.append({
            "text": _truncate_clean(para, 500),
            "start_sec": float(i),
            "end_sec": float(i + 1),
        })

    return chunks


def _extract_trafilatura(uri: str) -> str | None:
    """Primary extractor using trafilatura."""
    try:
        import trafilatura
    except ImportError:
        logger.warning("trafilatura not installed. Run: pip install trafilatura")
        return None

    try:
        downloaded = trafilatura.fetch_url(uri)
        if not downloaded:
            return None

        text = trafilatura.extract(
            downloaded,
            include_links=False,
            include_images=False,
            include_tables=True,
            favor_recall=True,
        )

        if text and len(text.strip()) >= 50:
            return text.strip()

    except Exception as exc:
        logger.debug("trafilatura failed for %s: %s", uri, exc)

    return None


def _extract_httpx_fallback(uri: str) -> str | None:
    """Fallback extractor using httpx with browser-like headers.

    Handles cases where trafilatura fails:
    - ZSTD compressed responses
    - CDN/bot blocks
    - Encoding issues
    """
    try:
        import httpx
    except ImportError:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",  # no zstd, no br
    }

    try:
        resp = httpx.get(uri, headers=headers, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text

        if not html or len(html) < 100:
            return None

        # Try trafilatura on the raw HTML we fetched ourselves
        try:
            import trafilatura
            text = trafilatura.extract(
                html,
                include_links=False,
                include_images=False,
                include_tables=True,
                favor_recall=True,
            )
            if text and len(text.strip()) >= 50:
                logger.info("httpx + trafilatura fallback succeeded for %s", uri)
                return text.strip()
        except Exception:
            pass

        # Last resort: strip HTML tags manually
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        if len(text) >= 50:
            logger.info("httpx + tag-strip fallback succeeded for %s (%d chars)", uri, len(text))
            return text

    except Exception as exc:
        logger.debug("httpx fallback failed for %s: %s", uri, exc)

    return None


def extract(uri: str) -> list[dict[str, Any]]:
    """Extract text content from a webpage, return as chunks."""
    logger.info("Fetching webpage: %s", uri)

    # 1. Try trafilatura (fast, good quality)
    text = _extract_trafilatura(uri)

    # 2. Fallback: httpx with browser headers
    if not text:
        logger.info("trafilatura failed, trying httpx fallback for %s", uri)
        text = _extract_httpx_fallback(uri)

    if not text:
        logger.warning("All extractors failed for %s", uri)
        return []

    chunks = _text_to_chunks(text)
    logger.info("Extracted %d chunks from %s", len(chunks), uri)
    return chunks
