"""Brief store — file-based .briefs/ folder + SQLite index.

Storage layout:
  .briefs/
  ├── {slug}/                         ← subdirectory per URL
  │   ├── _source.json                ← raw extraction data (chunks, metadata)
  │   ├── overview.brief              ← generic summary (no query)
  │   ├── async-support.brief         ← query-specific brief
  │   └── how-to-deploy.brief         ← query-specific brief
  └── _index.sqlite3                  ← lookup index

Each query produces a separate .brief file.
Agents can browse the folder, grep across files, or use SQLite for lookups.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_BRIEFS_DIR = Path.cwd() / ".briefs"


class BriefStore:
    def __init__(
        self,
        briefs_dir: str | Path | None = None,
    ) -> None:
        self.briefs_dir = Path(briefs_dir) if briefs_dir else _DEFAULT_BRIEFS_DIR
        self.briefs_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = str(self.briefs_dir / "_index.sqlite3")
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            # Drop old schema if it exists (migration from flat files)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS briefs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uri TEXT NOT NULL,
                    uri_hash TEXT NOT NULL,
                    query TEXT,
                    slug TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    summary TEXT,
                    created TEXT NOT NULL,
                    UNIQUE(uri_hash, query)
                )
                """
            )
            # Keep old table for backwards compatibility during migration
            conn.commit()

    @staticmethod
    def _uri_hash(uri: str) -> str:
        return hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _slugify(uri: str) -> str:
        """Create a short readable directory name from a URI."""
        from urllib.parse import urlparse

        parsed = urlparse(uri)
        host = (parsed.hostname or "unknown").replace("www.", "")
        path = parsed.path.strip("/").replace("/", "-")
        slug = f"{host}-{path}" if path else host
        slug = re.sub(r"[^a-z0-9-]", "-", slug.lower())
        slug = re.sub(r"-{2,}", "-", slug).strip("-")
        return slug[:60]

    @staticmethod
    def _query_slug(query: str) -> str:
        """Create a filename-safe slug from a query string."""
        if not query or query == "summarize this content":
            return "overview"
        slug = re.sub(r"[^a-z0-9 ]", "", query.lower())
        slug = "-".join(slug.split()[:5])  # max 5 words
        return slug[:40] or "query"

    def _url_dir(self, uri: str) -> Path:
        """Get or create the subdirectory for a URL."""
        slug = self._slugify(uri)
        url_dir = self.briefs_dir / slug
        url_dir.mkdir(parents=True, exist_ok=True)
        return url_dir

    # ── Source data (raw extraction) ───────────────────────────────

    def check_source(self, uri: str) -> dict[str, Any] | None:
        """Look up cached source data by URI. Returns brief dict or None."""
        url_dir = self.briefs_dir / self._slugify(uri)
        source_path = url_dir / "_source.json"
        if not source_path.exists():
            return None

        try:
            data = json.loads(source_path.read_text(encoding="utf-8"))
            logger.debug("Source cache hit: %s", source_path)
            return data
        except (json.JSONDecodeError, OSError):
            return None

    def save_source(self, brief_data: dict[str, Any], overview_text: str = "") -> Path:
        """Save raw extraction data as _source.json + overview.brief."""
        uri = brief_data.get("source", {}).get("uri", "")
        if not uri:
            logger.warning("Cannot save source: no URI.")
            return self.briefs_dir

        url_dir = self._url_dir(uri)
        source_path = url_dir / "_source.json"

        # Write structured JSON
        source_path.write_text(
            json.dumps(brief_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Write overview brief (generic, no query)
        if overview_text:
            overview_path = url_dir / "overview.brief"
            overview_path.write_text(overview_text, encoding="utf-8")
            self._index_brief(uri, None, "overview.brief", overview_text[:200],
                              brief_data.get("created", ""))

        logger.info("Saved source: %s/_source.json", url_dir.name)
        return source_path

    # ── Per-query briefs ──────────────────────────────────────────

    def check_query(self, uri: str, query: str) -> str | None:
        """Check if a specific query has been answered. Returns brief text or None."""
        url_dir = self.briefs_dir / self._slugify(uri)
        query_filename = self._query_slug(query) + ".brief"
        query_path = url_dir / query_filename

        if not query_path.exists():
            return None

        try:
            text = query_path.read_text(encoding="utf-8")
            logger.debug("Query cache hit: %s/%s", url_dir.name, query_filename)
            return text
        except OSError:
            return None

    def save_query(self, uri: str, query: str, brief_text: str,
                   summary: str = "") -> Path:
        """Save a query-specific .brief file and update the index."""
        url_dir = self._url_dir(uri)
        query_filename = self._query_slug(query) + ".brief"
        query_path = url_dir / query_filename

        query_path.write_text(brief_text, encoding="utf-8")
        self._index_brief(uri, query, query_filename, summary[:200])

        # Update trail in all sibling briefs
        self._update_trails(url_dir)

        logger.info("Saved query brief: %s/%s", url_dir.name, query_filename)
        return query_path

    def _update_trails(self, url_dir: Path) -> None:
        """Update the TRAIL section in all .brief files in a directory."""
        brief_files = sorted(url_dir.glob("*.brief"))
        if len(brief_files) < 2:
            return

        for bf in brief_files:
            try:
                # Build trail excluding self
                trail_lines = ["\n─── TRAIL " + "─" * 40]
                for sibling in brief_files:
                    if sibling.name != bf.name:
                        trail_lines.append(f"→ {sibling.name}")
                trail_lines.append("→ _source.json")
                trail_block = "\n".join(trail_lines)

                content = bf.read_text(encoding="utf-8")
                # Remove existing trail section
                content = re.sub(r"\n─── TRAIL ─.*", "", content, flags=re.DOTALL)
                content = content.rstrip() + "\n" + trail_block
                bf.write_text(content, encoding="utf-8")
            except OSError:
                continue

    # ── Index operations ──────────────────────────────────────────

    def _index_brief(self, uri: str, query: str | None, filename: str,
                     summary: str = "", created: str = "") -> None:
        """Add or update a brief in the SQLite index."""
        key = self._uri_hash(uri)
        slug = self._slugify(uri)
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO briefs
                   (uri, uri_hash, query, slug, filename, summary, created)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (uri, key, query, slug, filename, summary, created),
            )
            conn.commit()

    def list_queries(self, uri: str) -> list[dict[str, str]]:
        """List all queries answered for a URI."""
        key = self._uri_hash(uri)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT query, filename, summary FROM briefs WHERE uri_hash = ? ORDER BY created",
                (key,),
            ).fetchall()
        return [
            {"query": r[0] or "overview", "filename": r[1], "summary": r[2] or ""}
            for r in rows
        ]

    def list_all(self) -> list[dict[str, Any]]:
        """List all briefs grouped by URL."""
        results: dict[str, dict[str, Any]] = {}

        # Scan subdirectories
        for subdir in sorted(self.briefs_dir.iterdir()):
            if not subdir.is_dir() or subdir.name.startswith("_"):
                continue

            source_path = subdir / "_source.json"
            uri = ""
            source_type = ""
            if source_path.exists():
                try:
                    data = json.loads(source_path.read_text(encoding="utf-8"))
                    uri = data.get("source", {}).get("uri", "")
                    source_type = data.get("source", {}).get("type", "")
                except (json.JSONDecodeError, OSError):
                    pass

            brief_files = sorted(subdir.glob("*.brief"))
            queries = []
            for bf in brief_files:
                try:
                    content = bf.read_text(encoding="utf-8")
                    # Extract first non-divider line as a summary preview
                    for line in content.split("\n"):
                        line = line.strip()
                        if line and not line.startswith(("═", "─", "→", "▸")):
                            queries.append({
                                "file": bf.name,
                                "preview": line[:100],
                            })
                            break
                except OSError:
                    continue

            if queries:
                results[subdir.name] = {
                    "slug": subdir.name,
                    "uri": uri,
                    "type": source_type,
                    "briefs": queries,
                }

        return list(results.values())

    # ── Backwards compatibility ───────────────────────────────────

    def check(self, uri: str) -> dict[str, Any] | None:
        """Legacy method — checks for source data."""
        return self.check_source(uri)

    def save(self, brief: dict[str, Any], rendered_text: str = "") -> Path:
        """Legacy method — saves source data."""
        return self.save_source(brief, rendered_text)
