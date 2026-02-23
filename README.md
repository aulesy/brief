<div align="center">
  <img src="assets/logo.png" alt="Brief Logo" width="300" />
</div>

# Brief

Reading the web is expensive, in tokens, in time, in redundant work. Brief gives agents a shared layer for extracting and understanding content: webpages, videos, and PDFs get pulled once, summarized around the task at hand, and cached so any agent in your pipeline can reuse them instantly. Start with a headline, go deep only where it matters, and let the briefs accumulate as your system works.

Without Brief, your agent fetches a page, chunks it, summarizes it, and then finally gets to the actual question, burning tokens at every step. Brief collapses that into a single call that returns exactly as much as the agent needs, already shaped around the task.

```python
from brief import brief

# ~9 tokens - enough to know if this page is worth reading
brief("https://fastapi.tiangolo.com/", "what is fastapi", depth=0)

# ~100 tokens - key points and top sections
brief("https://fastapi.tiangolo.com/", "what is fastapi", depth=1)

# ~700 tokens - full structured summary, re-ranked around your query
brief("https://fastapi.tiangolo.com/", "async support", depth=2)
```

## Depth levels

The agent controls how much it reads:

```
depth=0   headline     ~9 tokens      "[WEBPAGE] FastAPI - high performance web framework"
depth=1   summary      ~100 tokens    + key points, top 3 sections
depth=2   detailed     ~700 tokens    + all sections, re-ranked by query
depth=3   full         ~2000 tokens   + complete extracted text
```

Every depth level reads from the same cached extraction. No re-fetching. When a new query is asked, Brief re-summarizes the cached content with the LLM, fast, because the expensive extraction is already done.

## Works across content types

Brief handles webpages, videos, and PDFs with the same interface:

- **Webpages** - [trafilatura](https://trafilatura.readthedocs.io/) strips navigation, ads, and scripts, leaving just the article. Falls back to [httpx](https://www.python-httpx.org/) with browser headers, then optionally to [Playwright](https://playwright.dev/) for sites behind Cloudflare or bot protection (`pip install getbrief[playwright]`).
- **Videos** - [yt-dlp](https://github.com/yt-dlp/yt-dlp) fetches captions directly. If none exist, [faster-whisper](https://github.com/SYSTRAN/faster-whisper) transcribes the audio locally. Falls back to video metadata (title, description, tags) when neither is available.
- **PDFs** - [pymupdf](https://pymupdf.readthedocs.io/) extracts text page by page.

## Common patterns

### Scan many URLs cheaply, then read what matters

```python
from brief import brief_batch, brief

headlines = brief_batch([
    "https://docs.python.org/3/library/asyncio.html",
    "https://fastapi.tiangolo.com/",
    "https://flask.palletsprojects.com/",
], query="python async web framework", depth=0)

# Now only fetch detail on the one that looks relevant
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

### Check the cache before fetching

```python
from brief import check_brief

data = check_brief("https://fastapi.tiangolo.com/")
# Returns the cached brief if it exists, None otherwise
```

## Install

```bash
pip install getbrief
```

Brief uses any OpenAI-compatible LLM for summarization. Add your API key to a `.env` file — see [Configuration](#configuration). Free models work well.

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

## The `.briefs/` folder

Every URL gets its own subdirectory. Each query adds a new `.brief` file:

```
.briefs/
├── fastapi-tiangolo-com/
│   ├── _source.json              ← raw extraction data
│   ├── overview.brief            ← generic summary
│   ├── async-support.brief       ← Agent A's question
│   └── how-to-deploy.brief       ← Agent B's question
└── _index.sqlite3                ← fast lookups
```

One agent researches, another reasons, another writes — nothing gets fetched or summarized twice. Each `.brief` file includes a TRAIL section listing sibling briefs, so any agent can see what else has been researched.

Agents with MCP tools call `brief_content()`. Agents with file access can read `.briefs/` directly, or `grep` across files. No special tools required — just plain text.


## Configuration

Brief uses any OpenAI-compatible provider for summarization. Create a `.env` file in your project root:

```bash
# OpenRouter (one key, many models)
BRIEF_LLM_API_KEY=sk-or-v1-your-key
BRIEF_LLM_BASE_URL=https://openrouter.ai/api/v1
BRIEF_LLM_MODEL=google/gemma-3-4b-it:free
```

Also works with OpenAI, Ollama (local), and Groq. See [.env.example](.env.example) for all options.

For videos without captions, Brief transcribes audio locally using `faster-whisper`. To use OpenAI's Whisper API instead:

```bash
BRIEF_STT_API_KEY=sk-your-openai-key
```

## Contributing

Brief is designed to be easy to extend and contributions are welcome — whether that's a new content type, a better summarization strategy, or improvements to the CLI or API. New extractors live in `brief/extractors/` and each one is just a single file implementing one function:

```python
def extract(uri: str) -> list[dict[str, Any]]:
    """Return a list of chunks with 'text', 'start_sec', 'end_sec' keys."""
```

Adding support for a new type (audio, spreadsheets, etc.) is a single file addition. Contributions welcome.
