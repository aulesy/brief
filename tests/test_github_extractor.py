"""Tests for GitHub extractor — docstring extraction and URL parsing."""

from brief.extractors.github import (
    _extract_js_docstring,
    _extract_python_docstring,
    _parse_blob_url,
    _prioritize_files,
)


# ── Python docstring tests ──────────────────────────────────────


def test_extract_python_docstring_basic():
    source = '"""This is a module docstring."""\n\ndef foo(): pass\n'
    assert _extract_python_docstring(source) == "This is a module docstring."


def test_extract_python_docstring_multiline():
    source = '"""Multi-line docstring.\n\nWith a second paragraph.\n"""\n\nimport os\n'
    result = _extract_python_docstring(source)
    assert result is not None
    assert "Multi-line docstring" in result
    assert "second paragraph" in result


def test_extract_python_docstring_with_future_import():
    source = 'from __future__ import annotations\n\n"""Module docs after future."""\n'
    result = _extract_python_docstring(source)
    assert result is not None
    assert "Module docs after future" in result


def test_extract_python_docstring_single_quotes():
    source = "'''Single-quote docstring.'''\n\nclass Foo: pass\n"
    result = _extract_python_docstring(source)
    assert result is not None
    assert "Single-quote docstring" in result


def test_extract_python_docstring_none_when_missing():
    source = "import os\n\ndef main():\n    pass\n"
    assert _extract_python_docstring(source) is None


def test_extract_python_docstring_none_for_empty():
    assert _extract_python_docstring("") is None


def test_extract_python_docstring_truncates_long():
    long_doc = 'x' * 500
    source = f'"""{long_doc}"""\n'
    result = _extract_python_docstring(source)
    assert result is not None
    assert len(result) <= 300


# ── JS/TS docstring tests ───────────────────────────────────────


def test_extract_js_docstring_basic():
    source = '/**\n * Express web framework.\n */\n\nconst app = express();\n'
    result = _extract_js_docstring(source)
    assert result is not None
    assert "Express web framework" in result


def test_extract_js_docstring_with_tags():
    source = '/**\n * HTTP client library.\n * @module http-client\n * @version 1.0\n */\n'
    result = _extract_js_docstring(source)
    assert result is not None
    assert "HTTP client library" in result
    # Tags should be stripped
    assert "@module" not in result


def test_extract_js_docstring_with_use_strict():
    source = "'use strict';\n\n/**\n * Utility functions.\n */\n"
    result = _extract_js_docstring(source)
    assert result is not None
    assert "Utility functions" in result


def test_extract_js_docstring_none_when_missing():
    source = 'const x = 1;\nfunction foo() {}\n'
    assert _extract_js_docstring(source) is None


def test_extract_js_docstring_none_for_regular_comment():
    source = '// This is a regular comment\nconst x = 1;\n'
    assert _extract_js_docstring(source) is None


def test_extract_js_docstring_with_hashbang():
    source = '#!/usr/bin/env node\n/**\n * CLI tool.\n */\n'
    result = _extract_js_docstring(source)
    assert result is not None
    assert "CLI tool" in result


# ── /blob/ URL parsing tests ────────────────────────────────────


def test_parse_blob_url_basic():
    url = "https://github.com/psf/requests/blob/main/src/requests/api.py"
    result = _parse_blob_url(url)
    assert result == ("psf", "requests", "main", "src/requests/api.py")


def test_parse_blob_url_with_branch():
    url = "https://github.com/owner/repo/blob/develop/lib/index.ts"
    result = _parse_blob_url(url)
    assert result == ("owner", "repo", "develop", "lib/index.ts")


def test_parse_blob_url_non_blob():
    url = "https://github.com/owner/repo"
    assert _parse_blob_url(url) is None


def test_parse_blob_url_issues():
    url = "https://github.com/owner/repo/issues/123"
    assert _parse_blob_url(url) is None


# ── File prioritization tests ──────────────────────────────────


def test_prioritize_files_init_first():
    files = [
        {"name": "utils.py", "path": "src/utils.py", "size": 1000},
        {"name": "__init__.py", "path": "src/__init__.py", "size": 500},
        {"name": "config.py", "path": "src/config.py", "size": 800},
    ]
    result = _prioritize_files(files)
    assert result[0]["name"] == "__init__.py"


def test_prioritize_files_skips_tests():
    files = [
        {"name": "test_utils.py", "path": "test_utils.py", "size": 1000},
        {"name": "main.py", "path": "main.py", "size": 500},
    ]
    result = _prioritize_files(files)
    names = [f["name"] for f in result]
    assert "test_utils.py" not in names
    assert "main.py" in names


def test_prioritize_files_skips_non_code():
    files = [
        {"name": "README.md", "path": "README.md", "size": 5000},
        {"name": "Dockerfile", "path": "Dockerfile", "size": 300},
        {"name": "app.py", "path": "app.py", "size": 2000},
    ]
    result = _prioritize_files(files)
    names = [f["name"] for f in result]
    assert "README.md" not in names
    assert "Dockerfile" not in names
    assert "app.py" in names


def test_prioritize_files_max_limit():
    files = [{"name": f"mod{i}.py", "path": f"mod{i}.py", "size": 100} for i in range(20)]
    result = _prioritize_files(files)
    assert len(result) <= 10
