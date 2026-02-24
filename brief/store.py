"""Brief store — file-based .briefs/ folder + SQLite index.

Storage layout:
  .briefs/
  ├── {slug}/                         ← subdirectory per URL
  │   ├── _source.json                ← raw extraction data (chunks only)
  │   ├── async-support.brief         ← depth=1 query brief
  │   └── async-support-deep.brief    ← depth=2 query brief
  └── _index.sqlite3                  ← lookup index

Each (query, depth) produces a separate .brief file.
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS briefs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uri TEXT NOT NULL,
                    uri_hash TEXT NOT NULL,
                    query TEXT,
                    depth INTEGER DEFAULT 1,
                    slug TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    summary TEXT,
                    key_points TEXT,
                    created TEXT NOT NULL,
                    UNIQUE(uri_hash, query, depth)
                )
                """
            )
            # Migration: add depth and key_points columns if missing
            try:
                conn.execute("SELECT depth FROM briefs LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE briefs ADD COLUMN depth INTEGER DEFAULT 1")
            try:
                conn.execute("SELECT key_points FROM briefs LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE briefs ADD COLUMN key_points TEXT")
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
    def _query_slug(query: str, depth: int = 1) -> str:
        """Create a filename-safe slug from a query string + depth."""
        if not query or query == "summarize this content":
            base = "summary"
        else:
            base = re.sub(r"[^a-z0-9 ]", "", query.lower())
            base = "-".join(base.split()[:5])  # max 5 words
            base = base[:40] or "query"
        if depth == 2:
            base += "-deep"
        return base

    def _url_dir(self, uri: str) -> Path:
        """Get or create the subdirectory for a URL."""
        slug = self._slugify(uri)
        url_dir = self.briefs_dir / slug
        url_dir.mkdir(parents=True, exist_ok=True)
        return url_dir

    # ── Source data (raw extraction only) ─────────────────────────

    def check_source(self, uri: str) -> dict[str, Any] | None:
        """Look up cached raw extraction data by URI. Returns dict or None."""
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

    def save_source(self, source_data: dict[str, Any]) -> Path:
        """Save raw extraction data as _source.json. No LLM output."""
        uri = source_data.get("source", {}).get("uri", "")
        if not uri:
            logger.warning("Cannot save source: no URI.")
            return self.briefs_dir

        url_dir = self._url_dir(uri)
        source_path = url_dir / "_source.json"

        source_path.write_text(
            json.dumps(source_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info("Saved source: %s/_source.json", url_dir.name)
        return source_path

    # ── Per-query briefs ──────────────────────────────────────────

    def check_query(self, uri: str, query: str, depth: int = 1) -> str | None:
        """Check if a specific (query, depth) has been answered. Returns brief text or None."""
        url_dir = self.briefs_dir / self._slugify(uri)
        query_filename = self._query_slug(query, depth) + ".brief"
        query_path = url_dir / query_filename

        if not query_path.exists():
            return None

        try:
            text = query_path.read_text(encoding="utf-8")
            logger.debug("Query cache hit: %s/%s", url_dir.name, query_filename)
            return text
        except OSError:
            return None

    def save_query(self, uri: str, query: str, depth: int, brief_text: str,
                   summary: str = "", key_points: list[str] | None = None) -> Path:
        """Save a query-specific .brief file and update the index."""
        url_dir = self._url_dir(uri)
        query_filename = self._query_slug(query, depth) + ".brief"
        query_path = url_dir / query_filename

        query_path.write_text(brief_text, encoding="utf-8")
        self._index_brief(
            uri, query, depth, query_filename,
            summary=summary,
            key_points=key_points,
        )

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

    def _index_brief(self, uri: str, query: str | None, depth: int,
                     filename: str, summary: str = "",
                     key_points: list[str] | None = None,
                     created: str = "") -> None:
        """Add or update a brief in the SQLite index."""
        key = self._uri_hash(uri)
        slug = self._slugify(uri)
        kp_json = json.dumps(key_points or [])
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO briefs
                   (uri, uri_hash, query, depth, slug, filename, summary, key_points, created)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uri, key, query, depth, slug, filename, summary, kp_json, created),
            )
            conn.commit()

    def check_existing(self, uri: str) -> list[dict[str, Any]]:
        """List all queries answered for a URI (for check_existing_brief MCP tool)."""
        key = self._uri_hash(uri)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT query, depth, filename, summary FROM briefs WHERE uri_hash = ? ORDER BY created",
                (key,),
            ).fetchall()
        return [
            {"query": r[0] or "summary", "depth": r[1], "filename": r[2], "summary": r[3] or ""}
            for r in rows
        ]

    def list_all(self) -> list[dict[str, Any]]:
        """List all briefs grouped by URL."""
        results: dict[str, dict[str, Any]] = {}

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

        # Include comparisons if any exist
        comp_dir = self.briefs_dir / "_comparisons"
        if comp_dir.is_dir():
            comp_files = sorted(comp_dir.glob("*.brief"))
            if comp_files:
                comps = []
                for cf in comp_files:
                    try:
                        content = cf.read_text(encoding="utf-8")
                        preview = ""
                        for line in content.split("\n"):
                            line = line.strip()
                            if line and not line.startswith(("═", "─", "===", "---")):
                                preview = line[:100]
                                break
                        comps.append({"file": cf.name, "preview": preview})
                    except OSError:
                        continue
                if comps:
                    results["_comparisons"] = {
                        "slug": "_comparisons",
                        "uri": "",
                        "type": "comparison",
                        "briefs": comps,
                    }

        return list(results.values())

    # ── Comparison caching ────────────────────────────────────────

    @staticmethod
    def _short_slug(uri: str) -> str:
        """Extract a short readable name from a URI for comparison filenames."""
        from urllib.parse import urlparse
        parsed = urlparse(uri)
        host = (parsed.hostname or "unknown").replace("www.", "")
        # Take just the first part of the domain (e.g. "fastapi" from "fastapi.tiangolo.com")
        parts = host.split(".")
        # Use the most meaningful part — skip generic TLDs
        slug = parts[0] if len(parts) <= 2 else parts[0]
        # If first part is too generic (like "docs"), use first two parts
        if slug in ("docs", "api", "www", "blog", "app", "dev") and len(parts) > 1:
            slug = f"{parts[0]}-{parts[1]}"
        return re.sub(r"[^a-z0-9]", "-", slug.lower()).strip("-")[:20]

    def _comparison_key(self, uris: list[str], query: str, depth: int) -> str:
        """Create a human-readable, order-invariant filename for a comparison.

        Format: source1-vs-source2--query-slug.brief
        Up to 4 sources shown, then +N for additional.
        """
        # Sort for order invariance
        sorted_uris = sorted(uri.strip().rstrip(",;") for uri in uris)
        slugs = [self._short_slug(uri) for uri in sorted_uris]

        # Show up to 4 slugs, then +N
        if len(slugs) <= 4:
            source_part = "-vs-".join(slugs)
        else:
            source_part = "-vs-".join(slugs[:4]) + f"-+{len(slugs) - 4}"

        # Query slug
        query_slug = self._query_slug(query, depth)

        return f"{source_part}--{query_slug}"

    def check_comparison(self, uris: list[str], query: str, depth: int = 1) -> str | None:
        """Check if a comparison has been cached. Returns text or None."""
        comp_dir = self.briefs_dir / "_comparisons"
        key = self._comparison_key(uris, query, depth)
        comp_path = comp_dir / f"{key}.brief"

        if not comp_path.exists():
            return None

        try:
            text = comp_path.read_text(encoding="utf-8")
            logger.debug("Comparison cache hit: %s", key)
            return text
        except OSError:
            return None

    def save_comparison(self, uris: list[str], query: str, depth: int,
                        comparison_text: str) -> Path:
        """Save a comparison result as a .brief file."""
        comp_dir = self.briefs_dir / "_comparisons"
        comp_dir.mkdir(parents=True, exist_ok=True)
        key = self._comparison_key(uris, query, depth)
        comp_path = comp_dir / f"{key}.brief"

        comp_path.write_text(comparison_text, encoding="utf-8")
        logger.info("Saved comparison: _comparisons/%s.brief", key)
        return comp_path


    # ── Backwards compatibility ───────────────────────────────────

    def check(self, uri: str) -> dict[str, Any] | None:
        """Legacy method — checks for source data."""
        return self.check_source(uri)

    def save(self, brief: dict[str, Any], rendered_text: str = "") -> Path:
        """Legacy method — saves source data."""
        return self.save_source(brief)
