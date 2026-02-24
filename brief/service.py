"""Brief Service — the entry point.

Agents call brief(uri, query, depth) to get a rendered text brief.
Extraction happens once, summarization happens per (query, depth).

Rules:
1. Extract once → save raw chunks to _source.json
2. Each (query, depth) → one LLM call → one .brief file
3. Same query + depth → zero LLM calls, return cached .brief
4. New query or new depth → one LLM call, repeat rule 2
"""

from __future__ import annotations

import hashlib
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from .extractors import detect_type
from .renderer import render_query_file
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
    """
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    host = parsed.hostname or ""

    # Skip validation for platforms with dedicated extractors
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


def _build_source_data(
    source_type: str,
    uri: str,
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build clean source data — raw extraction only, no LLM output."""
    return {
        "source": {
            "type": source_type,
            "uri": uri,
        },
        "chunks": [
            {"text": c.get("text", "").strip(), "start_sec": c.get("start_sec", 0.0)}
            for c in chunks
            if c.get("text", "").strip()
        ],
        "created": datetime.now(timezone.utc).isoformat(),
    }


def _extract(uri: str) -> tuple[str, list[dict[str, Any]]] | None:
    """Extract content from a URI. Returns (content_type, chunks) or None."""
    url_error = _validate_url(uri)
    if url_error:
        return None

    content_type = detect_type(uri)
    print(f"⟳ Extracting {content_type} content...", file=sys.stderr, flush=True)

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
        return None

    if not chunks:
        return None

    return content_type, chunks


def brief(uri: str, query: str, force: bool = False, depth: int = 1) -> str:
    """Main entry point: get a rendered brief for a URI.

    Flow:
    1. Check if this (query, depth) was already answered → return cached .brief
    2. Check if source data exists → summarize with LLM for this (query, depth)
    3. If no source data: extract → save source → summarize → save .brief
    4. Return rendered brief

    Args:
        uri: Content URI (video URL, page URL, etc.)
        query: The consuming agent's current task/question
        force: Skip cache and re-extract
        depth: Detail level (0=headline, 1=summary, 2=detailed)

    Returns:
        Plain text brief for agent consumption
    """
    # Clamp depth to 0-2
    depth = max(0, min(2, depth))

    # Clean up URI — trailing commas/semicolons sneak in from CLI and agents
    uri = uri.strip().rstrip(",;")
    query = query.strip().rstrip(",;")

    slug = _store._slugify(uri)

    if not force:
        # ── Rule 3: Same query + depth → return cached .brief ──
        if depth > 0:
            cached = _store.check_query(uri, query, depth)
            if cached:
                _store.record_cache_hit(uri, query, depth)
                logger.info("Cache hit: %s / %s (depth=%d)", slug, query, depth)
                return f"brief found → .briefs/{slug}/\n\n{cached}"

        # depth=0 never saves, but check if source exists to avoid re-extraction
        # (we still need an LLM call for depth=0 since it's not cached)

    # ── Get chunks: from source cache or fresh extraction ──

    cached_source = _store.check_source(uri) if not force else None
    if cached_source:
        chunks = cached_source.get("chunks", [])
        content_type = cached_source.get("source", {}).get("type", "webpage")
        created = cached_source.get("created", "")
    else:
        result = _extract(uri)
        if result is None:
            url_error = _validate_url(uri)
            if url_error:
                return url_error
            return f"could not extract content from {uri}"

        content_type, raw_chunks = result
        source_data = _build_source_data(content_type, uri, raw_chunks)
        _store.save_source(source_data)
        chunks = source_data["chunks"]
        created = source_data["created"]

    if not chunks:
        return f"could not extract content from {uri}"

    # ── Rule 2 & 4: Summarize with LLM ──

    print("⟳ Summarizing with LLM...", file=sys.stderr, flush=True)
    summary, key_points, tokens_used = summarize(chunks, query=query, depth=depth)

    # ── Save .brief file (depth 1-2 only, depth 0 is triage) ──

    if depth > 0 and summary:
        brief_text = render_query_file(
            uri=uri, query=query, summary=summary,
            key_points=key_points, source_type=content_type,
            created=created,
        )
        _store.save_query(
            uri, query, depth, brief_text,
            summary=summary, key_points=key_points,
            tokens_used=tokens_used,
        )
        return f"brief created → .briefs/{slug}/\n\n{brief_text}"

    # depth=0: return headline directly, no file saved
    if summary:
        label = f"[{content_type.upper()}: {uri}]"
        return f"{label}\n{summary}"

    return f"could not summarize content from {uri}"


