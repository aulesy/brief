"""Brief renderer — converts stored JSON to agent-readable plain text.

Supports progressive depth (layered briefs):
  depth=0  headline     ~10 tokens   "3-min tutorial: install GitHub CLI"
  depth=1  summary      ~80 tokens   + key points + top 3 pointers
  depth=2  detailed     ~200 tokens  + all pointers with timestamps
  depth=3  full         ~2000 tokens + raw chunk text (everything extracted)

The render layer is where query-awareness lives.
Same stored brief, different rendering per consuming agent's query.
"""

from __future__ import annotations

import re
from typing import Any

_STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "how",
    "in", "is", "it", "of", "on", "or", "that", "the", "to", "what", "with",
}


def _terms(text: str) -> set[str]:
    items = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {w for w in items if w and w not in _STOPWORDS}


def _relevance(query: str, text: str) -> float:
    q = _terms(query)
    if not q:
        return 0.0
    t = _terms(text)
    return len(q & t) / len(q)


def _format_pointer(p: dict[str, Any]) -> str:
    at = p.get("at", "")
    text = p.get("text", "")
    if at:
        return f"{at} {text}"
    return text


def _rank_pointers(
    pointers: list[dict[str, Any]], query: str | None
) -> list[dict[str, Any]]:
    if query and pointers:
        return sorted(
            pointers,
            key=lambda p: _relevance(query, p.get("text", "")),
            reverse=True,
        )
    return pointers


def render_brief(
    brief: dict[str, Any],
    query: str | None = None,
    depth: int = 1,
) -> str:
    """Render a stored brief at the requested depth.

    depth=0: headline only (source type + URI + summary first sentence)
    depth=1: summary + key points + top 3 pointers (default)
    depth=2: summary + key points + ALL pointers
    depth=3: everything + full chunk text
    """
    source = brief.get("source", {})
    source_type = source.get("type", "content").upper()
    uri = source.get("uri", "unknown")
    summary = brief.get("summary", "")
    key_points = brief.get("key_points", [])
    pointers = brief.get("pointers", [])

    # ── Layer 0: headline ──
    if depth == 0:
        headline = summary[:160].rsplit(" ", 1)[0].rstrip(".,;:!?") + "..." if len(summary) > 160 else summary
        if not headline:
            headline = "No summary"
        return f"[{source_type}] {headline}"

    # Re-rank pointers by query relevance
    ranked_pointers = _rank_pointers(pointers, query)

    # Derive query-aware key points from ranked chunks/pointers.
    # Uses full chunks if available (more text = better matching), falls back to pointers.
    # When no query is given, falls back to the stored static key_points.
    raw_chunks = brief.get("chunks", [])
    if query and (raw_chunks or ranked_pointers):
        source_pool = _rank_pointers(raw_chunks, query) if raw_chunks else ranked_pointers
        dynamic_key_points = [p.get("text", "") for p in source_pool[:5] if p.get("text")]
    else:
        dynamic_key_points = key_points  # static fallback for no-query calls

    # ── Layer 1: summary (default) ──
    header = f"[{source_type}: {uri}]"
    parts = [header, summary]

    if dynamic_key_points:
        # Truncate each key point to ~120 chars for readability
        kp_texts = [kp[:120] + "..." if len(kp) > 120 else kp for kp in dynamic_key_points]
        parts.append("Key: " + " · ".join(kp_texts))

    label = "Moments:" if source_type == "VIDEO" else "Sections:"

    if depth == 1:
        if ranked_pointers:
            parts.append(
                f"{label} "
                + " · ".join(_format_pointer(p) for p in ranked_pointers[:3])
            )
        return "\n".join(parts)

    # ── Layer 2: detailed ──
    if ranked_pointers:
        ptr_lines = [label]
        for p in ranked_pointers:
            ptr_lines.append(f"  {_format_pointer(p)}")
        parts.append("\n".join(ptr_lines))

    if depth == 2:
        return "\n".join(parts)

    # ── Layer 3: full chunks ──
    raw_chunks = brief.get("chunks", pointers)  # fall back to pointers for old briefs
    full_label = "\nFull transcript:" if source_type == "VIDEO" else "\nFull content:"
    parts.append(full_label)
    for p in raw_chunks:  # original order, not re-ranked
        at = p.get("at", "")
        text = p.get("text", "")
        if at:
            parts.append(f"  [{at}] {text}")
        else:
            parts.append(f"  {text}")

    return "\n".join(parts)


# ── .brief file format ─────────────────────────────────────────────

_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://[^)]+)\)')


def _extract_links(text: str) -> list[tuple[str, str]]:
    """Extract markdown links from text, returns [(label, url), ...]."""
    seen = set()
    links = []
    for label, url in _LINK_RE.findall(text):
        if url not in seen:
            seen.add(url)
            links.append((label.strip(), url))
    return links


