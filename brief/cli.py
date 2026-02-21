"""Brief CLI — simple command-line interface.

Usage:
    brief --uri "https://youtube.com/watch?v=abc" --query "how to install"
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
    query: str = typer.Option("summarize this content", help="What the consuming agent wants to know"),
    depth: int = typer.Option(1, "--depth", min=0, max=3, help="Detail level: 0=headline, 1=summary, 2=detailed, 3=full"),
    list_briefs: bool = typer.Option(False, "--list", help="List all existing briefs"),
    raw: bool = typer.Option(False, "--raw", help="Output raw JSON instead of rendered text"),
    force: bool = typer.Option(False, "--force", help="Skip cache and re-extract"),
) -> None:
    """Content compression for AI agents."""
    sys.stdout.reconfigure(encoding="utf-8")

    if list_briefs:
        from .store import BriefStore

        store = BriefStore()
        briefs = store.list_all()
        if not briefs:
            typer.echo("No briefs found. Create one with: brief --uri <URL> --query <QUERY>")
            raise typer.Exit()

        typer.echo(f"Found {len(briefs)} brief(s) in .briefs/:\n")
        for b in briefs:
            typer.echo(f"  [{b['type'].upper()}] {b['uri']}")
            typer.echo(f"    {b['summary']}")
            typer.echo(f"    File: {b['file']}\n")
        raise typer.Exit()

    if not uri:
        typer.echo("Error: --uri is required. Example: brief --uri 'https://youtube.com/watch?v=abc' --query 'how to install'")
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
