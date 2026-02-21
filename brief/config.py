"""Brief configuration — loads settings from .env file or environment.

Config is loaded from (in priority order):
  1. Environment variables (highest priority)
  2. .env file in current directory
  3. .briefs/.env file
  4. Defaults
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_loaded = False


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple .env file (KEY=VALUE, one per line)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        values[key] = value
    return values


def load_config() -> None:
    """Load config from .env files into os.environ (if not already set)."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    # Check multiple locations
    candidates = [
        Path.cwd() / ".env",
        Path.cwd() / ".briefs" / ".env",
    ]

    for env_path in candidates:
        values = _parse_env_file(env_path)
        if values:
            logger.debug("Loaded config from %s", env_path)
            for key, value in values.items():
                if key not in os.environ:  # env vars take priority
                    os.environ[key] = value
            break  # use first found


def get(key: str, default: str = "") -> str:
    """Get a config value (loads .env on first call)."""
    load_config()
    return os.environ.get(key, default)
