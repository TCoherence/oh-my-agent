from __future__ import annotations

KNOWN_PROVIDERS = {
    "bilibili",
    "youtube",
    "xiaohongshu",
    "xianyu",
}

AUTH_CAPABLE_PROVIDERS = {
    "bilibili",
    "youtube",
    "xiaohongshu",
    "xianyu",
}

_ALIASES = {
    "bili": "bilibili",
    "bilibili": "bilibili",
    "yt": "youtube",
    "youtube": "youtube",
    "xhs": "xiaohongshu",
    "xiaohongshu": "xiaohongshu",
    "hongshu": "xiaohongshu",
    "xianyu": "xianyu",
}


def normalize_provider_name(name: str) -> str | None:
    normalized = str(name).strip().lower()
    if not normalized:
        return None
    resolved = _ALIASES.get(normalized, normalized)
    if resolved in KNOWN_PROVIDERS:
        return resolved
    return None


def is_known_provider(name: str) -> bool:
    return normalize_provider_name(name) is not None
