<div align="center">
  <img src="assets/logo.png" alt="Brief Logo" width="300" />
</div>

# Brief

Reading the web is the most expensive thing your agent does, and the least of what it's good at.

Brief reads content so your agent doesn't have to. Give it a URL and a question, and it extracts, summarizes, and caches the answer. Webpages, videos, PDFs, Reddit, GitHub, all through one interface.

```python
from brief import brief

# Is this page even relevant? (~1 sentence)
brief("https://fastapi.tiangolo.com/", "async support", depth=0)

# What do I need to know? (summary + key points)
brief("https://fastapi.tiangolo.com/", "async support", depth=1)

# Give me everything. (detailed analysis with examples)
brief("https://fastapi.tiangolo.com/", "async support", depth=2)
```

Every answer is cached as a plain `.brief` file. Ask once, reuse forever. A team of agents can share research without repeating work. One agent investigates, another reasons, another writes, and nobody re-reads the same source.

The more your system runs, the more it already knows.

```python
# Agent A researches FastAPI (extraction + LLM call)
brief("https://fastapi.tiangolo.com/", "async support", depth=2)

# Minutes later, Agent B is writing a comparison doc.
# Same URL, same question. Instant. No fetch, no LLM, no tokens.
check_brief("https://fastapi.tiangolo.com/")
# → "async-support-deep.brief: FastAPI handles async natively..."

# Agent B goes deeper with a new question. One LLM call, no re-extraction.
brief("https://fastapi.tiangolo.com/", "error handling", depth=1)
```

Agent A paid the cost. Agent B got it free. Agents should spend tokens on reasoning, not searching.

## Install

Requires Python 3.12+.

```bash
pip install getbrief
```

Brief uses any OpenAI-compatible LLM for summarization. Create a `.env` file:

```bash
BRIEF_LLM_API_KEY=sk-or-v1-your-key
BRIEF_LLM_BASE_URL=https://openrouter.ai/api/v1
BRIEF_LLM_MODEL=google/gemma-3-4b-it:free # any OpenRouter free or cheap model works
```

Free models work great. Also works with OpenAI, Ollama (local), and Groq. See [.env.example](.env.example) for all options.

## Common patterns

### Triage many URLs, then go deep on what matters

```python
from brief import brief_batch, brief

# Scan 10 URLs for pennies. Which ones are relevant?
headlines = brief_batch([
    "https://docs.python.org/3/library/asyncio.html",
    "https://fastapi.tiangolo.com/",
    "https://flask.palletsprojects.com/",
], query="python async web framework", depth=0)

# Go deep on the one that matters
detail = brief("https://fastapi.tiangolo.com/", "async support", depth=2)
```

### Compare sources

```python
from brief import compare

# Briefs each source, then synthesizes a comparison
result = compare(
    ["https://fastapi.tiangolo.com/", "https://flask.palletsprojects.com/"],
    query="how do they handle middleware",
    depth=2,
)
```

### Check what's already been researched

```python
from brief import check_brief

# See what queries have already been answered for this URL
check_brief("https://fastapi.tiangolo.com/")
```

## Depth levels

```
depth=0   headline    one sentence, is this worth reading?
depth=1   summary     2-3 sentences + key points (default)
depth=2   deep dive   detailed analysis with specifics, examples, trade-offs
```

Each (query, depth) pair produces its own `.brief` file.

## Content types

Brief handles five content types with the same interface:

- **Webpages** — [trafilatura](https://trafilatura.readthedocs.io/) strips navigation, ads, and scripts. Falls back to [httpx](https://www.python-httpx.org/), then optionally [Playwright](https://playwright.dev/) for bot-protected sites.
- **Videos** — [yt-dlp](https://github.com/yt-dlp/yt-dlp) fetches captions. If none exist, [faster-whisper](https://github.com/SYSTRAN/faster-whisper) transcribes audio locally.
- **PDFs** — [pymupdf](https://pymupdf.readthedocs.io/) extracts text page by page.
- **Reddit** — fetches post content and top comments via Reddit's JSON API.
- **GitHub** — fetches repo metadata, README, file tree, and open issues via GitHub's API.

## Interfaces

### Python

```python
from brief import brief, brief_batch, compare, check_brief
```

### CLI

```bash
brief --uri "https://example.com" --query "key takeaways"
brief --uri "https://example.com" --depth 0
brief --compare --batch "https://url1.com" --batch "https://url2.com" --query "compare"
brief --list
```

### MCP

```json
{
  "mcpServers": {
    "brief": {
      "command": "uvx",
      "args": ["--from", "getbrief", "brief-mcp"],
      "env": {
        "BRIEF_LLM_API_KEY": "sk-or-v1-your-key",
        "BRIEF_LLM_BASE_URL": "https://openrouter.ai/api/v1",
        "BRIEF_LLM_MODEL": "google/gemma-3-4b-it:free"
      }
    }
  }
}
```

This gives your agent four tools:

- **brief_content** — brief a URL with a query at depth 0–2
- **check_existing_brief** — see what's already been asked about a URL
- **list_briefs** — show all cached briefs
- **compare_sources** — same question across multiple URLs, with synthesis

### HTTP API

```bash
uvicorn brief.api:app --port 8080
```

```bash
# Brief a URL
curl -X POST http://localhost:8080/brief \
  -H "Content-Type: application/json" \
  -d '{"uri": "https://fastapi.tiangolo.com/", "query": "async support", "depth": 1}'

# List all briefs
curl http://localhost:8080/briefs

# Health check
curl http://localhost:8080/health
```

## The `.briefs/` folder

Every URL gets its own subdirectory. Each (query, depth) adds a new `.brief` file:

```
.briefs/
├── fastapi-tiangolo-com/
│   ├── _source.json                 raw extraction, no LLM output
│   ├── async-support.brief          depth=1 answer
│   └── async-support-deep.brief     depth=2 answer, same query, richer
├── _comparisons/                    cached cross-source comparisons
└── _index.sqlite3                   fast lookups
```

Each `.brief` file includes a TRAIL section at the bottom, listing sibling briefs for the same source:

```
─── TRAIL ──────────────────────────────────────
→ async-support.brief
→ error-handling-deep.brief
→ _source.json
```

When an agent opens any `.brief` file, it instantly sees what else has already been asked about that source. No API call, no index lookup, just read the file. This means agents can build on each other's research naturally.

## Configuration

Brief uses any OpenAI-compatible provider. `OPENAI_API_KEY` also works as a fallback if `BRIEF_LLM_API_KEY` is not set.

For video transcription without captions:

```bash
pip install getbrief[transcribe]  # installs faster-whisper
```

For bot-protected sites (Cloudflare, etc.):

```bash
pip install getbrief[playwright]
playwright install chromium
```

For GitHub repos, the public API is rate-limited to 60 requests/hour. Set a token for higher limits:

```bash
GITHUB_TOKEN=ghp_your-token
```

## Troubleshooting

- **Paywalled / auth-protected content** — Brief returns a clear error for 401/403/429 responses. It cannot extract content behind logins or paywalls.
- **Bot protection (Cloudflare, etc.)** — Install Playwright: `pip install getbrief[playwright] && playwright install chromium`
- **Stale or bad summary** — Use `--force` to skip cache and re-extract: `brief --uri <URL> --force`
- **Clear all cached data** — Delete the `.briefs/` folder.
- **LLM not responding** — Check your `.env` file has valid API keys. Brief falls back to a heuristic summary if the LLM is unavailable.

## Contributing

Brief is designed to be easy to extend. New extractors live in `brief/extractors/` and each one is a single file implementing one function:

```python
def extract(uri: str) -> list[dict[str, Any]]:
    """Return a list of chunks with 'text' and 'start_sec' keys."""
```

Contributions welcome: new content types, better summarization, CLI improvements, or API enhancements.
