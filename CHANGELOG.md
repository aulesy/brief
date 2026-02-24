# Changelog

All notable changes to Brief are documented here.

## [0.6.0] - 2026-02-24

### Breaking Changes
- `compare()` default depth changed from 2 to 1
- `compare()` returns synthesis only (no individual briefs appended)
- `list_briefs` MCP tool removed (merged into `check_existing_brief`)
- `faster-whisper` moved to optional `[transcribe]` extra

### Added
- **Token usage tracking** — tracks LLM tokens spent per brief and cache hits saved
- **Compare redesign** — focused synthesis with TRAIL breadcrumbs to individual briefs
- **Depth-aware comparison** — depth 0 (headline), 1 (summary), 2 (deep dive)
- **Human-readable comparison filenames** — `fastapi-vs-flask--routing.brief`
- `check_brief()` works without URI for compact overview of all sources
- `compare()` accepts `force` parameter for cache bypass
- Source URLs labeled in comparison output

### Fixed
- 60s timeout on all LLM calls (prevents indefinite hangs)
- API `cached` flag now checks query cache, not just source existence
- `check_brief()` returns query info instead of raw source data
- GitHub extractor `_HEADERS` race condition in batch mode
- `httpx` added as explicit dependency
- Dead `schemas.py` removed
- User-Agent strings updated to `Brief/0.5`

## [0.5.0] - 2026-02-20

### Added
- Initial public release
- Five content extractors: webpage, video, PDF, Reddit, GitHub
- Depth-aware summarization (0/1/2)
- Query-focused caching with `.brief` files
- TRAIL sections linking sibling briefs
- MCP server, CLI, HTTP API, Python API
- Batch briefing with `brief_batch()`
- SQLite index for fast lookups