def _strip_links(text: str) -> str:
    """Replace markdown links with just their label text, remove anchors."""
    # Remove HTTP links → keep label
    text = _LINK_RE.sub(r'\1', text)
    # Remove internal anchor links like [¶](#section) or [text](#anchor)
    text = re.sub(r'\[([^\]]*)\]\(#[^)]*\)', r'\1', text)
    # Remove leftover pilcrow markers
    text = text.replace(' ¶', '').replace('¶', '')
    return text.strip()


def _is_code_like(text: str) -> bool:
    """Heuristic: detect chunks that look like code rather than prose."""
    indicators = [
        text.strip().startswith(("def ", "class ", "import ", "from ", "return ", "async ", "@", "$", ">>>", "{")),
        text.count("(") > 2 and text.count(")") > 2,
        text.count("=") > 2,
        "def " in text and ":" in text,
    ]
    return sum(indicators) >= 2


def _truncate_line(text: str, max_len: int = 120) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0].rstrip(".,;:!?") + "..."


def render_overview_file(brief: dict[str, Any]) -> str:
    """Render the overview.brief file — a generic, query-independent card."""
    source = brief.get("source", {})
    source_type = source.get("type", "content").upper()
    uri = source.get("uri", "unknown")
    summary = brief.get("summary", "")
    key_points = brief.get("key_points", [])
    pointers = brief.get("pointers", [])
    created = brief.get("created", "")
    chunks = brief.get("chunks", [])

    lines: list[str] = []

    title = ""
    if pointers:
        first_text = pointers[0].get("text", "")
        title = _strip_links(first_text)[:80]
    if not title and summary:
        title = summary.split(".")[0].strip()[:80]

    lines.append("═══ BRIEF " + "═" * max(1, 50 - len("═══ BRIEF ")))
    lines.append(title)
    lines.append(uri)
    meta = f"Type: {source_type}"
    if created:
        meta += f" | Extracted: {created[:10]}"
    lines.append(meta)

    if summary:
        lines.append("")
        lines.append("─── SUMMARY " + "─" * max(1, 48 - len("─── SUMMARY ")))
        lines.append(summary)

    if key_points:
        lines.append("")
        lines.append("─── KEY POINTS " + "─" * max(1, 45 - len("─── KEY POINTS ")))
        for kp in key_points[:5]:
            clean = _strip_links(kp)
            lines.append(f"• {_truncate_line(clean)}")

    prose_pointers = [p for p in pointers if not _is_code_like(p.get("text", ""))]
    if prose_pointers:
        lines.append("")
        section_label = "MOMENTS" if source_type == "VIDEO" else "SECTIONS"
        lines.append(f"─── {section_label} " + "─" * max(1, 46 - len(section_label)))
        for p in prose_pointers[:12]:
            at = p.get("at", "")
            text = _strip_links(p.get("text", ""))
            text = _truncate_line(text, 100)
            if at:
                lines.append(f"▸ [{at}] {text}")
            else:
                lines.append(f"▸ {text}")

    all_text = " ".join(c.get("text", "") for c in (chunks or pointers))
    links = _extract_links(all_text)
    if links:
        lines.append("")
        lines.append("─── LINKS " + "─" * max(1, 50 - len("─── LINKS ")))
        for label, url in links[:15]:
            if label.startswith("http"):
                lines.append(f"→ {url}")
            else:
                lines.append(f"→ {label}: {url}")

    return "\n".join(lines)


def render_query_file(
    uri: str,
    query: str,
    summary: str,
    key_points: list[str],
    source_type: str = "WEBPAGE",
    created: str = "",
) -> str:
    """Render a per-query .brief file — focused answer to a specific question."""
    lines: list[str] = []

    lines.append("═══ BRIEF " + "═" * max(1, 50 - len("═══ BRIEF ")))
    lines.append(_strip_links(query)[:80])
    lines.append(uri)
    query_clean = _strip_links(query)[:60]
    meta = f"Query: \"{query_clean}\" | Type: {source_type.upper()}"
    if created:
        meta += f" | {created[:10]}"
    lines.append(meta)

    if summary:
        lines.append("")
        lines.append("─── ANSWER " + "─" * max(1, 49 - len("─── ANSWER ")))
        lines.append(summary)

    if key_points:
        lines.append("")
        lines.append("─── KEY POINTS " + "─" * max(1, 45 - len("─── KEY POINTS ")))
        for kp in key_points[:5]:
            clean = _strip_links(kp)
            lines.append(f"• {_truncate_line(clean)}")

    # TRAIL section is added by store._update_trails() after saving

    return "\n".join(lines)


# Backwards compatibility
render_brief_file = render_overview_file

