"""Brief CLI — simple command-line interface.

Usage:
    brief --uri "https://youtube.com/watch?v=abc" --query "how to install"
    brief --batch "https://url1.com" "https://url2.com" --query "compare" --depth 0
    brief --list
"""

from __future__ import annotations

import json
import sys

import typer

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@app.command()
def main(
    uri: str = typer.Option(None, help="Content URI to brief (video URL, page URL, etc.)"),
    batch: list[str] = typer.Option(None, help="Multiple URIs to brief in parallel"),
    query: str = typer.Option("summarize this content", help="What the consuming agent wants to know"),
    depth: int = typer.Option(1, "--depth", min=0, max=2, help="Detail level: 0=headline, 1=summary, 2=deep dive"),
    list_briefs: bool = typer.Option(False, "--list", help="List all existing briefs"),
    raw: bool = typer.Option(False, "--raw", help="Output raw JSON instead of rendered text"),
    force: bool = typer.Option(False, "--force", help="Skip cache and re-extract"),
) -> None:
    """Content compression for AI agents."""
    sys.stdout.reconfigure(encoding="utf-8")

    if list_briefs:
        from .store import BriefStore

        store = BriefStore()
        groups = store.list_all()
        if not groups:
            typer.echo("No briefs found. Create one with: brief --uri <URL> --query <QUERY>")
            raise typer.Exit()

        typer.echo(f"Found {len(groups)} source(s) in .briefs/:\n")
        for g in groups:
            source_type = g.get("type", "").upper() or "UNKNOWN"
            typer.echo(f"  [{source_type}] {g.get('uri', g['slug'])}")
            for b in g.get("briefs", []):
                typer.echo(f"    • {b['file']}: {b.get('preview', '')[:80]}")
            typer.echo()
        raise typer.Exit()

    # ── Batch mode ────────────────────────────────────────────────
    if batch:
        from brief import brief_batch

        typer.echo(f"Briefing {len(batch)} URLs at depth={depth}...\n", err=True)
        results = brief_batch(batch, query=query, depth=depth)
        for i, (url, result) in enumerate(zip(batch, results), 1):
            typer.echo(f"{'─' * 50}")
            typer.echo(f"[{i}/{len(batch)}] {url}")
            typer.echo(f"{'─' * 50}")
            typer.echo(result)
            typer.echo()
        raise typer.Exit()

    # ── Single URI mode ───────────────────────────────────────────
    if not uri:
        typer.echo("Error: --uri or --batch is required.\n"
                   "  brief --uri 'https://example.com' --query 'key takeaways'\n"
                   "  brief --batch 'https://url1.com' --batch 'https://url2.com' --depth 0")
        raise typer.Exit(1)

    from .service import brief, get_brief_data

    if raw:
        brief(uri, query, force=force, depth=depth)
        data = get_brief_data(uri)
        if data:
            typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            typer.echo("Failed to create brief.")
            raise typer.Exit(1)
    else:
        result = brief(uri, query, force=force, depth=depth)
        typer.echo(result)


if __name__ == "__main__":
    app()
