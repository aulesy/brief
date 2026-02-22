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
        # First sentence of summary
        headline = summary.split(".")[0].strip() if summary else "No summary"
        return f"[{source_type}] {headline}"

    # Re-rank pointers by query relevance
    ranked_pointers = _rank_pointers(pointers, query)

    # ── Layer 1: summary (default) ──
    header = f"[{source_type}: {uri}]"
    parts = [header, summary]

    if key_points:
        parts.append("Key: " + " · ".join(key_points[:5]))

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
