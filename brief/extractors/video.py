"""Video extractor — extract captions/transcript from video URIs.

Supports YouTube and 1700+ sites via yt-dlp.
Fallback chain: captions → local Whisper → API Whisper → slug heuristic.
Returns merged topical chunks with real timestamps.
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


# ── Local Whisper STT (faster-whisper, no API key) ───────────

def _download_audio(media_url: str) -> str | None:
    """Download audio track via yt-dlp to a temp file."""
    yt_dlp_path = shutil.which("yt-dlp")
    if not yt_dlp_path:
        return None

    temp_dir = tempfile.mkdtemp(prefix="brief-audio-")
    output_path = str(Path(temp_dir) / "audio.%(ext)s")
    cmd = [
        yt_dlp_path,
        "-x", "--audio-format", "wav",
        "--audio-quality", "5",
        "--no-warnings", "--quiet",
        "-o", output_path,
        media_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        if result.returncode != 0:
            return None
        # Find the downloaded file
        files = list(Path(temp_dir).glob("audio.*"))
        if files:
            return str(files[0])
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _transcribe_local(media_url: str) -> CaptionResult | None:
    """Transcribe video using local faster-whisper (free, no API key)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.debug("faster-whisper not installed, skipping local STT")
        return None

    audio_path = _download_audio(media_url)
    if not audio_path:
        logger.debug("Could not download audio for local STT")
        return None

    try:
        model_size = os.getenv("BRIEF_WHISPER_MODEL", "base")
        logger.info("Local Whisper transcribing (%s model)...", model_size)
        model = WhisperModel(model_size, device="cpu", compute_type="int8")

        raw_segments, info = model.transcribe(audio_path, beam_size=5)
        logger.info("Detected language: %s (%.0f%% confidence)",
                     info.language, info.language_probability * 100)

        segments: list[CaptionSegment] = []
        texts: list[str] = []
        for seg in raw_segments:
            text = seg.text.strip()
            if text:
                segments.append(CaptionSegment(
                    start_sec=round(seg.start, 3),
                    end_sec=round(seg.end, 3),
                    text=text,
                ))
                texts.append(text)

        if segments:
            return CaptionResult(
                text=" ".join(texts),
                provider="local_whisper",
                segments=segments,
            )
    except Exception as exc:
        logger.warning("Local Whisper failed: %s", exc)
    finally:
        try:
            os.remove(audio_path)
            os.rmdir(str(Path(audio_path).parent))
        except OSError:
            pass
    return None


# ── API STT fallback ─────────────────────────────────────────

def _transcribe_stt(media_url: str) -> CaptionResult | None:
    # Only use a dedicated STT key — LLM providers (OpenRouter etc.) don't support Whisper
    api_key = os.getenv("BRIEF_STT_API_KEY")
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


def _metadata_fallback(media_url: str) -> CaptionResult | None:
    """Extract video title + description via yt-dlp metadata (no download)."""
    yt_dlp_path = shutil.which("yt-dlp")
    if not yt_dlp_path:
        return None

    try:
        import json as _json
        cmd = [
            yt_dlp_path, "--dump-json",
            "--no-warnings", "--quiet",
            "--skip-download",
            media_url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return None

        info = _json.loads(result.stdout)
        title = info.get("title", "")
        description = info.get("description", "")
        tags = info.get("tags", [])
        duration = info.get("duration", 0)

        # Build meaningful text from metadata
        parts = []
        if title:
            parts.append(f"Title: {title}")
        if description:
            # Truncate long descriptions
            desc = description[:2000] if len(description) > 2000 else description
            parts.append(f"Description: {desc}")
        if tags:
            parts.append(f"Tags: {', '.join(tags[:15])}")
        if duration:
            mins = int(duration) // 60
            secs = int(duration) % 60
            parts.append(f"Duration: {mins}:{secs:02d}")

        text = "\n".join(parts)
        if text.strip():
            logger.info("Metadata fallback: title=%s (%d chars)", title[:50], len(text))
            return CaptionResult(text=text, provider="yt_dlp_metadata")

    except Exception as exc:
        logger.debug("Metadata fallback failed: %s", exc)

    return None


def _slug_heuristic(media_url: str) -> CaptionResult | None:
    """Last resort: guess content from URL path."""
    parsed = urlparse(media_url)
    # Skip useless slugs like 'watch'
    stem = parsed.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    tokens = [p for p in stem.replace("_", "-").split("-") if p and p != "watch"]
    if not tokens:
        return None
    return CaptionResult(text=f"This video is about {' '.join(tokens)}.", provider="url_slug_heuristic")


# ── Chunking ─────────────────────────────────────────────────

def _truncate_clean(text: str, max_len: int) -> str:
    """Truncate at word boundary, never mid-word."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:!?") + "..."


def _merge_segments(segments: list[CaptionSegment], window_sec: float = 30.0) -> list[dict[str, Any]]:
    """Merge fine-grained subtitle segments into topical chunks.

    Groups consecutive segments into ~window_sec windows, producing
    ~10-20 meaningful chunks instead of hundreds of subtitle lines.
    Each chunk gets the real start/end timestamps from its segments.
    """
    if not segments:
        return []

    chunks: list[dict[str, Any]] = []
    current_texts: list[str] = []
    window_start = segments[0].start_sec
    window_end = segments[0].end_sec

    for seg in segments:
        # Start a new chunk if we've exceeded the window
        if seg.start_sec - window_start >= window_sec and current_texts:
            merged_text = " ".join(current_texts)
            chunks.append({
                "start_sec": round(window_start, 3),
                "end_sec": round(window_end, 3),
                "text": _truncate_clean(merged_text, 500),
            })
            current_texts = []
            window_start = seg.start_sec

        current_texts.append(seg.text.strip())
        window_end = seg.end_sec

    # Flush remaining
    if current_texts:
        merged_text = " ".join(current_texts)
        chunks.append({
            "start_sec": round(window_start, 3),
            "end_sec": round(window_end, 3),
            "text": _truncate_clean(merged_text, 500),
        })

    logger.info("Merged %d segments into %d chunks (%.0fs windows)",
                len(segments), len(chunks), window_sec)
    return chunks


def _chunk_from_segments(segments: list[CaptionSegment]) -> list[dict[str, Any]]:
    """Convert segments to chunks, merging into topical windows."""
    if len(segments) > 20:
        # Many segments — merge into meaningful chunks
        return _merge_segments(segments)
    # Few segments — keep as-is (already meaningful)
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

    # 2. Try local Whisper (free, no API key)
    result = _transcribe_local(uri)
    if result:
        logger.info("Transcribed via %s (%d segments)", result.provider, len(result.segments))
        if result.segments:
            return _chunk_from_segments(result.segments)
        return _chunk_from_text(result.text)

    # 3. Try API STT
    result = _transcribe_stt(uri)
    if result:
        logger.info("Transcribed via %s", result.provider)
        return _chunk_from_text(result.text)

    # 4. Try video metadata (title + description)
    result = _metadata_fallback(uri)
    if result:
        return _chunk_from_text(result.text)

    # 5. Last resort: URL slug
    result = _slug_heuristic(uri)
    if result:
        return _chunk_from_text(result.text)

    logger.warning("No content extracted from %s", uri)
    return []
