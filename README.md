<p align="center">
  <img src="brief-logo.png" alt="brief" width="120" />
  <br>
  <strong>.brief</strong>
  <br>
  <em>extract once, render per query</em>
</p>

# Brief

Content compression for AI agents.

An agent researching a topic finds 10 web pages and a tutorial video. Reading all of them raw costs ~40,000 tokens. With Brief, the agent scans all 11 sources in ~100 tokens, picks the 3 that matter, and reads those in detail for ~2,000 tokens. **Same conclusions, 20x cheaper.**

## Why

| Problem | Without Brief | With Brief |
|---|---|---|
| Agent reads a 5,000-word article | 5,000 tokens | 9 tokens (headline) to 200 tokens (detailed) |
| Two agents research the same topic | Both extract everything from scratch | Second agent reads from `.briefs/` folder — instant |
| Agent re-visits a source mid-task | Might extract different content each time | Extracted once, frozen. Same ground truth every time |

## Install

```bash
pip install brief
```

Brief works out of the box for webpage extraction. For LLM-powered summaries, add your API key to a `.env` file — see [LLM Config](#llm-config).

## Use Cases

### 1. Research Agent — scan and dive

```python
from brief import brief_batch, brief

# Agent finds 5 URLs from search results
headlines = brief_batch([
    "https://docs.python.org/3/library/asyncio.html",
    "https://fastapi.tiangolo.com/",
    "https://flask.palletsprojects.com/",
    "https://www.djangoproject.com/",
    "https://expressjs.com/",
], query="python async web framework", depth=0)

# headlines = 5 one-liners, ~50 tokens total
# Agent reads them, decides FastAPI and Flask are relevant

# Go deeper on the two that matter
detail = brief("https://fastapi.tiangolo.com/", "async support", depth=2)
```

### 2. Agent handoff — shared memory

```python
# Agent A does research, briefs 3 sources
brief("https://fastapi.tiangolo.com/", "getting started")
brief("https://docs.pydantic.dev/", "validation")
brief("https://docs.sqlalchemy.org/", "async engine")

# Agent B picks up the work later
from brief import check_brief
data = check_brief("https://fastapi.tiangolo.com/")
# → instant, no re-extraction. Same ground truth Agent A saw.
```

### 3. Cross-reference — compare sources

```python
from brief import compare

result = compare(
    ["https://fastapi.tiangolo.com/", "https://flask.palletsprojects.com/"],
    query="how do they handle middleware",
    depth=2,
)
# → Both sources rendered at same depth, same query. Apples to apples.
```

### 4. Video briefing

```python
from brief import brief

text = brief("https://youtube.com/watch?v=abc", "how to deploy")
# → Extracts captions, summarizes, returns timestamped moments
```

## Layered Depth

Briefs are progressive. The agent controls how much detail it needs:

```
depth=0   headline     ~9 tokens      "[WEBPAGE] FastAPI — high performance web framework"
depth=1   summary      ~100 tokens    + key points, top moments
depth=2   detailed     ~700 tokens    + all extracted content, re-ranked by query
depth=3   full         ~2000 tokens   + complete transcript/text
```

Same cached data at every depth. No re-extraction.

## Interfaces

### Python

```python
from brief import brief, brief_batch, compare, check_brief

brief(uri, query, depth=1)                    # single source
brief_batch([uri1, uri2], query, depth=0)     # scan many
compare([uri1, uri2], query, depth=2)         # cross-reference
check_brief(uri)                               # cache lookup
```

### CLI

```bash
brief --uri "https://example.com" --query "key takeaways"
brief --uri "https://example.com" --depth 0
brief --list
```

### MCP (Claude, Cursor)

```bash
pip install -e ".[mcp]"
```

```json
{
  "mcpServers": {
    "brief": {
      "command": "python",
      "args": ["-m", "brief.mcp_server"],
      "env": {
        "BRIEF_LLM_API_KEY": "sk-or-v1-your-key",
        "BRIEF_LLM_BASE_URL": "https://openrouter.ai/api/v1",
        "BRIEF_LLM_MODEL": "anthropic/claude-3.5-sonnet"
      }
    }
  }
}
```

4 tools: `brief_content`, `check_existing_brief`, `list_briefs`, `compare_sources`

### HTTP API

```bash
uvicorn brief.api:app --port 8080
```

## The `.briefs/` Folder

Every brief saves two files:

```
.briefs/
├── fastapi-tiangolo-com.brief       ← plain text, any agent can read
├── fastapi-tiangolo-com.brief.json  ← structured data, all layers
└── _index.sqlite3                   ← fast URI lookups
```

Agents with file access browse `.briefs/` directly. No API, no server, no MCP — just read the file.

## LLM Config

Brief uses an LLM for summarization. Any OpenAI-compatible provider works. Create a `.env` file:

```bash
# OpenRouter (recommended — one key, every model)
BRIEF_LLM_API_KEY=sk-or-v1-your-key
BRIEF_LLM_BASE_URL=https://openrouter.ai/api/v1
BRIEF_LLM_MODEL=anthropic/claude-3.5-sonnet
```

Also works with OpenAI, Ollama (local), Groq. See [.env.example](.env.example) for all options.

No LLM? Brief still works — falls back to heuristic extraction.

## Supported Content

| Type | Status |
|---|---|
| Webpages | ✅ Working |
| Video (YouTube + 1700 sites) | ✅ Working |
| PDF | 🔜 Next |
| Audio | 🔜 Planned |

Adding a new type = one file in `brief/extractors/`.
