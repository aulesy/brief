"""Brief Service — the entry point.

Agents call brief(uri, query) to get a rendered text brief.
Extraction happens once, rendering happens per query.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from .extractors import detect_type
from .renderer import render_brief, render_brief_file
from .store import BriefStore
from .summarizer import summarize

logger = logging.getLogger(__name__)

_store = BriefStore()

_VIDEO_SCHEMES = {"youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "dailymotion.com"}


def _validate_url(uri: str) -> str | None:
    """Check if a URL is reachable before attempting extraction.

    Returns None if the URL is valid, or an error string explaining the problem.
    Skips validation for video platforms (handled by yt-dlp internally).
    """
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    host = parsed.hostname or ""

    # Skip validation for video platforms — yt-dlp handles these directly
    if any(vh in host for vh in _VIDEO_SCHEMES):
        return None

    try:
        import httpx
        resp = httpx.head(uri, timeout=8, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 404:
            return (
                f"url not found (404) — '{uri}' does not exist. "
                "Do not guess or construct URLs. Only pass URLs you have explicitly "
                "navigated to or confirmed exist."
            )
        if resp.status_code in (401, 402, 403, 429):
            return (
                f"url blocked ({resp.status_code}) — '{uri}' requires authentication "
                "or is behind a paywall/bot-protection. Brief cannot extract paywalled content."
            )
    except Exception:
        # If HEAD fails entirely (no httpx, network error, etc.) let extraction try anyway
        pass

    return None



def _content_hash(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _format_timestamp(sec: float) -> str:
    """Convert seconds to human-readable timestamp like '1:25' or '1:02:15'."""
    total = int(sec)
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _build_brief(
    source_type: str,
    uri: str,
    chunks: list[dict[str, Any]],
    summary: str,
    key_points: list[str],
) -> dict[str, Any]:
    """Assemble a .brief v2 dict from extracted data."""
    full_text = " ".join(c.get("text", "") for c in chunks)

    pointers = []
    raw_chunks = []
    for chunk in chunks:
        start = chunk.get("start_sec", 0.0)
        text = chunk.get("text", "").strip()
        if text:
            # Truncate cleanly at ~150 chars for the pointer preview
            pointer_text = text[:150].rsplit(" ", 1)[0] + "..." if len(text) > 150 else text
            p = {
                "sec": round(start, 2),
                "text": pointer_text,
            }
            if source_type == "video":
                p["at"] = _format_timestamp(start)
            pointers.append(p)

            # Store full untruncated text for depth=3
            raw = {"sec": round(start, 2), "text": text}
            if source_type == "video":
                raw["at"] = _format_timestamp(start)
            raw_chunks.append(raw)

    import json
    brief = {
        "v": 2,
        "source": {
            "type": source_type,
            "uri": uri,
            "tokens_original": _estimate_tokens(full_text),
            "hash": _content_hash(full_text),
        },
        "summary": summary,
        "key_points": key_points,
        "pointers": pointers,
        "chunks": raw_chunks,
        "tokens": 0,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    brief["tokens"] = _estimate_tokens(json.dumps(brief))
    return brief


def brief(uri: str, query: str, force: bool = False, depth: int = 1) -> str:
    """Main entry point: get a rendered brief for a URI.

    1. Check store for cached brief
    2. If miss (or force=True): extract → summarize → store
    3. Render with query-aware pointer ranking at requested depth
    4. Return plain text

    Args:
        uri: Content URI (video URL, page URL, etc.)
        query: The consuming agent's current task/question
        force: Skip cache and re-extract
        depth: Detail level (0=headline, 1=summary, 2=detailed, 3=full)

    Returns:
        Plain text brief for agent consumption
    """
    # 1. Cache check
    if not force:
        cached = _store.check(uri)
        if cached:
            logger.info("Brief cache hit for %s", uri)
            rendered = render_brief(cached, query=query, depth=depth)
            return f"brief found\n\n{rendered}"

    # 2. Validate URL before attempting extraction
    url_error = _validate_url(uri)
    if url_error:
        return url_error

    # 3. Detect type and extract
    content_type = detect_type(uri)
    logger.info("Extracting %s content from %s", content_type, uri)

    chunks: list[dict[str, Any]] = []
    if content_type == "video":
        from .extractors.video import extract as extract_video
        chunks = extract_video(uri)
    elif content_type == "webpage":
        from .extractors.webpage import extract as extract_webpage
        chunks = extract_webpage(uri)
    elif content_type == "pdf":
        from .extractors.pdf import extract as extract_pdf
        chunks = extract_pdf(uri)
    else:
        logger.warning("No extractor available for type '%s' yet.", content_type)
        return f"no extractor available for {content_type} yet"

    if not chunks:
        return f"could not extract content from {uri}"

    # 3. Summarize (query-focused when available)
    summary, key_points = summarize(chunks, query=query)

    # 4. Build brief
    brief_data = _build_brief(
        source_type=content_type,
        uri=uri,
        chunks=chunks,
        summary=summary,
        key_points=key_points,
    )

    # 5. Render
    rendered = render_brief(brief_data, query=query, depth=depth)

    # 6. Save (.brief file uses the stable, query-independent format)
    slug = _store._slugify(uri)
    _store.save(brief_data, rendered_text=render_brief_file(brief_data))

    return f"brief created → .briefs/{slug}.brief\n\n{rendered}"


def get_brief_data(uri: str) -> dict[str, Any] | None:
    """Get the raw stored brief JSON (for tooling/debugging)."""
    return _store.check(uri)


def compare(
    uris: list[str],
    query: str = "summarize this content",
    depth: int = 2,
) -> str:
    """Compare multiple sources against the same query.

    Briefs each URI (or uses cache), then renders all
    at the same depth with the same query for apples-to-apples
    cross-referencing.

    Args:
        uris: List of content URIs to compare
        query: The comparison question
        depth: Detail level for all sources (default: 2 for detailed)

    Returns:
        Rendered comparison text with separators
    """
    parts = []
    for i, uri in enumerate(uris, 1):
        result = brief(uri, query, depth=depth)
        # Strip status line
        lines = result.split("\n")
        content = "\n".join(lines[2:]) if lines[0].startswith("brief") else result
        parts.append(f"--- source {i} ---\n{content.strip()}")

    return "\n\n".join(parts)
