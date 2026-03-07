"""Local path extractor — read files and directories from disk.

Handles both single files and project directories.
For directories: builds a file tree + reads code files as chunks.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Extensions we consider "code" — readable text files worth briefing
CODE_EXTENSIONS = {
    ".py", ".ts", ".js", ".jsx", ".tsx", ".go", ".rs", ".rb", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
    ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".graphql", ".proto",
    ".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".conf",
    ".md", ".rst", ".txt",
    ".html", ".css", ".scss", ".less",
    ".dockerfile", ".env", ".gitignore",
}

# Directories to always skip
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".next", ".nuxt", "target", "bin", "obj",
    ".egg-info", "eggs", "*.egg-info",
    ".briefs", ".agents", ".agent",
}

# Max file size to read (skip large generated files)
MAX_FILE_BYTES = 50_000  # 50KB
MAX_FILES = 50


def _should_skip_dir(name: str) -> bool:
    """Check if a directory should be skipped."""
    return name in SKIP_DIRS or name.endswith(".egg-info")


def _is_code_file(path: Path) -> bool:
    """Check if a file is a code file worth reading."""
    # Check extension
    if path.suffix.lower() in CODE_EXTENSIONS:
        return True
    # Extensionless files that are commonly important
    if path.name.lower() in {"makefile", "dockerfile", "procfile", "gemfile", "rakefile"}:
        return True
    return False


def _build_tree(root: Path, max_depth: int = 4) -> str:
    """Build a simple file tree string."""
    lines = [f"Project structure: {root.name}/"]

    def _walk(path: Path, prefix: str, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return

        dirs = [e for e in entries if e.is_dir() and not _should_skip_dir(e.name)]
        files = [e for e in entries if e.is_file()]

        for i, entry in enumerate(dirs + files):
            is_last = i == len(dirs) + len(files) - 1
            connector = "└── " if is_last else "├── "
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                extension = "    " if is_last else "│   "
                _walk(entry, prefix + extension, depth + 1)
            else:
                size = entry.stat().st_size
                if size > 1024:
                    size_str = f" ({size // 1024}kb)"
                else:
                    size_str = ""
                lines.append(f"{prefix}{connector}{entry.name}{size_str}")

    _walk(root, "", 0)
    return "\n".join(lines)


def _walk_code_files(root: Path) -> list[Path]:
    """Walk a directory and return code files, respecting limits."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Filter out skip dirs in-place (prevents os.walk from descending)
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]

        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            if not _is_code_file(fpath):
                continue
            try:
                if fpath.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            files.append(fpath)
            if len(files) >= MAX_FILES:
                return files
    return files


def extract(uri: str) -> list[dict[str, Any]]:
    """Extract content from a local file or directory."""
    path = Path(uri).resolve()

    if path.is_file():
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            return [{"text": f"── {path.name} ──\n{content}", "start_sec": 0}]
        except OSError:
            return []

    if path.is_dir():
        chunks: list[dict[str, Any]] = []

        # File tree overview
        tree = _build_tree(path)
        chunks.append({"text": tree, "start_sec": 0})

        # Read code files + collect docstrings
        docstring_lines = ["Module docstrings:"]
        for fpath in _walk_code_files(path):
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                rel = fpath.relative_to(path)
                chunks.append({
                    "text": f"── {rel} ──\n{content}",
                    "start_sec": 0,
                })

                # Extract module-level docstring (same as GitHub extractor)
                docstring = _extract_module_docstring(fpath, content)
                if docstring:
                    first_line = docstring.split("\n\n")[0].strip()
                    if len(first_line) > 200:
                        first_line = first_line[:200] + "…"
                    docstring_lines.append(f"  {rel}: {first_line}")
            except (OSError, UnicodeDecodeError):
                continue

        # Add docstrings chunk if we found any
        if len(docstring_lines) > 1:
            chunks.append({
                "text": "\n".join(docstring_lines),
                "start_sec": 0,
            })

        return chunks

    return []


# ── Module docstring extraction ─────────────────────────────────

