"""Tests for the video extractor (captions, VTT parsing)."""

import subprocess
from pathlib import Path

from brief.extractors.video import _get_captions, _parse_vtt, CaptionSegment


def _output_template_to_path(template: str) -> Path:
    rendered = template.replace("%(id)s", "video123").replace("%(ext)s", "vtt")
    return Path(rendered)


def test_get_captions_falls_back_to_auto_after_manual_failure(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []
    output_template = ""

    def fake_which(name: str) -> str | None:
        return "/usr/bin/yt-dlp" if name == "yt-dlp" else None

    def fake_run(command, *, capture_output=False, text=False, timeout=120, check=False):
        calls.append(command)
        nonlocal output_template
        for i, arg in enumerate(command):
            if arg == "-o" and i + 1 < len(command):
                output_template = command[i + 1]
        if "--write-subs" in command:
            return subprocess.CompletedProcess(command, 1, "", "no manual captions")
        output_path = _output_template_to_path(output_template)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nhello world\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("brief.extractors.video.shutil.which", fake_which)
    monkeypatch.setattr("brief.extractors.video.subprocess.run", fake_run)

    result = _get_captions("https://example.com/video.mp4")
    assert result is not None
    assert result.provider == "yt_dlp_auto_captions"
    assert len(calls) == 2
    assert "--write-subs" in calls[0]
    assert "--write-auto-subs" in calls[1]


def test_parse_vtt_preserves_timestamps() -> None:
    vtt = """WEBVTT

00:00:05.200 --> 00:00:10.500
Hello world.

00:00:10.500 --> 00:00:18.300
Install the workflow.
"""
    text, segments = _parse_vtt(vtt)
    assert "Hello world" in text
    assert len(segments) == 2
    assert segments[0].start_sec == 5.2
    assert segments[0].end_sec == 10.5
    assert segments[1].start_sec == 10.5
    assert segments[1].end_sec == 18.3


def test_parse_vtt_handles_short_timestamp_format() -> None:
    vtt = """WEBVTT

00:05.200 --> 00:10.500
Short format timestamps.
"""
    text, segments = _parse_vtt(vtt)
    assert segments[0].start_sec == 5.2
    assert segments[0].end_sec == 10.5
