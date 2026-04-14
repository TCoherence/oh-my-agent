from __future__ import annotations

from typing import Any, Mapping


def format_usage_audit(usage: Mapping[str, Any] | None) -> str:
    """Format token/cache/cost usage into a compact audit suffix."""
    if not usage:
        return ""

    parts: list[str] = []
    input_tok = usage.get("input_tokens")
    output_tok = usage.get("output_tokens")
    input_val = int(input_tok or 0) if input_tok is not None else 0
    output_val = int(output_tok or 0) if output_tok is not None else 0
    if input_val or output_val:
        parts.append(f"{input_val:,} in / {output_val:,} out")

    cache_read = usage.get("cache_read_input_tokens")
    cache_write = usage.get("cache_creation_input_tokens")
    if cache_read and cache_write:
        parts.append(f"cache {int(cache_read):,}r/{int(cache_write):,}w")
    elif cache_read:
        parts.append(f"cache {int(cache_read):,}r")
    elif cache_write:
        parts.append(f"cache {int(cache_write):,}w")

    cost = usage.get("cost_usd")
    if cost is not None:
        try:
            parts.append(f"${float(cost):.4f}")
        except (TypeError, ValueError):
            pass

    return " · ".join(parts)


def append_usage_audit(prefix: str, usage: Mapping[str, Any] | None) -> str:
    audit = format_usage_audit(usage)
    return f"{prefix} · {audit}" if audit else prefix