def get_brief_data(uri: str) -> dict[str, Any] | None:
    """Get the raw stored source JSON (for tooling/debugging)."""
    return _store.check_source(uri)


def check_existing(uri: str = "") -> str:
    """Check what briefs exist. No URI = compact overview, with URI = detail."""
    if not uri:
        # Compact overview of all sources
        groups = _store.list_all()
        if not groups:
            return "no briefs yet"

        source_count = sum(1 for g in groups if g.get("type") != "comparison")
        comp_count = sum(1 for g in groups if g.get("type") == "comparison")

        lines = [f".briefs/ — {source_count} source{'s' if source_count != 1 else ''}, {comp_count} comparison{'s' if comp_count != 1 else ''}", ""]
        for g in groups:
            slug = g.get("slug", "")
            briefs = g.get("briefs", [])
            count = len(briefs)
            label = "comparison" if g.get("type") == "comparison" else "brief"
            lines.append(f"  {slug}/ ({count} {label}{'s' if count != 1 else ''})")

        # Add stats
        stats = _store.get_stats()
        if stats["total_tokens_used"] > 0:
            lines.append("")
            lines.append(f"📊 {stats['total_tokens_used']:,} tokens spent, ~{stats['tokens_saved']:,} tokens saved by cache ({stats['total_cache_hits']} cache hits)")

        return "\n".join(lines)

    queries = _store.check_existing(uri)
    if not queries:
        return f"No briefs exist for {uri}. Call brief_content to create one."

    slug = _store._slugify(uri)
    lines = [f"Briefs for {uri} (.briefs/{slug}/):", ""]
    for q in queries:
        label = q["query"]
        depth_label = f"depth={q['depth']}" if q.get("depth") else ""
        preview = q["summary"][:80] + "..." if len(q["summary"]) > 80 else q["summary"]
        lines.append(f"  • {label} ({depth_label}): {q['filename']}")
        if preview:
            lines.append(f"    {preview}")
    lines.append("")
    lines.append("Call brief_content with a new query to add more.")
    return "\n".join(lines)


def compare(
    uris: list[str],
    query: str = "summarize this content",
    depth: int = 1,
    force: bool = False,
) -> str:
    """Compare multiple sources against the same query.

    Returns a focused comparison, not the individual briefs.
    Individual briefs are created and cached separately.
    Use the TRAIL section to find them.

    depth=0: one-sentence comparison
    depth=1: synthesis + per-source notes (default)
    depth=2: detailed comparative analysis
    """
    from .summarizer import synthesize_comparison

    # Check comparison cache first (order-invariant)
    if not force:
        cached = _store.check_comparison(uris, query, depth)
        if cached:
            logger.info("Comparison cache hit")
            return f"comparison found → .briefs/_comparisons/\n\n{cached}"

    # Brief each source individually (they get cached for later use)
    brief_texts = []
    source_slugs = []
    for uri in uris:
        result = brief(uri, query, depth=depth)
        lines = result.split("\n")
        content = "\n".join(lines[2:]) if lines[0].startswith("brief") else result
        brief_texts.append(content.strip())
        # Track the slug + query file for TRAIL
        slug = _store._slugify(uri)
        query_file = _store._query_slug(query, depth) + ".brief"
        source_slugs.append(f".briefs/{slug}/{query_file}")

    # Synthesize comparison
    print("⟳ Synthesizing comparison...", file=sys.stderr, flush=True)
    synthesis = synthesize_comparison(brief_texts, query=query, depth=depth)

    # Build output
    lines = []
    lines.append("═══ COMPARISON " + "═" * 45)
    lines.append(query)
    lines.append(f"Sources: {len(uris)} | Depth: {depth} | {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    lines.append("")

    if synthesis:
        lines.append("─── ANALYSIS " + "─" * 47)
        lines.append(synthesis)
    else:
        lines.append("─── ANALYSIS " + "─" * 47)
        lines.append("Synthesis unavailable (no LLM). See individual briefs below.")

    # TRAIL: point to individual source briefs
    lines.append("")
    lines.append("─── TRAIL " + "─" * 50)
    for i, (uri, path) in enumerate(zip(uris, source_slugs), 1):
        lines.append(f"→ source {i}: {path}")

    result_text = "\n".join(lines)

    # Cache the comparison
    _store.save_comparison(uris, query, depth, result_text)
    return f"comparison created → .briefs/_comparisons/\n\n{result_text}"
