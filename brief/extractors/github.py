"""GitHub extractor — fetch repo README, metadata, and top issues via GitHub's API.

GitHub's public API requires no authentication for public repos
(rate limited to 60 requests/hour without a token, 5000 with).

Extracts: repo metadata, README content, and recent/top issues.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "Brief/0.4 (content extraction for AI agents)",
}


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


def extract(uri: str) -> list[dict[str, Any]]:
    """Extract repo content from a GitHub URL.

    For repo-level URLs: uses GitHub API (metadata, README, tree, issues).
    For sub-page URLs (discussions, specific issues, PRs, wiki): falls back
    to webpage extraction since the GitHub API can't fetch those.
    """
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]

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

    # Check for optional GitHub token for higher rate limits
    try:
        from .. import config
        token = config.get("GITHUB_TOKEN") or config.get("BRIEF_GITHUB_TOKEN")
        if token:
            _HEADERS["Authorization"] = f"Bearer {token}"
    except Exception:
        pass

    chunks: list[dict[str, Any]] = []

    # ── Repo metadata ──
    try:
        resp = httpx.get(api_base, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        meta = resp.json()

        info_parts = [
            f"{meta.get('full_name', f'{owner}/{repo}')}",
            f"{meta.get('description', 'No description')}",
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
        resp = httpx.get(f"{api_base}/readme", headers=_HEADERS, timeout=15)
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
    try:
        resp = httpx.get(
            f"{api_base}/contents",
            headers=_HEADERS,
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
                            headers=_HEADERS,
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
                    except Exception:
                        pass
                else:
                    tree_lines.append(f"  {name} ({_human_size(size)})")

            chunks.append({
                "text": "\n".join(tree_lines),
                "start_sec": 1.5,
            })
    except Exception as exc:
        logger.debug("Could not fetch file tree: %s", exc)

    # ── Top issues (recent, open) ──
    try:
        resp = httpx.get(
            f"{api_base}/issues",
            headers=_HEADERS,
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
