"""Brief Service — the entry point.

Agents call brief(uri, query) to get a rendered text brief.
Extraction happens once, rendering happens per query.
Each query produces a separate .brief file in the URL's subdirectory.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from .extractors import detect_type
from .renderer import render_brief, render_overview_file, render_query_file
from .store import BriefStore
from .summarizer import summarize

logger = logging.getLogger(__name__)

_store = BriefStore()

_VIDEO_SCHEMES = {"youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "dailymotion.com"}
_REDDIT_HOSTS = {"reddit.com", "old.reddit.com", "np.reddit.com"}
_GITHUB_HOSTS = {"github.com"}


def _validate_url(uri: str) -> str | None:
    """Check if a URL is reachable before attempting extraction.

    Returns None if the URL is valid, or an error string explaining the problem.
    Skips validation for video platforms (handled by yt-dlp internally).
    """
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    host = parsed.hostname or ""

    # Skip validation for video platforms and Reddit — they have their own extraction
    if any(vh in host for vh in _VIDEO_SCHEMES):
        return None
    if any(rh in host for rh in _REDDIT_HOSTS):
        return None
    if any(gh in host for gh in _GITHUB_HOSTS):
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
            pointer_text = text[:150].rsplit(" ", 1)[0] + "..." if len(text) > 150 else text
            p = {
                "sec": round(start, 2),
                "text": pointer_text,
            }
            if source_type == "video":
                p["at"] = _format_timestamp(start)
            pointers.append(p)

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


def _trim_chunks(chunks: list[dict[str, Any]], max_words: int = 1200) -> list[dict[str, Any]]:
    """Trim chunks to a word budget for re-summarization."""
    trimmed = []
    word_count = 0
    for c in chunks:
        text = c.get("text", "")
        word_count += len(text.split())
        trimmed.append(c)
        if word_count >= max_words:
            break
    return trimmed


def brief(uri: str, query: str, force: bool = False, depth: int = 1) -> str:
    """Main entry point: get a rendered brief for a URI.

    Flow:
    1. Check if this specific query was already answered → return cached .brief
    2. Check if source data exists → re-summarize with LLM for new query
    3. If no source data: extract → summarize → save source + overview + query brief
    4. Render and return

    Args:
        uri: Content URI (video URL, page URL, etc.)
        query: The consuming agent's current task/question
        force: Skip cache and re-extract
        depth: Detail level (0=headline, 1=summary, 2=detailed, 3=full)

    Returns:
        Plain text brief for agent consumption
    """
    is_real_query = query and query != "summarize this content"
    slug = _store._slugify(uri)

    if not force:
        # ── Check 1: Was this exact query already answered?
        if is_real_query:
            cached_query = _store.check_query(uri, query)
            if cached_query:
                logger.info("Query cache hit: %s / %s", slug, query)
                return f"brief found → .briefs/{slug}/\n\n{cached_query}"

        # ── Check 2: Do we have source data (extraction cache)?
        cached_source = _store.check_source(uri)
        if cached_source:
            logger.info("Source cache hit for %s", uri)

            if is_real_query and depth > 0:
                # Re-summarize with the LLM for this new query
                chunks = cached_source.get("chunks", cached_source.get("pointers", []))
                source_type = cached_source.get("source", {}).get("type", "webpage")
                created = cached_source.get("created", "")

                if chunks:
                    trimmed = _trim_chunks(chunks)
                    try:
                        new_summary, new_key_points = summarize(trimmed, query=query)
                        if new_summary:
                            # Save per-query .brief file
                            brief_text = render_query_file(
                                uri=uri, query=query, summary=new_summary,
                                key_points=new_key_points, source_type=source_type,
                                created=created,
                            )
                            _store.save_query(uri, query, brief_text, summary=new_summary)

                            # Also render for agent response
                            updated = {**cached_source, "summary": new_summary,
                                        "key_points": new_key_points}
                            rendered = render_brief(updated, query=query, depth=depth)
                            return f"brief created → .briefs/{slug}/\n\n{rendered}"
                    except Exception as exc:
                        logger.debug("Re-summarization failed: %s", exc)

            # Fall back to rendering from cached source
            rendered = render_brief(cached_source, query=query, depth=depth)
            return f"brief found → .briefs/{slug}/\n\n{rendered}"

    # ── Fresh extraction ──────────────────────────────────────────

    # Validate URL before attempting extraction
    url_error = _validate_url(uri)
    if url_error:
        return url_error

    # Detect type and extract
    content_type = detect_type(uri)
    logger.info("Extracting %s content from %s", content_type, uri)

    chunks: list[dict[str, Any]] = []
    if content_type == "video":
        from .extractors.video import extract as extract_video
        chunks = extract_video(uri)
    elif content_type == "webpage":
        from .extractors.webpage import extract as extract_webpage
        chunks = extract_webpage(uri)
    elif content_type == "reddit":
        from .extractors.reddit import extract as extract_reddit
        chunks = extract_reddit(uri)
    elif content_type == "github":
        from .extractors.github import extract as extract_github
        chunks = extract_github(uri)
    elif content_type == "pdf":
        from .extractors.pdf import extract as extract_pdf
        chunks = extract_pdf(uri)
    else:
        logger.warning("No extractor available for type '%s' yet.", content_type)
        return f"no extractor available for {content_type} yet"

    if not chunks:
        return f"could not extract content from {uri}"

    # Summarize
    summary, key_points = summarize(chunks, query=query)

    # Build brief data
    brief_data = _build_brief(
        source_type=content_type,
        uri=uri,
        chunks=chunks,
        summary=summary,
        key_points=key_points,
    )

    # Save source data + overview brief
    overview_text = render_overview_file(brief_data)
    _store.save_source(brief_data, overview_text)

    # Save per-query brief (if depth > 0 and real query)
    if is_real_query and depth > 0:
        query_text = render_query_file(
            uri=uri, query=query, summary=summary,
            key_points=key_points, source_type=content_type,
            created=brief_data.get("created", ""),
        )
        _store.save_query(uri, query, query_text, summary=summary)

    # Render for agent response
    rendered = render_brief(brief_data, query=query, depth=depth)
    return f"brief created → .briefs/{slug}/\n\n{rendered}"


def get_brief_data(uri: str) -> dict[str, Any] | None:
    """Get the raw stored brief JSON (for tooling/debugging)."""
    return _store.check_source(uri)


def check_existing(uri: str) -> str:
    """Check what briefs exist for a URI. Returns a human-readable summary."""
    queries = _store.list_queries(uri)
    if not queries:
        return f"No briefs exist for {uri}. Call brief_content to create one."

    slug = _store._slugify(uri)
    lines = [f"Briefs for {uri} (.briefs/{slug}/):", ""]
    for q in queries:
        label = q["query"]
        preview = q["summary"][:80] + "..." if len(q["summary"]) > 80 else q["summary"]
        lines.append(f"  • {label}: {q['filename']}")
        if preview:
            lines.append(f"    {preview}")
    lines.append("")
    lines.append("Call brief_content with a new query to add more.")
    return "\n".join(lines)


def compare(
    uris: list[str],
    query: str = "summarize this content",
    depth: int = 2,
) -> str:
    """Compare multiple sources against the same query."""
    parts = []
    for i, uri in enumerate(uris, 1):
        result = brief(uri, query, depth=depth)
        lines = result.split("\n")
        content = "\n".join(lines[2:]) if lines[0].startswith("brief") else result
        parts.append(f"--- source {i} ---\n{content.strip()}")

    return "\n\n".join(parts)
