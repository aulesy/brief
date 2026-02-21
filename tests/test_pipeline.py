"""Tests for the brief service and extractors."""


def test_video_extract_produces_chunks() -> None:
    from brief.extractors.video import extract

    chunks = extract("https://github.github.com/gh-aw/videos/install-and-add-workflow-in-cli.mp4")
    assert isinstance(chunks, list)
    if chunks:
        assert "text" in chunks[0]
        assert "start_sec" in chunks[0]


def test_brief_creates_and_caches(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from brief.service import brief

    result = brief("https://cdn.example.com/demo-install-guide.mp4", "install", force=True)
    assert "brief created" in result

    result2 = brief("https://cdn.example.com/demo-install-guide.mp4", "install")
    assert "brief found" in result2


def test_brief_depth_levels(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from brief.service import brief

    url = "https://cdn.example.com/demo-tutorial.mp4"
    d0 = brief(url, "test", depth=0)
    d1 = brief(url, "test", depth=1)
    d3 = brief(url, "test", depth=3)
    assert len(d0) <= len(d1) <= len(d3)


def test_captions_chunking() -> None:
    from brief.extractors.video import CaptionSegment, _chunk_from_segments

    segments = [
        CaptionSegment(start_sec=5.2, end_sec=10.5, text="Hello world."),
        CaptionSegment(start_sec=10.5, end_sec=18.3, text="Install the workflow."),
    ]
    chunks = _chunk_from_segments(segments)
    assert len(chunks) == 2
    assert chunks[0]["start_sec"] == 5.2
    assert chunks[1]["start_sec"] == 10.5
    assert "install" in chunks[1]["text"].lower()
