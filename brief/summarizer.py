"""Brief summarizer — generates neutral summaries from extracted content.

Works with ANY LLM that exposes an OpenAI-compatible API.

Config is read from a .env file (drop it in your project root) or env vars.
No terminal needed — just create a .env file with these keys:

Setup — pick ONE provider:

  # OpenRouter (recommended — one key, every model)
  #   1. Go to https://openrouter.ai/keys
  #   2. Create a free API key
  #   3. Set these env vars:
  export BRIEF_LLM_API_KEY=sk-or-v1-your-key-here
  export BRIEF_LLM_BASE_URL=https://openrouter.ai/api/v1
  export BRIEF_LLM_MODEL=anthropic/claude-3.5-sonnet  # or google/gemini-flash-1.5, meta-llama/llama-3.1-70b, etc.

  # OpenAI (direct)
  export OPENAI_API_KEY=sk-...
  # That's it — defaults to gpt-4o-mini

  # Ollama (local, free, no key needed)
  export BRIEF_LLM_BASE_URL=http://localhost:11434/v1
  export BRIEF_LLM_MODEL=llama3
  export BRIEF_LLM_API_KEY=ollama

  # Groq (fast inference, free tier)
  export BRIEF_LLM_API_KEY=gsk_...
  export BRIEF_LLM_BASE_URL=https://api.groq.com/openai/v1
  export BRIEF_LLM_MODEL=llama-3.1-70b-versatile
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You summarize content for AI agents. Be factual, neutral, concise. "
    "Respond with JSON: {\"summary\": \"2-3 sentences max\", \"key_points\": [\"point1\", \"point2\", ...]}"
)


def _get_llm_config() -> tuple[str | None, str | None, str]:
    """Return (api_key, base_url, model) from config (.env file or env vars)."""
    from . import config

    api_key = config.get("BRIEF_LLM_API_KEY") or config.get("OPENAI_API_KEY")
    base_url = config.get("BRIEF_LLM_BASE_URL") or None
    model = config.get("BRIEF_LLM_MODEL", "gpt-4o-mini")
    return api_key, base_url, model


def _truncate(text: str, max_chars: int) -> str:
    """Truncate at the nearest word boundary, never mid-word."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(None, 1)[0]
    return cut + "..."


def _heuristic_summary(chunks: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Fallback: extract summary from chunk text without LLM."""
    if not chunks:
        return "", []

    texts = [c.get("text", "") for c in chunks if c.get("text", "").strip()]
    if not texts:
        return "", []

    first = texts[0].strip()
    last = texts[-1].strip() if len(texts) > 1 else ""

    if last and last != first:
        summary = f"{first} ... {last}"
    else:
        summary = first

    if len(summary) > 300:
        summary = _truncate(summary, 297)

    step = max(1, len(texts) // 5)
    # Prefer longer paragraphs (more likely to be real content, not boilerplate)
    substantive = [t for t in texts if len(t) > 100] or texts
    key_points = [_truncate(t, 120) for t in substantive[::max(1, len(substantive) // 5)]][:5]
    return summary, key_points


def _llm_summary(chunks: list[dict[str, Any]], query: str | None = None) -> tuple[str, list[str]] | None:
    """Generate summary via any OpenAI-compatible API."""
    api_key, base_url, model = _get_llm_config()

    if not api_key:
        logger.debug("No LLM API key configured. Set BRIEF_LLM_API_KEY or OPENAI_API_KEY.")
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logger.debug("openai package not installed.")
        return None

    transcript = " ".join(c.get("text", "") for c in chunks)
    words = transcript.split()
    if len(words) > 1500:
        transcript = " ".join(words[:1500])

    try:
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        client = OpenAI(**client_kwargs)
        logger.info("Calling LLM for summary: model=%s base_url=%s", model, base_url or "default")

        if query:
            user_content = (
                f"The user wants to know: {query}\n\n"
                f"Lead with the answer to their question, then give broader context. "
                f"Do not start with a generic description.\n\n{transcript}"
            )
        else:
            user_content = f"Summarize this transcript:\n\n{transcript}"

        # Try with system prompt first, fall back to single user message
        # (some free models don't support system prompts)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.2,
                max_tokens=500,
            )
        except Exception as sys_err:
            if "400" in str(sys_err) or "system" in str(sys_err).lower():
                logger.info("System prompt not supported, retrying as user message")
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "user", "content": f"{_SYSTEM_PROMPT}\n\n{user_content}"},
                    ],
                    temperature=0.2,
                    max_tokens=500,
                )
            else:
                raise

        raw = response.choices[0].message.content or ""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        # Try strict JSON first
        parsed = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Try to repair truncated JSON (close open strings/brackets)
            for fix in [raw + '"]}', raw + '"}', raw + "]}", raw + "}"]:
                try:
                    parsed = json.loads(fix)
                    break
                except json.JSONDecodeError:
                    continue

        if parsed:
            summary = parsed.get("summary", "")
            key_points = parsed.get("key_points", [])
            if isinstance(summary, str) and summary:
                logger.info("LLM summary generated (%d chars, model=%s)", len(summary), model)
                return _truncate(summary, 300), [_truncate(str(kp), 120) for kp in key_points[:5]]

    except Exception as exc:
        logger.warning("LLM summary failed (%s): %s", model, exc)

    return None


def summarize(chunks: list[dict[str, Any]], query: str | None = None) -> tuple[str, list[str]]:
    """Generate a query-focused summary and key points.

    Tries LLM first (any OpenAI-compatible provider), falls back to heuristic.
    When query is provided, the summary focuses on that specific angle.
    Returns (summary, key_points).
    """
    llm_result = _llm_summary(chunks, query=query)
    if llm_result is not None:
        return llm_result

    logger.debug("Using heuristic summary fallback.")
    return _heuristic_summary(chunks)
