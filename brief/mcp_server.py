"""Brief MCP Server — expose brief() as tools for any MCP-capable agent.

Run:
    python -m brief.mcp_server

Or add to your MCP config (e.g., Claude Desktop, Cursor):
    {
      "mcpServers": {
        "brief": {
          "command": "python",
          "args": ["-m", "brief.mcp_server"],
          "env": {
            "BRIEF_LLM_API_KEY": "sk-or-v1-your-key",
            "BRIEF_LLM_BASE_URL": "https://openrouter.ai/api/v1",
            "BRIEF_LLM_MODEL": "anthropic/claude-3.5-sonnet"
          }
        }
      }
    }
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("brief")


@mcp.tool()
def brief_content(uri: str, query: str = "summarize this content", depth: int = 1) -> str:
    """Brief a piece of content (video, webpage, PDF).

    Extracts content, generates a summary, caches the result.
    Returns a text brief at the requested depth:
      depth=0  headline    ~10 tokens
      depth=1  summary     ~80 tokens (default)
      depth=2  detailed    ~200 tokens
      depth=3  full        all extracted content

    If a brief already exists for this URI, returns the cached
    version re-ranked for your query — no re-extraction.
    Start with depth=0 or 1. Go deeper only if you need more detail.

    IMPORTANT: Only pass URLs you have explicitly navigated to or
    confirmed exist. Do NOT construct, guess, or hallucinate URLs.
    If the URL returns a 404 error, search for the correct URL first.

    Args:
        uri: URL of the content (YouTube video, webpage, etc.)
        query: What you want to know about this content
        depth: Detail level 0-3 (0=headline, 1=summary, 2=detailed, 3=full)
    """
    from .service import brief

    return brief(uri, query, depth=depth)


@mcp.tool()
def check_existing_brief(uri: str) -> str:
    """Check if a brief already exists for this URI.

    Call this BEFORE brief_content() to avoid redundant work.
    Returns a list of queries already answered for this URL,
    or a message saying none exists.

    Args:
        uri: URL to check
    """
    from .service import check_existing

    return check_existing(uri)


@mcp.tool()
def list_briefs() -> str:
    """List all existing briefs in the .briefs/ folder.

    Shows what content has already been briefed.
    Use this to see what's available before requesting new briefs.
    """
    from .store import BriefStore

    store = BriefStore()
    groups = store.list_all()
    if not groups:
        return "no briefs yet"

    lines = []
    for g in groups:
        uri = g.get("uri", "")
        slug = g.get("slug", "")
        briefs = g.get("briefs", [])
        lines.append(f".briefs/{slug}/ ({len(briefs)} brief{'s' if len(briefs) != 1 else ''})")
        if uri:
            lines.append(f"  {uri}")
        for b in briefs:
            lines.append(f"  • {b['file']}: {b.get('preview', '')[:80]}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def compare_sources(uris: list[str], query: str = "summarize this content", depth: int = 2) -> str:
    """Cross-reference multiple sources against the same question.

    Briefs each URI (or uses cache), then renders all at the
    same depth with the same query for apples-to-apples comparison.
    Start with depth=1, go to 2 for more detail.

    IMPORTANT: Only pass URLs you have explicitly navigated to or
    confirmed exist. Do NOT construct, guess, or hallucinate URLs.

    Args:
        uris: List of URLs to compare
        query: The comparison question
        depth: Detail level for all sources (0-3)
    """
    from .service import compare

    return compare(uris, query=query, depth=depth)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
