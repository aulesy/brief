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

from concurrent.futures import ThreadPoolExecutor, as_completed

from .service import brief, get_brief_data, compare, check_existing, _store


def check_brief(uri: str):
    """Check what briefs exist for this URI. Returns summary string or None."""
    return check_existing(uri)


def brief_batch(
    uris: list[str],
    query: str = "summarize this content",
    depth: int = 0,
) -> list[str]:
    """Brief multiple URIs in parallel. Returns list of rendered briefs.

    Ideal for research workflows:
      results = brief_batch(urls, query="how to deploy", depth=0)
      # → headlines for each URL
      # Agent picks relevant ones, then goes deeper
    """
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(uris))) as executor:
        future_to_uri = {
            executor.submit(brief, uri, query, False, depth): uri
            for uri in uris
        }
        for future in as_completed(future_to_uri):
            uri = future_to_uri[future]
            try:
                results[uri] = future.result()
            except Exception as exc:
                results[uri] = f"error: {exc}"
    return [results[uri] for uri in uris]  # preserve original order


__all__ = ["brief", "check_brief", "get_brief_data", "brief_batch", "compare"]
