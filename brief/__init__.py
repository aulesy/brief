"""
.brief — Content compression for AI agents.

Usage:
    from brief import brief, check_brief, brief_batch

    # Brief a single URL
    text = brief("https://example.com/article", "key takeaways")

    # Brief multiple URLs (research workflow)
    results = brief_batch([
        "https://example.com/article1",
        "https://example.com/article2",
    ], query="comparison", depth=0)

    # Check if a brief exists
    data = check_brief("https://example.com/article")
"""

from .renderer import render_brief
from .service import brief, get_brief_data, compare
from .store import BriefStore

_store = BriefStore()


def check_brief(uri: str):
    """Check if a brief exists for this URI. Returns brief dict or None."""
    return _store.check(uri)


def brief_batch(
    uris: list[str],
    query: str = "summarize this content",
    depth: int = 0,
) -> list[str]:
    """Brief multiple URIs. Returns list of rendered briefs.

    Ideal for research workflows:
      results = brief_batch(urls, query="how to deploy", depth=0)
      # → 10 headlines, ~90 tokens total
      # Agent picks relevant ones, then goes deeper on each
    """
    return [brief(uri, query, depth=depth) for uri in uris]


__all__ = ["brief", "render_brief", "check_brief", "get_brief_data", "brief_batch", "compare"]