_PYTHON_EXTENSIONS = {".py"}
_JS_EXTENSIONS = {".js", ".ts", ".jsx", ".tsx", ".mjs"}


def _extract_module_docstring(fpath: Path, content: str) -> str | None:
    """Extract the module-level docstring from a source file.

    Supports Python (ast.parse + regex fallback) and JS/TS (JSDoc).
    """
    import ast
    import re

    suffix = fpath.suffix.lower()

    if suffix in _PYTHON_EXTENSIONS:
        # Try ast.parse first
        try:
            tree = ast.parse(content)
            ds = ast.get_docstring(tree)
            if ds:
                return ds[:300]
        except SyntaxError:
            pass
        # Regex fallback
        m = re.match(
            r'^(?:\s*#[^\n]*\n)*\s*'
            r'(?:from\s+__future__[^\n]*\n)*'
            r'\s*(?:'
            r'"""(.*?)"""|'
            r"'''(.*?)''')",
            content,
            re.DOTALL,
        )
        if m:
            ds = (m.group(1) or m.group(2) or "").strip()
            if ds:
                return ds[:300]

    elif suffix in _JS_EXTENSIONS:
        m = re.match(
            r'^(?:#![^\n]*\n)?'
            r'(?:\s*(?://[^\n]*\n)*)?'
            r"(?:\s*['\"]use strict['\"];?\s*)?"
            r'\s*/\*\*(.*?)\*/',
            content,
            re.DOTALL,
        )
        if m:
            lines = []
            for line in m.group(1).split("\n"):
                import re as _re
                cleaned = _re.sub(r'^\s*\*\s?', '', line).strip()
                if cleaned.startswith("@"):
                    break
                if cleaned:
                    lines.append(cleaned)
            ds = " ".join(lines).strip()
            if ds:
                return ds[:300]

    return None


# ── Query-driven code fetching ──────────────────────────────────

# Common stopwords to ignore when matching query against filenames
# (matches github.py's _STOPWORDS for consistency)
_STOPWORDS = {
    "what", "how", "does", "the", "is", "it", "a", "an", "and", "or", "of",
    "in", "to", "for", "this", "that", "with", "use", "work", "handle",
    "are", "do", "its", "by", "from", "on", "at", "be", "was", "were",
}

# Files to skip in query matching
_SKIP_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "go.sum", "Cargo.lock", "Gemfile.lock", "composer.lock",
    ".gitignore", ".eslintrc", ".prettierrc", "tsconfig.json",
    "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md",
}

# Common code abbreviations — bidirectional so "auth" matches
# "authentication" and "authentication" matches "auth"
_ABBREVIATIONS: dict[str, list[str]] = {
    "auth":   ["authentication", "authorization", "authenticate"],
    "config": ["configuration", "cfg", "conf"],
    "db":     ["database"],
    "msg":    ["message"],
    "req":    ["request"],
    "res":    ["response"],
    "err":    ["error"],
    "ctx":    ["context"],
    "fn":     ["function"],
    "impl":   ["implementation", "implement"],
    "init":   ["initialize", "initialization"],
    "env":    ["environment"],
    "util":   ["utility", "utilities"],
    "param":  ["parameter"],
    "pkg":    ["package"],
    "src":    ["source"],
    "tmp":    ["temporary"],
    "cmd":    ["command"],
    "dir":    ["directory"],
    "doc":    ["document", "documentation"],
    "sync":   ["synchronize", "synchronous"],
    "gen":    ["generate", "generator"],
    "fmt":    ["format"],
    "conn":   ["connection"],
    "repo":   ["repository"],
    "ref":    ["reference"],
    "idx":    ["index"],
}

# Build reverse map: "authentication" → stems to check for "auth"
_ABBREV_REVERSE: dict[str, set[str]] = {}
for _abbr, _fulls in _ABBREVIATIONS.items():
    for _full in _fulls:
        _ABBREV_REVERSE.setdefault(_full, set()).add(_abbr)
        _ABBREV_REVERSE.setdefault(_abbr, set()).add(_full)


