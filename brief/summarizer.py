"""Brief summarizer — generates depth-aware summaries from extracted content.

Works with ANY LLM that exposes an OpenAI-compatible API.

Config is read from a .env file (drop it in your project root) or env vars.

Setup — pick ONE provider:

  # OpenRouter (recommended — one key, every model)
  BRIEF_LLM_API_KEY=sk-or-v1-your-key-here
  BRIEF_LLM_BASE_URL=https://openrouter.ai/api/v1
  BRIEF_LLM_MODEL=google/gemma-3-12b-it:free

  # OpenAI (direct)
  OPENAI_API_KEY=sk-...

  # Ollama (local, free)
  BRIEF_LLM_BASE_URL=http://localhost:11434/v1
  BRIEF_LLM_MODEL=llama3
  BRIEF_LLM_API_KEY=ollama
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Depth-aware prompts ──────────────────────────────────────────
# Each depth produces a genuinely different LLM answer.

_PROMPTS = {
    0: {
        "system": (
            "You answer in exactly ONE sentence. No preamble, no key points. "
            "Respond with JSON: {\"summary\": \"one sentence\", \"key_points\": []}"
        ),
        "user": "In one sentence, does this content cover: {query}?\n\n{text}",
        "user_generic": "In one sentence, what is this about?\n\n{text}",
        "max_tokens": 100,
    },
    1: {
        "system": (
            "You summarize content for AI agents. Be factual, direct, concise. "
            "Lead with the answer to the question, not a generic description. "
            "Respond with JSON: {\"summary\": \"2-3 sentences\", \"key_points\": [\"point1\", \"point2\", ...]}"
        ),
        "user": "Answer this question about the content: {query}\n\n{text}",
        "user_generic": "Summarize this content:\n\n{text}",
        "max_tokens": 400,
    },
    2: {
        "system": (
            "You are a research analyst. Provide a detailed, thorough analysis. "
            "Include specifics: exact numbers, evidence, nuances, trade-offs, "
            "and anything someone would need to fully understand this topic. "
            "Respond with JSON: {\"summary\": \"detailed analysis, 5-10 sentences\", "
            "\"key_points\": [\"specific point with details\", ...]}"
        ),
        "user": "Deep dive into this topic: {query}\n\nAnalyze thoroughly:\n\n{text}",
        "user_generic": "Provide a detailed analysis of this content:\n\n{text}",
        "max_tokens": 1000,
    },
}


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

    substantive = [t for t in texts if len(t) > 100] or texts
    key_points = [_truncate(t, 120) for t in substantive[::max(1, len(substantive) // 5)]][:5]
    return summary, key_points


def _llm_summary(
    chunks: list[dict[str, Any]],
    query: str | None = None,
    depth: int = 1,
) -> tuple[str, list[str]] | None:
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

    # Feed ALL extracted content — no trimming.
    # The model's context window handles it (32K+ for free models).
    transcript = " ".join(c.get("text", "") for c in chunks)

    # Get depth-specific prompt config
    prompt_cfg = _PROMPTS.get(depth, _PROMPTS[1])
    system_prompt = prompt_cfg["system"]
    max_tokens = prompt_cfg["max_tokens"]

    if query and query != "summarize this content":
        user_content = prompt_cfg["user"].format(query=query, text=transcript)
    else:
        user_content = prompt_cfg["user_generic"].format(text=transcript)

    try:
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        client = OpenAI(**client_kwargs)
        logger.info("Calling LLM: model=%s depth=%d", model, depth)

        # Try with system prompt first, fall back to single user message
        # (some free models don't support system prompts)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
            )
        except Exception as sys_err:
            if "400" in str(sys_err) or "system" in str(sys_err).lower():
                logger.info("System prompt not supported, retrying as user message")
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "user", "content": f"{system_prompt}\n\n{user_content}"},
                    ],
                    temperature=0.2,
                    max_tokens=max_tokens,
                )
            else:
                raise

        raw = response.choices[0].message.content or ""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        # Parse JSON response
        parsed = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
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
                logger.info("LLM summary generated (%d chars, depth=%d)", len(summary), depth)
                max_summary = {0: 160, 1: 500, 2: 2000}.get(depth, 500)
                max_kp = {0: 0, 1: 120, 2: 300}.get(depth, 120)
                truncated_kp = [_truncate(str(kp), max_kp) for kp in key_points[:8]] if max_kp else []
                return _truncate(summary, max_summary), truncated_kp

        # LLM returned something but it wasn't valid JSON — use raw text as summary
        if raw and len(raw) > 20:
            import sys
            print(f"⚠ LLM returned plain text instead of JSON, using as-is", file=sys.stderr, flush=True)
            logger.warning("LLM response was not JSON (%d chars), using raw text", len(raw))
            max_summary = {0: 160, 1: 500, 2: 2000}.get(depth, 500)
            return _truncate(raw, max_summary), []

    except Exception as exc:
        logger.warning("LLM summary failed (%s): %s", model, exc)

    return None


def summarize(
    chunks: list[dict[str, Any]],
    query: str | None = None,
    depth: int = 1,
) -> tuple[str, list[str]]:
    """Generate a depth-aware, query-focused summary.

    depth=0: one-sentence headline
    depth=1: 2-3 sentence summary + key points
    depth=2: detailed analysis with specifics

    Returns (summary, key_points).
    """
    llm_result = _llm_summary(chunks, query=query, depth=depth)
    if llm_result is not None:
        return llm_result

    logger.debug("Using heuristic summary fallback.")
    return _heuristic_summary(chunks)
