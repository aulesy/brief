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


def test_structure_chunks_generic_mode() -> None:
    """Without query files, _structure_chunks includes all chunks."""
    from brief.summarizer import _structure_chunks

    base_chunks = [
        {"text": "owner/repo\nSome project\n\nStars: 100 | Forks: 10\nLanguage: Python", "start_sec": 0},
        {"text": "# README\n\nThis is a long readme..." + "x" * 600, "start_sec": 1},
        {"text": "Repository structure:\n  src/\n  README.md", "start_sec": 1.5},
    ]
    result = _structure_chunks(base_chunks)

    # All sections should be present
    assert "METADATA" in result
    assert "README" in result
    assert "REPOSITORY STRUCTURE" in result


def test_structure_chunks_query_file_mode() -> None:
    """With query files, _structure_chunks leads with code and skips README."""
    from brief.summarizer import _structure_chunks

    base_chunks = [
        {"text": "owner/repo\nSome project\n\nStars: 100 | Forks: 10\nLanguage: Python", "start_sec": 0},
        {"text": "# README\n\nThis is a long readme..." + "x" * 600, "start_sec": 1},
        {"text": "Repository structure:\n  src/\n  README.md", "start_sec": 1.5},
        {"text": "Recent open issues:\n#1 Bug report", "start_sec": 2},
    ]
    query_files = [
        {"text": "Query-relevant source files:\n\n─── src/cache.py ───\nclass Cache:\n    pass", "start_sec": 1.8},
    ]
    result = _structure_chunks(base_chunks, query_files=query_files)

    # Source code should come FIRST
    assert result.startswith("Query-relevant source files:")

    # Minimal context should be present
    assert "PROJECT CONTEXT" in result
    assert "Stars:" in result

    # Full README and issues should NOT be in the output
    assert "README" not in result.split("PROJECT CONTEXT")[0]  # no README label
    assert "long readme" not in result
    assert "open issues" not in result.lower()


def test_structure_chunks_query_files_preserves_code_verbatim() -> None:
    """Query file content must appear verbatim — no truncation or filtering."""
    from brief.summarizer import _structure_chunks

    code = "def complex_function():\n    # 100 lines of code\n    return 42\n" * 50
    query_files = [{"text": f"─── src/engine.py ───\n{code}", "start_sec": 1.8}]
    base_chunks = [{"text": "Stars: 1 | Language: Python", "start_sec": 0}]

    result = _structure_chunks(base_chunks, query_files=query_files)
    assert code in result
