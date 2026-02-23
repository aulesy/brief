"""Reddit extractor — fetch posts and comments via Reddit's JSON API.

Reddit blocks regular HTTP requests with 403/429, but every Reddit URL
has a .json endpoint that returns structured data with no authentication.

Extracts: post title, selftext, and top comments.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# User-Agent is required by Reddit's API — they block generic agents
_HEADERS = {
    "User-Agent": "Brief/0.4 (content extraction for AI agents)",
}


def extract(uri: str) -> list[dict[str, Any]]:
    """Extract post content and comments from a Reddit URL."""
    try:
        import httpx
    except ImportError:
        logger.error("httpx is required for Reddit extraction.")
        return []

    # Build the JSON API URL — strip trailing slash, strip query params
    clean = uri.split("?")[0].rstrip("/")
    json_url = clean + ".json"

    try:
        resp = httpx.get(json_url, headers=_HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Reddit JSON API failed for %s: %s", uri, exc)
        return []

    chunks: list[dict[str, Any]] = []

    # Reddit returns a list: [post_listing, comments_listing]
    if not isinstance(data, list) or len(data) < 1:
        logger.warning("Unexpected Reddit JSON structure for %s", uri)
        return []

    # ── Post content ──
    try:
        post_data = data[0]["data"]["children"][0]["data"]
        title = post_data.get("title", "")
        selftext = post_data.get("selftext", "")
        subreddit = post_data.get("subreddit_name_prefixed", "")
        score = post_data.get("score", 0)
        author = post_data.get("author", "[deleted]")

        # First chunk: the post itself
        post_text = f"{title}\n\n{selftext}" if selftext else title
        post_meta = f"Posted by u/{author} in {subreddit} ({score} upvotes)"
        chunks.append({
            "text": f"{post_meta}\n\n{post_text}",
            "start_sec": 0.0,
        })
    except (KeyError, IndexError) as exc:
        logger.warning("Could not parse Reddit post: %s", exc)
        return []

    # ── Comments ──
    if len(data) >= 2:
        try:
            comments = data[1]["data"]["children"]
            for i, comment in enumerate(comments[:20]):  # top 20 comments
                if comment.get("kind") != "t1":
                    continue
                c = comment["data"]
                body = c.get("body", "").strip()
                if not body or body == "[deleted]" or body == "[removed]":
                    continue
                c_author = c.get("author", "[deleted]")
                c_score = c.get("score", 0)

                chunks.append({
                    "text": f"u/{c_author} ({c_score} pts): {body}",
                    "start_sec": float(i + 1),
                })
        except (KeyError, IndexError):
            pass  # No comments is fine

    logger.info("Extracted %d chunks from Reddit: %s", len(chunks), uri)
    return chunks
