"""Extractor registry — routes URIs to the right content extractor."""

from __future__ import annotations

from urllib.parse import urlparse

MEDIA_EXTENSIONS = {".mp4", ".webm", ".m3u8", ".mpd", ".mov", ".avi", ".mkv"}
VIDEO_HOSTS = {"youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "dailymotion.com"}


def detect_type(uri: str) -> str:
    """Detect content type from URI. Returns 'video', 'webpage', 'pdf', etc."""
    parsed = urlparse(uri)
    path_lower = parsed.path.lower()

    # Check file extension
    for ext in MEDIA_EXTENSIONS:
        if path_lower.endswith(ext):
            return "video"

    if path_lower.endswith(".pdf"):
        return "pdf"

    # Check known video hosts
    host = parsed.hostname or ""
    if any(vh in host for vh in VIDEO_HOSTS):
        return "video"

    # Default: treat as webpage
    return "webpage"
