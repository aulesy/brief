"""Brief store — file-based .briefs/ folder + SQLite index.

Every brief is saved as two files:
  .briefs/{slug}.brief.json  — structured data (for programmatic access)
  .briefs/{slug}.brief      — agent-readable text (the .brief format)

SQLite index for fast URI→file lookups.
Agents can also just browse the .briefs/ folder.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_BRIEFS_DIR = Path.cwd() / ".briefs"
_DEFAULT_DB = _DEFAULT_BRIEFS_DIR / "_index.sqlite3"


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
                CREATE TABLE IF NOT EXISTS brief_index (
                    uri_hash TEXT PRIMARY KEY,
                    uri TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    created TEXT NOT NULL
                )
                """
            )
            conn.commit()

    @staticmethod
    def _uri_hash(uri: str) -> str:
        return hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _slugify(uri: str) -> str:
        """Create a short readable filename from a URI."""
        from urllib.parse import urlparse

        parsed = urlparse(uri)
        host = (parsed.hostname or "unknown").replace("www.", "")
        path = parsed.path.strip("/").replace("/", "-")
        slug = f"{host}_{path}" if path else host
        # Clean up
        slug = slug.replace(".", "-").replace(" ", "-").lower()
        # Truncate
        return slug[:60]

    def check(self, uri: str) -> dict[str, Any] | None:
        """Look up a cached brief by URI. Returns brief dict or None."""
        key = self._uri_hash(uri)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT filename FROM brief_index WHERE uri_hash = ?", (key,)
            ).fetchone()
        if not row:
            return None

        json_path = self.briefs_dir / row[0]
        if not json_path.exists():
            return None

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            logger.debug("Brief cache hit: %s", row[0])
            return data
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, brief: dict[str, Any], rendered_text: str = "") -> Path:
        """Save a brief as .brief.json + .brief files. Returns JSON path."""
        uri = brief.get("source", {}).get("uri", "")
        if not uri:
            logger.warning("Cannot save brief: no source URI.")
            return self.briefs_dir

        slug = self._slugify(uri)
        key = self._uri_hash(uri)

        json_name = f"{slug}.brief.json"
        txt_name = f"{slug}.brief"

        json_path = self.briefs_dir / json_name
        txt_path = self.briefs_dir / txt_name

        # Write structured JSON
        json_path.write_text(
            json.dumps(brief, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Write rendered text (agent-readable)
        if rendered_text:
            txt_path.write_text(rendered_text, encoding="utf-8")

        # Index for fast lookups
        created = brief.get("created", "")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO brief_index (uri_hash, uri, filename, created) VALUES (?, ?, ?, ?)",
                (key, uri, json_name, created),
            )
            conn.commit()

        logger.info("Saved brief: %s", json_name)
        return json_path

    def list_all(self) -> list[dict[str, str]]:
        """List all briefs in the folder."""
        results = []
        for f in sorted(self.briefs_dir.glob("*.brief.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append({
                    "file": f.name,
                    "uri": data.get("source", {}).get("uri", ""),
                    "summary": data.get("summary", "")[:100],
                    "type": data.get("source", {}).get("type", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return results
