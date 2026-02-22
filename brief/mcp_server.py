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
    Returns the cached brief if found, or a message saying none exists.

    Args:
        uri: URL to check
    """
    from .store import BriefStore
    from .renderer import render_brief

    store = BriefStore()
    data = store.check(uri)
    if data:
        return f"brief found\n\n{render_brief(data)}"
    return "no brief for this URI"


@mcp.tool()
def list_briefs() -> str:
    """List all existing briefs in the .briefs/ folder.

    Shows what content has already been briefed.
    Use this to see what's available before requesting new briefs.
    """
    from .store import BriefStore

    store = BriefStore()
    briefs = store.list_all()
    if not briefs:
        return "no briefs yet"

    lines = [f"{len(briefs)} brief(s) available\n"]
    for b in briefs:
        lines.append(f"  [{b['type'].upper()}] {b['uri']}")
        lines.append(f"    {b['summary']}\n")
    return "\n".join(lines)


@mcp.tool()
def compare_sources(uris: list[str], query: str = "summarize this content", depth: int = 2) -> str:
    """Cross-reference multiple sources against the same question.

    Briefs each URI (or uses cache), then renders all at the
    same depth with the same query for apples-to-apples comparison.
    Start with depth=1, go to 2 for more detail.

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
