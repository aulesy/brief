# Contributing to Brief

Brief is designed to be easy to extend. Here's how to contribute.

## Adding a New Extractor

Each extractor lives in `brief/extractors/` as a single file implementing one function:

```python
# brief/extractors/mytype.py

def extract(uri: str) -> list[dict[str, Any]]:
    """Return a list of chunks with 'text' and optional 'start_sec' keys."""
    # Fetch content from uri
    # Split into meaningful chunks
    return [
        {"text": "extracted content here", "start_sec": 0.0},
    ]
```

Then register the type in `brief/extractors/__init__.py`:

```python
def detect_type(uri: str) -> str:
    # Add your detection logic
    if "mysite.com" in parsed.hostname:
        return "mytype"
```

And add extraction to `brief/service.py`:

```python
elif content_type == "mytype":
    from .extractors.mytype import extract as extract_mytype
    chunks = extract_mytype(uri)
```

## Development Setup

```bash
git clone https://github.com/aulesy/brief.git
cd brief
pip install -e ".[dev]"
```

Create a `.env` file with your LLM API key (see `.env.example`).

## Testing

```bash
# Quick smoke test
python -c "from brief import brief; print(brief('https://example.com', 'test', depth=0))"

# Test imports
python -c "from brief import brief, check_brief, compare, brief_batch; print('ok')"
```

## Guidelines

- Keep extractors simple — one file, one function
- Use `httpx` for HTTP requests (not `urllib` or `requests`)
- Add `User-Agent: Brief/0.6` to all HTTP headers
- Handle errors gracefully — return empty list, don't crash
- Log with `logging.getLogger(__name__)`

## What We'd Love Help With

- New content types (Notion, Confluence, arXiv, etc.)
- Better summarization prompts
- CLI improvements
- API enhancements
- Test coverage
