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
        summary = summary[:297] + "..."

    step = max(1, len(texts) // 5)
    key_points = [t[:100] for t in texts[::step]][:5]
    return summary, key_points


def _llm_summary(chunks: list[dict[str, Any]]) -> tuple[str, list[str]] | None:
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
    if len(words) > 3500:
        transcript = " ".join(words[:3500])

    try:
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        client = OpenAI(**client_kwargs)
        logger.info("Calling LLM for summary: model=%s base_url=%s", model, base_url or "default")

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Summarize this transcript:\n\n{transcript}"},
            ],
            temperature=0.2,
            max_tokens=300,
        )

        raw = response.choices[0].message.content or ""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed = json.loads(raw)
        summary = parsed.get("summary", "")
        key_points = parsed.get("key_points", [])

        if isinstance(summary, str) and summary:
            logger.info("LLM summary generated (%d chars, model=%s)", len(summary), model)
            return summary[:300], [str(kp)[:100] for kp in key_points[:5]]

    except Exception as exc:
        logger.warning("LLM summary failed (%s): %s", model, exc)

    return None


def summarize(chunks: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Generate a neutral summary and key points.

    Tries LLM first (any OpenAI-compatible provider), falls back to heuristic.
    Returns (summary, key_points).
    """
    llm_result = _llm_summary(chunks)
    if llm_result is not None:
        return llm_result

    logger.debug("Using heuristic summary fallback.")
    return _heuristic_summary(chunks)
