from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Regex for ${VAR_NAME} substitution
_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def _substitute(value: Any) -> Any:
    """Recursively replace ${VAR} with environment variable values in strings."""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _substitute(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(item) for item in value]
    return value


def load_config(path: str | Path = "config.yaml") -> dict:
    """Load config.yaml with ${ENV_VAR} substitution from the environment."""
    load_dotenv()
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    return _substitute(data)
