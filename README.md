<div align="center">
  <img src="assets/logo.png" alt="Brief Logo" width="300" />
</div>

# Brief

Agents shouldn't have to read to research. They should be able to ask.

Your agent walks up to each source and asks: *do you have what I'm looking for? Can you give me only what I need?* The source answers with exactly that, a headline if you're skimming, a summary if you're curious, a deep dive if you need the full picture.

Brief extracts content once across webpages, videos, PDFs, Reddit, and GitHub. Every question gets a focused answer, shaped by the query, cached so your agents never repeat work, and saved so your whole pipeline can build on it.

> Instead of thinking "I need to read this article," your agent can think "I need to understand this concept across multiple sources." Brief handles the reading. The agent handles the connecting.

## What Brief feels like

Most agents read the web like a student the night before an exam: grab everything, highlight randomly, hope something sticks. Every page gets read cover to cover (or skimmed over), tokens burning whether the content matters or not.

Brief works differently. Your agent doesn't read sources, it interviews them. Ask a question, get an answer. Move on. And because every answer is saved, a team of agents can share the work without any of them repeating it. The more your system runs, the more it already knows.

```python
from brief import brief

# One sentence — is this page even relevant?
brief("https://fastapi.tiangolo.com/", "async support", depth=0)

# Summary + key points — what do I need to know?
brief("https://fastapi.tiangolo.com/", "async support", depth=1)

# Deep dive — give me specifics, examples, gotchas
brief("https://fastapi.tiangolo.com/", "async support", depth=2)
```

## Depth levels

```
depth=0   headline    one sentence — is this worth reading?
depth=1   summary     2-3 sentences + key points (default)
depth=2   deep dive   detailed analysis with specifics, examples, trade-offs
```

Each (query, depth) pair gets its own `.brief` file.

## Content types

Brief handles webpages, videos, PDFs, Reddit, and GitHub with the same interface:

- **Webpages** - [trafilatura](https://trafilatura.readthedocs.io/) strips navigation, ads, and scripts. Falls back to [httpx](https://www.python-httpx.org/), then optionally [Playwright](https://playwright.dev/) for bot-protected sites.
- **Videos** - [yt-dlp](https://github.com/yt-dlp/yt-dlp) fetches captions. If none exist, [faster-whisper](https://github.com/SYSTRAN/faster-whisper) transcribes audio locally.
- **PDFs** - [pymupdf](https://pymupdf.readthedocs.io/) extracts text page by page.
- **Reddit** - fetches post content and top comments via Reddit's JSON API.
- **GitHub** - fetches repo metadata, README, file tree, and open issues via GitHub's API.

## Common patterns

### Scan many URLs cheaply, then read what matters

```python
from brief import brief_batch, brief

# Triage — which of these are worth reading?
headlines = brief_batch([
    "https://docs.python.org/3/library/asyncio.html",
    "https://fastapi.tiangolo.com/",
    "https://flask.palletsprojects.com/",
], query="python async web framework", depth=0)

# Go deep on the one that matters
detail = brief("https://fastapi.tiangolo.com/", "async support", depth=2)
```

### Compare sources side by side

```python
from brief import compare

result = compare(
    ["https://fastapi.tiangolo.com/", "https://flask.palletsprojects.com/"],
    query="how do they handle middleware",
    depth=2,
)
```

### Check what's already been researched

```python
from brief import check_brief

data = check_brief("https://fastapi.tiangolo.com/")
# Returns existing briefs for this URL, None if not yet briefed
```

## Install

Requires Python 3.12+.

```bash
pip install getbrief
```

For video transcription, you'll need [ffmpeg](https://ffmpeg.org/) installed. For bot-protected sites:

```bash
pip install getbrief[playwright]
playwright install chromium
```

Brief uses any OpenAI-compatible LLM for summarization. Add your API key to a `.env` file, see [Configuration](#configuration). Free models work well.

## Interfaces

### Python

```python
from brief import brief, brief_batch, compare, check_brief
```

### CLI

```bash
brief --uri "https://example.com" --query "key takeaways"
brief --uri "https://example.com" --depth 0
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
└── _index.sqlite3                   fast lookups
```

One agent researches, another reasons, another writes. Each `.brief` file includes a TRAIL section listing sibling briefs, so any agent knows what else has been asked about that source. Any agent can read `.briefs/` directly, just plain text files, no special tools needed.

`.brief` is a plain text format, readable by humans, usable by any agent, and simple enough to share, version, or commit alongside your code.

## Configuration

Brief uses any OpenAI-compatible provider for summarization. Create a `.env` file in your project root:

```bash
# OpenRouter (one key, many models)
BRIEF_LLM_API_KEY=sk-or-v1-your-key
BRIEF_LLM_BASE_URL=https://openrouter.ai/api/v1
BRIEF_LLM_MODEL=google/gemma-3-4b-it:free
```

`OPENAI_API_KEY` also works as a fallback if `BRIEF_LLM_API_KEY` is not set.

Also works with OpenAI, Ollama (local), and Groq. See [.env.example](.env.example) for all options.

For videos without captions, Brief transcribes audio locally using `faster-whisper`. To use OpenAI's Whisper API instead:

```bash
BRIEF_STT_API_KEY=sk-your-openai-key
```

For GitHub repos, the public API is rate-limited to 60 requests/hour. Set a token for higher limits:

```bash
GITHUB_TOKEN=ghp_your-token
```

## Troubleshooting

- **Paywalled / auth-protected content** - Brief returns a clear error for 401/403/429 responses. It cannot extract content behind logins or paywalls.
- **Bot protection (Cloudflare, etc.)** - Install Playwright: `pip install getbrief[playwright] && playwright install chromium`
- **Stale or bad summary** - Use `--force` to skip cache and re-extract: `brief --uri <URL> --force`
- **Clear all cached data** - Delete the `.briefs/` folder.
- **LLM not responding** - Check your `.env` file has valid API keys. Brief falls back to a heuristic summary if the LLM is unavailable.

## Contributing

Brief is designed to be easy to extend. Contributions are welcome, whether that's a new content type, a better summarization strategy, or improvements to the CLI or API. New extractors live in `brief/extractors/` and each one is a single file implementing one function:

```python
def extract(uri: str) -> list[dict[str, Any]]:
    """Return a list of chunks with 'text' and 'start_sec' keys."""
```