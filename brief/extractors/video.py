"""Video extractor — extract captions/transcript from video URIs.

Supports YouTube and 1700+ sites via yt-dlp, with OpenAI STT fallback.
Returns chunks with real timestamps when available.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────

@dataclass(slots=True)
class CaptionSegment:
    start_sec: float
    end_sec: float
    text: str


@dataclass(slots=True)
class CaptionResult:
    text: str
    provider: str
    segments: list[CaptionSegment] = field(default_factory=list)


# ── VTT parsing ──────────────────────────────────────────────

_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?\.(\d{3})\s+-->\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\.(\d{3})"
)


def _ts_to_sec(h_or_m: str, m_or_s: str, s: str | None, ms: str) -> float:
    if s is not None:
        return int(h_or_m) * 3600 + int(m_or_s) * 60 + int(s) + int(ms) / 1000
    return int(h_or_m) * 60 + int(m_or_s) + int(ms) / 1000


def _parse_vtt(raw_text: str) -> tuple[str, list[CaptionSegment]]:
    segments: list[CaptionSegment] = []
    flat_lines: list[str] = []
    previous = ""
    current_start: float | None = None
    current_end: float | None = None
    current_lines: list[str] = []

    for original in raw_text.splitlines():
        line = re.sub(r"<[^>]+>", "", original).strip()
        if not line or line.upper().startswith("WEBVTT") or line.isdigit():
            continue
        if line.lower().startswith("kind:") or line.lower().startswith("language:"):
            continue
        if re.fullmatch(r"[\[\(♪♫\s\]]+", line):
            continue

        ts_match = _TIMESTAMP_RE.match(line)
        if ts_match:
            if current_start is not None and current_lines:
                segments.append(CaptionSegment(
                    start_sec=round(current_start, 3),
                    end_sec=round(current_end, 3),
                    text=" ".join(current_lines),
                ))
                current_lines = []
            g = ts_match.groups()
            current_start = _ts_to_sec(g[0], g[1], g[2], g[3])
            current_end = _ts_to_sec(g[4], g[5], g[6], g[7])
            continue

        if line == previous:
            continue
        current_lines.append(line)
        flat_lines.append(line)
        previous = line

    if current_start is not None and current_lines:
        segments.append(CaptionSegment(
            start_sec=round(current_start, 3),
            end_sec=round(current_end, 3),
            text=" ".join(current_lines),
        ))

    return " ".join(flat_lines).strip(), segments


# ── yt-dlp captions ──────────────────────────────────────────

def _get_captions(media_url: str) -> CaptionResult | None:
    yt_dlp_path = shutil.which("yt-dlp")
    if not yt_dlp_path:
        return None

    with tempfile.TemporaryDirectory(prefix="brief-subs-") as temp_dir:
        output_template = str(Path(temp_dir) / "%(id)s.%(ext)s")

        def try_strategy(auto: bool) -> CaptionResult | None:
            cmd = [
                yt_dlp_path, "--skip-download",
                "--sub-format", "vtt", "--sub-langs", "en.*",
                "--no-warnings", "--quiet",
                "-o", output_template,
                "--write-auto-subs" if auto else "--write-subs",
                media_url,
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
            except (OSError, subprocess.SubprocessError):
                return None
            if result.returncode != 0:
                return None

            files = sorted(Path(temp_dir).glob("*.vtt")) or sorted(Path(temp_dir).glob("*.srt"))
            if not files:
                return None
            text, segments = _parse_vtt(files[0].read_text(encoding="utf-8", errors="replace"))
            if not text:
                return None
            provider = "yt_dlp_auto_captions" if auto else "yt_dlp_manual_captions"
            return CaptionResult(text=text, provider=provider, segments=segments)

        return try_strategy(auto=False) or try_strategy(auto=True)


# ── STT fallback ─────────────────────────────────────────────

def _transcribe_stt(media_url: str) -> CaptionResult | None:
    # Use dedicated STT key to avoid picking up unrelated API keys
    api_key = os.getenv("BRIEF_STT_API_KEY") or os.getenv("BRIEF_LLM_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    # Download media file
    parsed = urlparse(media_url)
    suffix = "." + parsed.path.rsplit(".", 1)[-1][:8] if "." in parsed.path else ".mp4"
    try:
        req = Request(media_url, headers={"User-Agent": "brief/0.3"})
        with urlopen(req, timeout=30) as resp, tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            total = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 50 * 1024 * 1024:
                    os.remove(f.name)
                    return None
                f.write(chunk)
            file_path = f.name
    except OSError:
        return None

    try:
        client = OpenAI(api_key=api_key)
        with open(file_path, "rb") as media_file:
            response = client.audio.transcriptions.create(
                model=os.getenv("VIDEO_INTEL_STT_MODEL", "gpt-4o-mini-transcribe"),
                file=media_file,
            )
        text = getattr(response, "text", None)
        if text and isinstance(text, str):
            return CaptionResult(text=text.strip(), provider="openai_stt")
    except Exception as exc:
        logger.warning("STT failed: %s", exc)
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass
    return None


def _slug_heuristic(media_url: str) -> CaptionResult | None:
    stem = urlparse(media_url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    tokens = [p for p in stem.replace("_", "-").split("-") if p]
    if not tokens:
        return None
    return CaptionResult(text=f"This video is about {' '.join(tokens)}.", provider="url_slug_heuristic")


# ── Chunking ─────────────────────────────────────────────────

def _chunk_from_segments(segments: list[CaptionSegment]) -> list[dict[str, Any]]:
    return [
        {"start_sec": s.start_sec, "end_sec": s.end_sec, "text": s.text.strip()}
        for s in segments if s.text.strip()
    ]


def _chunk_from_text(text: str) -> list[dict[str, Any]]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if not sentences:
        sentences = [text.strip()]
    duration = max(60, len(sentences) * 30)
    step = duration / len(sentences)
    chunks = []
    t = 0.0
    for s in sentences:
        chunks.append({"start_sec": round(t, 3), "end_sec": round(t + step, 3), "text": s})
        t += step
    return chunks


# ── Entry point ──────────────────────────────────────────────

def extract(uri: str) -> list[dict[str, Any]]:
    """Extract content chunks from a video URI."""
    # 1. Try captions (real timestamps)
    result = _get_captions(uri)
    if result:
        logger.info("Captions via %s (%d segments)", result.provider, len(result.segments))
        if result.segments:
            return _chunk_from_segments(result.segments)
        return _chunk_from_text(result.text)

    # 2. Try STT
    result = _transcribe_stt(uri)
    if result:
        logger.info("Transcribed via %s", result.provider)
        return _chunk_from_text(result.text)

    # 3. Slug heuristic fallback
    result = _slug_heuristic(uri)
    if result:
        return _chunk_from_text(result.text)

    logger.warning("No content extracted from %s", uri)
    return []
