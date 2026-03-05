"""GitHub extractor — fetch repo README, metadata, tree, docstrings, and top issues.

GitHub's public API requires no authentication for public repos
(rate limited to 60 requests/hour without a token, 5000 with).

Extracts: repo metadata, README content, file tree, module docstrings,
recent/top issues, and full file content for /blob/ URLs.
"""

from __future__ import annotations

import ast
import base64
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "Brief/0.5 (content extraction for AI agents)",
}

# Extensions worth scanning for module-level docstrings
_PYTHON_EXTENSIONS = {".py"}
_JS_EXTENSIONS = {".js", ".ts", ".jsx", ".tsx", ".mjs"}
_DOCSTRING_EXTENSIONS = _PYTHON_EXTENSIONS | _JS_EXTENSIONS

# Max files to fetch docstrings from (to stay within rate limits)
_MAX_DOCSTRING_FILES = 10

# Max characters per individual docstring
_MAX_DOCSTRING_CHARS = 300

# Max characters for a full file fetch (/blob/ URLs)
_MAX_FILE_CHARS = 15_000


def _human_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _parse_github_url(uri: str) -> tuple[str, str] | None:
    """Extract owner/repo from a GitHub URL. Returns (owner, repo) or None."""
    # Match: github.com/owner/repo or github.com/owner/repo/...
    m = re.match(r"https?://(?:www\.)?github\.com/([^/]+)/([^/]+)", uri)
    if not m:
        return None
    owner = m.group(1)
    repo = m.group(2).removesuffix(".git")
    return owner, repo


def _extract_python_docstring(source: str) -> str | None:
    """Extract the module-level docstring from Python source code.

    Uses ast.parse for accuracy, falls back to regex for files with syntax errors.
    """
    # Try ast.parse first — most reliable
    try:
        tree = ast.parse(source)
        docstring = ast.get_docstring(tree)
        if docstring:
            return docstring[:_MAX_DOCSTRING_CHARS]
    except SyntaxError:
        pass

    # Fallback: regex for triple-quoted string at top of file
    # Skip leading comments, blank lines, and encoding declarations
    m = re.match(
        r'^(?:\s*#[^\n]*\n)*\s*'          # optional leading comments
        r'(?:from\s+__future__[^\n]*\n)*'  # optional __future__ imports
        r'\s*(?:'
        r'"""(.*?)"""|'                     # double-quote docstring
        r"'''(.*?)''')",                    # single-quote docstring
        source,
        re.DOTALL,
    )
    if m:
        docstring = (m.group(1) or m.group(2) or "").strip()
        if docstring:
            return docstring[:_MAX_DOCSTRING_CHARS]

    return None


def _extract_js_docstring(source: str) -> str | None:
    """Extract the top-of-file JSDoc block from JS/TS source code.

    Looks for a /** ... */ comment at the very beginning of the file
    (optionally preceded by hashbang, 'use strict', or blank lines).
    """
    m = re.match(
        r'^(?:#![^\n]*\n)?'                # optional hashbang
        r'(?:\s*(?://[^\n]*\n)*)?'          # optional single-line comments
        r"(?:\s*['\"]use strict['\"];?\s*)?" # optional 'use strict'
        r'\s*/\*\*(.*?)\*/',                # the JSDoc block
        source,
        re.DOTALL,
    )
    if not m:
        return None

    raw = m.group(1)
    # Clean up JSDoc: strip leading * from each line
    lines = []
    for line in raw.split("\n"):
        cleaned = re.sub(r'^\s*\*\s?', '', line).strip()
        # Skip @tags — we only want the description
        if cleaned.startswith("@"):
            break
        if cleaned:
            lines.append(cleaned)

    docstring = " ".join(lines).strip()
    if docstring:
        return docstring[:_MAX_DOCSTRING_CHARS]
    return None


def _parse_blob_url(uri: str) -> tuple[str, str, str, str] | None:
    """Parse a /blob/ URL into (owner, repo, branch, filepath).

    Example: github.com/psf/requests/blob/main/src/requests/api.py
    Returns: ("psf", "requests", "main", "src/requests/api.py")
    """
    m = re.match(
        r"https?://(?:www\.)?github\.com/"
        r"([^/]+)/([^/]+)/blob/([^/]+)/(.+)",
        uri,
    )
    if not m:
        return None
    owner = m.group(1)
    repo = m.group(2).removesuffix(".git")
    branch = m.group(3)
    filepath = m.group(4)
    return owner, repo, branch, filepath


