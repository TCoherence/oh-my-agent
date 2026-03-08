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
    config_path = Path(path).expanduser().resolve()

    # Prefer .env next to the selected config file. This works reliably when
    # code is installed in a different prefix (e.g. Docker image) than the
    # runtime-mounted workspace/repository.
    load_dotenv(dotenv_path=config_path.parent / ".env", override=False)
    # Keep default discovery as a compatibility fallback.
    load_dotenv(override=False)

    raw = config_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    return _substitute(data)
