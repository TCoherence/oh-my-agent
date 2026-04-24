from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


async def record_usage_from_response(
    store: Any,
    *,
    agent: str,
    source: str,
    platform: str | None = None,
    channel_id: str | None = None,
    thread_id: str | None = None,
    response: Any = None,
    task_id: str | None = None,
    model: str | None = None,
) -> None:
    """Persist a usage sample from an agent response.

    Safe to call with ``store=None`` / missing ``record_usage_event`` /
    ``response.usage=None`` — any of those silently no-op. ``source`` tags
    where the usage came from (``chat``, ``runtime``, ``automation``, ``judge``)
    so the ledger can be queried by provenance.
    """
    if store is None or not hasattr(store, "record_usage_event"):
        return
    usage: Mapping[str, Any] | None = getattr(response, "usage", None) if response is not None else None
    if not usage:
        return
    try:
        await store.record_usage_event(
            agent=agent,
            source=source,
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
            model=model,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cache_read_input_tokens=usage.get("cache_read_input_tokens"),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens"),
            cost_usd=usage.get("cost_usd"),
            task_id=task_id,
        )
    except Exception:
        logger.warning("record_usage_event failed (source=%s agent=%s)", source, agent, exc_info=True)


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