def _get_file_extension(path: str) -> str:
    """Get the lowercase extension of a file path."""
    idx = path.rfind(".")
    if idx == -1:
        return ""
    return path[idx:].lower()


def _prioritize_files(
    file_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sort and filter files to prioritize the most informative ones for docstrings.

    Priority: __init__.py > top-level modules > nested files.
    Only includes files with docstring-compatible extensions.
    """
    candidates = []
    for item in file_items:
        path = item.get("path", item.get("name", ""))
        ext = _get_file_extension(path)
        if ext not in _DOCSTRING_EXTENSIONS:
            continue
        # Skip test files, configs, and generated files
        basename = path.rsplit("/", 1)[-1] if "/" in path else path
        if basename.startswith("test_") or basename.startswith("conftest"):
            continue
        if basename in ("setup.py", "conftest.py", "manage.py"):
            continue

        # Priority score: lower = higher priority
        depth = path.count("/")
        if basename == "__init__.py":
            priority = depth  # package inits first, by depth
        elif basename in ("__main__.py", "app.py", "main.py", "cli.py", "index.js", "index.ts"):
            priority = depth + 0.5  # entry points next
        else:
            priority = depth + 1 + (0 if ext in _PYTHON_EXTENSIONS else 0.1)

        candidates.append((priority, path, item))

    candidates.sort(key=lambda x: x[0])
    return [item for _, _, item in candidates[:_MAX_DOCSTRING_FILES]]


def _extract_blob_file(uri: str) -> list[dict[str, Any]]:
    """Extract full file content from a /blob/ URL."""
    blob = _parse_blob_url(uri)
    if not blob:
        return []

    try:
        import httpx
    except ImportError:
        logger.error("httpx is required for GitHub extraction.")
        return []

    owner, repo, branch, filepath = blob
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{filepath}"

    headers = dict(_HEADERS)
    try:
        from .. import config
        token = config.get("GITHUB_TOKEN") or config.get("BRIEF_GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception:
        pass

    try:
        resp = httpx.get(api_url, headers=headers, params={"ref": branch}, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        content = data.get("content", "")
        encoding = data.get("encoding", "base64")

        if content and encoding == "base64":
            file_text = base64.b64decode(content).decode("utf-8", errors="replace")
            if len(file_text) > _MAX_FILE_CHARS:
                file_text = file_text[:_MAX_FILE_CHARS] + "\n\n[File truncated]"

            file_size = data.get("size", len(file_text))
            header = f"{filepath} ({_human_size(file_size)}) — branch: {branch}"

            return [{
                "text": f"{header}\n\n{file_text}",
                "start_sec": 0.0,
            }]
    except Exception as exc:
        logger.error("Could not fetch file %s: %s", filepath, exc)

    return []


def extract(uri: str) -> list[dict[str, Any]]:
    """Extract repo content from a GitHub URL.

    For /blob/ URLs: fetches the specific file content via GitHub API.
    For repo-level URLs: uses GitHub API (metadata, README, tree, docstrings, issues).
    For sub-page URLs (discussions, specific issues, PRs, wiki): falls back
    to webpage extraction since the GitHub API can't fetch those.
    """
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]

    # ── /blob/ URLs: fetch specific file content ──
    if len(path_parts) > 3 and path_parts[2].lower() == "blob":
        logger.info("GitHub /blob/ URL detected, fetching file content")
        return _extract_blob_file(uri)

    # Sub-page URLs the GitHub API can't handle — fall back to webpage
    # e.g. /owner/repo/discussions/1944, /owner/repo/issues/123, /owner/repo/pull/456
    if len(path_parts) > 2:
        sub = path_parts[2].lower()
        if sub in ("discussions", "issues", "pull", "wiki", "actions", "security", "releases"):
            logger.info("GitHub sub-page detected (%s), falling back to webpage extractor", sub)
            from .webpage import extract as extract_webpage
            return extract_webpage(uri)

    try:
        import httpx
    except ImportError:
        logger.error("httpx is required for GitHub extraction.")
        return []

    parsed = _parse_github_url(uri)
    if not parsed:
        logger.error("Could not parse GitHub URL: %s", uri)
        return []

    owner, repo = parsed
    api_base = f"https://api.github.com/repos/{owner}/{repo}"

    # Build headers per call — don't mutate module-level dict (race condition in batch)
    headers = dict(_HEADERS)
    try:
        from .. import config
        token = config.get("GITHUB_TOKEN") or config.get("BRIEF_GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception:
        pass

    chunks: list[dict[str, Any]] = []

    # ── Repo metadata ──
    try:
        resp = httpx.get(api_base, headers=headers, timeout=15)
        resp.raise_for_status()
        meta = resp.json()

        info_parts = [
            f"{meta.get('full_name', f'{owner}/{repo}')}",
            f"{meta.get('description') or 'No description'}",
            "",
            f"Stars: {meta.get('stargazers_count', 0):,} | "
            f"Forks: {meta.get('forks_count', 0):,} | "
            f"Open issues: {meta.get('open_issues_count', 0):,}",
            f"Language: {meta.get('language', 'Unknown')} | "
            f"License: {(meta.get('license') or {}).get('spdx_id', 'None')}",
            f"Last updated: {meta.get('updated_at', 'unknown')[:10]}",
        ]

        topics = meta.get("topics", [])
        if topics:
            info_parts.append(f"Topics: {', '.join(topics[:10])}")

        chunks.append({
            "text": "\n".join(info_parts),
            "start_sec": 0.0,
        })
    except Exception as exc:
        logger.error("GitHub API failed for %s: %s", api_base, exc)
        return []

    # ── README ──
    try:
        resp = httpx.get(f"{api_base}/readme", headers=headers, timeout=15)
        if resp.status_code == 200:
            readme_data = resp.json()
            content = readme_data.get("content", "")
            encoding = readme_data.get("encoding", "base64")

            if content and encoding == "base64":
                readme_text = base64.b64decode(content).decode("utf-8", errors="replace")
                # Strip HTML tags if present
                readme_text = re.sub(r"<[^>]+>", "", readme_text)
                # Truncate very long READMEs
                if len(readme_text) > 8000:
                    readme_text = readme_text[:8000] + "\n\n[README truncated]"

                chunks.append({
                    "text": readme_text,
                    "start_sec": 1.0,
                })
    except Exception as exc:
        logger.debug("Could not fetch README: %s", exc)

    # ── File tree ──
    # We also collect file items here for docstring extraction below
    all_file_items: list[dict[str, Any]] = []
    try:
        resp = httpx.get(
            f"{api_base}/contents",
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            contents = resp.json()
            tree_lines = ["Repository structure:"]

            for item in sorted(contents, key=lambda x: (x.get("type") != "dir", x.get("name", ""))):
                name = item.get("name", "")
                item_type = item.get("type", "")
                size = item.get("size", 0)

                if item_type == "dir":
                    tree_lines.append(f"  {name}/")
                    # Fetch one level deeper for directories
                    try:
                        sub_resp = httpx.get(
                            f"{api_base}/contents/{name}",
                            headers=headers,
                            timeout=10,
                        )
                        if sub_resp.status_code == 200:
                            sub_contents = sub_resp.json()
                            for sub in sorted(sub_contents, key=lambda x: x.get("name", ""))[:15]:
                                sub_name = sub.get("name", "")
                                sub_type = sub.get("type", "")
                                sub_size = sub.get("size", 0)
                                if sub_type == "dir":
                                    tree_lines.append(f"    {sub_name}/")
                                else:
                                    tree_lines.append(f"    {sub_name} ({_human_size(sub_size)})")
                                    # Collect file items for docstring extraction
                                    all_file_items.append({
                                        "name": sub_name,
                                        "path": f"{name}/{sub_name}",
                                        "size": sub_size,
                                    })
                    except Exception:
                        pass
                else:
                    tree_lines.append(f"  {name} ({_human_size(size)})")
                    all_file_items.append({
                        "name": name,
                        "path": name,
                        "size": size,
                    })

            chunks.append({
                "text": "\n".join(tree_lines),
                "start_sec": 1.5,
            })
    except Exception as exc:
        logger.debug("Could not fetch file tree: %s", exc)

    # ── Module docstrings ──
    # Fetch top docstrings from key files to give a semantic overview
    try:
        candidates = _prioritize_files(all_file_items)
        if candidates:
            docstring_lines = ["Module docstrings:"]
            fetched = 0
            for item in candidates:
                path = item.get("path", "")
                ext = _get_file_extension(path)
                try:
                    file_resp = httpx.get(
                        f"{api_base}/contents/{path}",
                        headers=headers,
                        timeout=10,
                    )
                    if file_resp.status_code == 200:
                        file_data = file_resp.json()
                        file_content = file_data.get("content", "")
                        file_encoding = file_data.get("encoding", "base64")

                        if file_content and file_encoding == "base64":
                            source = base64.b64decode(file_content).decode("utf-8", errors="replace")

                            docstring = None
                            if ext in _PYTHON_EXTENSIONS:
                                docstring = _extract_python_docstring(source)
                            elif ext in _JS_EXTENSIONS:
                                docstring = _extract_js_docstring(source)

                            if docstring:
                                # Take just the first sentence for a clean summary
                                first_line = docstring.split("\n\n")[0].strip()
                                first_line = " ".join(first_line.split())  # normalize whitespace
                                if len(first_line) > 120:
                                    first_line = first_line[:117] + "..."
                                docstring_lines.append(f"  {path}: {first_line}")
                                fetched += 1
                    elif file_resp.status_code == 403:
                        logger.warning("Rate limited while fetching docstrings, stopping")
                        break
                except Exception as exc:
                    logger.debug("Could not fetch %s for docstring: %s", path, exc)

            if fetched > 0:
                chunks.append({
                    "text": "\n".join(docstring_lines),
                    "start_sec": 1.75,
                })
                logger.info("Extracted docstrings from %d files", fetched)
    except Exception as exc:
        logger.debug("Could not extract docstrings: %s", exc)

    # ── Top issues (recent, open) ──
    try:
        resp = httpx.get(
            f"{api_base}/issues",
            headers=headers,
            params={"state": "open", "sort": "updated", "per_page": 10},
            timeout=15,
        )
        if resp.status_code == 200:
            issues = resp.json()
            issue_texts = []
            for issue in issues[:10]:
                if issue.get("pull_request"):
                    continue  # Skip PRs
                title = issue.get("title", "")
                body = (issue.get("body") or "")[:300]
                labels = ", ".join(l["name"] for l in issue.get("labels", []))
                comments = issue.get("comments", 0)
                line = f"#{issue['number']} {title}"
                if labels:
                    line += f" [{labels}]"
                line += f" ({comments} comments)"
                if body:
                    line += f"\n  {body}"
                issue_texts.append(line)

            if issue_texts:
                chunks.append({
                    "text": "Recent open issues:\n" + "\n\n".join(issue_texts),
                    "start_sec": 2.0,
                })
    except Exception as exc:
        logger.debug("Could not fetch issues: %s", exc)

    logger.info("Extracted %d chunks from GitHub: %s/%s", len(chunks), owner, repo)
    return chunks


# ── Query-driven code fetching ──────────────────────────────────

# Files to always skip in query matching
_SKIP_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "go.sum", "Cargo.lock", "Gemfile.lock", "composer.lock",
    ".gitignore", ".eslintrc", ".prettierrc", "tsconfig.json",
    "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md",
}

_SKIP_PREFIXES = ("test_", "spec.", ".test.", "_test.", "mock_", "fixture_")

# Common stopwords to ignore when matching query against filenames
_STOPWORDS = {
    "what", "how", "does", "the", "is", "it", "a", "an", "and", "or", "of",
    "in", "to", "for", "this", "that", "with", "use", "work", "handle",
    "are", "do", "its", "by", "from", "on", "at", "be", "was", "were",
}


def _match_files_to_query(query: str, file_tree_text: str, max_files: int = 5) -> list[str]:
    """Find files in the tree whose names/paths match query keywords.

    Returns a list of file paths sorted by relevance (best match first).
    """
    # Extract keywords from query
    words = set(query.lower().split()) - _STOPWORDS
    if not words:
        return []

    # Parse file paths from the tree text
    # Format: "    filename.ext (1.2 KB)"  or  "  dir/"
    file_paths: list[str] = []
    current_dirs: list[str] = []

    for line in file_tree_text.split("\n"):
        stripped = line.rstrip()
        if not stripped or stripped.startswith("Repository structure:"):
            continue

        # Determine depth from indentation
        indent = len(stripped) - len(stripped.lstrip())
        name = stripped.strip()

        if name.endswith("/"):
            # Directory
            dir_depth = indent // 2
            current_dirs = current_dirs[:dir_depth] + [name.rstrip("/")]
        elif "(" in name and "B)" in name:
            # File with size: "filename.ext (1.2 KB)"
            fname = name.split(" (")[0].strip()
            dir_depth = indent // 2
            dirs = current_dirs[:dir_depth]
            full_path = "/".join(dirs + [fname]) if dirs else fname
            file_paths.append(full_path)

    # Score each file by keyword match
    scored: list[tuple[float, str]] = []
    for path in file_paths:
        basename = path.rsplit("/", 1)[-1] if "/" in path else path

        # Skip unwanted files
        if basename in _SKIP_FILENAMES:
            continue
        basename_lower = basename.lower()
        if any(p in basename_lower for p in (".test.", ".spec.", "test_", "_test.", "mock_", "fixture_")):
            continue
        if basename.endswith((".lock", ".sum", ".map", ".min.js", ".min.css")):
            continue

        # Score: how many query keyword stems appear in the path?
        # Use crude stemming (first 4+ chars) so 'caching' matches 'cache'
        path_lower = path.lower()
        hits = 0
        for w in words:
            stem = w[:4] if len(w) > 4 else w  # 'caching' → 'cach', 'api' → 'api'
            if stem in path_lower:
                hits += 1
        if hits == 0:
            continue

        # Prefer files in src/lib/app/core over examples/docs
        depth = path.count("/")
        score = hits + (0.1 if depth <= 2 else 0)

        scored.append((score, path))

    scored.sort(key=lambda x: -x[0])
    return [path for _, path in scored[:max_files]]


def fetch_query_files(
    uri: str,
    query: str,
    file_tree_text: str,
    cache_dir: str | None = None,
    max_file_bytes: int = 4096,
) -> list[dict[str, Any]]:
    """Fetch source files relevant to a query, with local caching.

    This is Brief's differentiator: when you ask about caching,
    Brief reads cache-purge.js — not just the README.

    Args:
        uri: GitHub repo URL
        query: The user's question
        file_tree_text: The file tree chunk text from _source.json
        cache_dir: Path to URL's .briefs/ subdirectory (for _files/ cache)
        max_file_bytes: Max bytes per file (truncated)

    Returns:
        List of chunk dicts with start_sec=1.8, or empty list if no matches.
    """
    import httpx
    from pathlib import Path

    # Parse owner/repo from URI
    match = re.match(r"https?://github\.com/([^/]+)/([^/?#]+)", uri)
    if not match:
        return []

    owner, repo = match.group(1), match.group(2).rstrip("/")

    # Find relevant files
    paths = _match_files_to_query(query, file_tree_text)
    if not paths:
        logger.debug("No query-relevant files found for '%s'", query)
        return []

    logger.info("Query-relevant files for '%s': %s", query, paths)

    # Setup auth headers
    import os
    headers = dict(_HEADERS)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("BRIEF_GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"

    # Setup file cache directory
    files_dir = None
    if cache_dir:
        files_dir = Path(cache_dir) / "_files"
        files_dir.mkdir(exist_ok=True)

    api_base = f"https://api.github.com/repos/{owner}/{repo}/contents"
    code_lines = []
    fetched = 0

    for path in paths:
        # Check local cache first
        cache_key = path.replace("/", "--")
        if files_dir:
            cached_file = files_dir / cache_key
            if cached_file.exists():
                try:
                    content = cached_file.read_text(encoding="utf-8")
                    code_lines.append(f"─── {path} ───\n{content}")
                    fetched += 1
                    continue
                except OSError:
                    pass

        # Fetch from GitHub API
        try:
            resp = httpx.get(
                f"{api_base}/{path}",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("content", "")
                encoding = data.get("encoding", "base64")
                if raw and encoding == "base64":
                    source = base64.b64decode(raw).decode("utf-8", errors="replace")

                    # Truncate if needed
                    truncated = False
                    if len(source) > max_file_bytes:
                        source = source[:max_file_bytes]
                        truncated = True

                    # Cache locally
                    if files_dir:
                        try:
                            (files_dir / cache_key).write_text(source, encoding="utf-8")
                        except OSError:
                            pass

                    label = f"─── {path} ───"
                    if truncated:
                        label += f"\n[truncated at {max_file_bytes} bytes]"
                    code_lines.append(f"{label}\n{source}")
                    fetched += 1

            elif resp.status_code == 403:
                logger.warning("Rate limited during query file fetch, stopping")
                break
        except Exception as exc:
            logger.debug("Could not fetch %s: %s", path, exc)

    if not code_lines:
        return []

    chunk_text = "Query-relevant source files:\n\n" + "\n\n".join(code_lines)
    logger.info("Fetched %d query-relevant files for '%s'", fetched, query)

    return [{
        "text": chunk_text,
        "start_sec": 1.8,
    }]