def _expand_to_stems(words: set[str]) -> set[str]:
    """Convert query words to stems, including abbreviation expansion.

    "authentication" → stems for "auth", "authentication", plus 4-char prefix "auth"
    "cfg" → stems for "config", "configuration", "cfg"
    """
    stems = set()
    for w in words:
        # 4-char prefix stem
        stems.add(w[:4] if len(w) > 4 else w)
        # Add the full word too (for short abbreviations like "db", "cfg")
        stems.add(w)
        # Abbreviation expansion
        if w in _ABBREV_REVERSE:
            for alt in _ABBREV_REVERSE[w]:
                stems.add(alt[:4] if len(alt) > 4 else alt)
                stems.add(alt)
    return stems


def fetch_query_files(
    project_path: str,
    query: str,
    file_tree: str,
    cache_dir: str = "",
    docstrings_text: str = "",
) -> list[dict[str, Any]]:
    """Find and read files relevant to a query from a local project.

    Three-phase matching:
    1. Path matching with stemming + abbreviation expansion
    2. Docstring grep (if docstrings available)
    3. Content grep fallback (scans file contents)
    """
    root = Path(project_path).resolve()
    if not root.is_dir():
        return []

    # Extract keywords: remove stopwords, expand via abbreviations
    words = set(query.lower().split()) - _STOPWORDS
    if not words:
        return []
    stems = _expand_to_stems(words)

    # Score all code files by keyword match
    scored: list[tuple[float, Path]] = []
    for fpath in _walk_code_files(root):
        name = fpath.name.lower()

        # Skip test files, lock files, etc.
        if fpath.name in _SKIP_NAMES:
            continue
        if any(p in name for p in (".test.", ".spec.", "test_", "_test.", "mock_", "fixture_")):
            continue
        if name.endswith((".lock", ".sum", ".map", ".min.js", ".min.css")):
            continue

        # Match stems against the relative path
        rel = str(fpath.relative_to(root)).lower()
        hits = sum(1 for s in stems if s in rel)
        if hits == 0:
            continue

        # Bonus for shallow files (more likely to be core modules)
        depth = rel.count(os.sep)
        score = hits + (0.1 if depth <= 2 else 0)
        scored.append((score, fpath))

    if not scored and docstrings_text:
        # Phase 2: search docstrings for stems
        # Same format as GitHub: "  filepath: description"
        docstring_matches = []
        for line in docstrings_text.split("\n"):
            line = line.strip()
            if not line or line.startswith("Module docstrings"):
                continue
            if ": " not in line:
                continue
            dpath, desc = line.split(": ", 1)
            dpath = dpath.strip()
            if not dpath:
                continue
            desc_lower = desc.lower()
            hits = sum(1 for s in stems if s in desc_lower)
            if hits > 0:
                # Find the actual file on disk
                candidate = root / dpath
                if candidate.is_file():
                    scored.append((hits + 0.2, candidate))  # slight bonus over content grep

    if not scored:
        # Fallback: search file CONTENTS for stems.
        # This catches cases like "caching" → store.py where the keyword
        # appears in the code but not the filename. Reading from local disk
        # is cheap, so we can afford to scan all code files.
        for fpath in _walk_code_files(root):
            name = fpath.name.lower()
            if fpath.name in _SKIP_NAMES:
                continue
            if any(p in name for p in (".test.", ".spec.", "test_", "_test.", "mock_", "fixture_")):
                continue

            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                content_lower = content.lower()
                hits = sum(1 for s in stems if s in content_lower)
                if hits > 0:
                    depth = str(fpath.relative_to(root)).count(os.sep)
                    score = hits + (0.1 if depth <= 2 else 0)
                    scored.append((score, fpath))
            except (OSError, UnicodeDecodeError):
                continue

    # Sort by score descending, take top 10
    scored.sort(key=lambda x: -x[0])

    matches: list[dict[str, Any]] = []
    for _, fpath in scored[:10]:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            rel = fpath.relative_to(root)
            matches.append({
                "text": f"── {rel} (query-matched) ──\n{content}",
                "start_sec": 0,
            })
        except (OSError, UnicodeDecodeError):
            continue

    return matches
