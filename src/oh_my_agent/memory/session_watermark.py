"""Per-(thread, agent) CLI-session "last seen turn" watermark protocol.

Shared by every ``thread_id``-keyed agent resume site (the gateway reply path
and the RuntimeService auth-suspended / HITL resume entries) so a CLI session is
resumed **only when it has seen every persisted turn** that predates the new
input. Any turn a *different* actor wrote to the thread (a runtime task, an
automation, a foreign reply) bumps the ``turns`` table id without advancing the
session's watermark, so the next gated resume deterministically re-seeds fresh.

The protocol is expressed with two pure decision functions (``should_resume`` /
``can_advance_watermark``) plus a small async wrapper that performs the advance.
Each call site supplies its own ``owned_turn_ids`` — the rows *this* invocation
appended/incorporated (its user turn, its visible/assistant output, a HITL
answer, …) — so those are never mistaken for foreign writes.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any


def _baseline(last_seen_turn_id: int | None) -> int:
    # ``None`` (never advanced / fresh row) means "nothing seen" → -1 so every
    # real turn id (>= 1) counts as unseen.
    return last_seen_turn_id if last_seen_turn_id is not None else -1


def session_is_stale(
    *,
    last_seen_turn_id: int | None,
    existing_turn_ids: Sequence[int],
    owned_turn_ids: Iterable[int],
) -> bool:
    """True when a resume would skip a turn the session never saw.

    A turn is "unseen" if its id is above the watermark and was not produced by
    this invocation. Caller checks session existence separately.
    """
    owned = set(owned_turn_ids)
    base = _baseline(last_seen_turn_id)
    return any(tid > base and tid not in owned for tid in existing_turn_ids)


def should_resume(
    *,
    session_exists: bool,
    last_seen_turn_id: int | None,
    existing_turn_ids: Sequence[int],
    owned_turn_ids: Iterable[int],
) -> bool:
    """Resume iff a session exists AND it is not stale (see ``session_is_stale``)."""
    if not session_exists:
        return False
    return not session_is_stale(
        last_seen_turn_id=last_seen_turn_id,
        existing_turn_ids=existing_turn_ids,
        owned_turn_ids=owned_turn_ids,
    )


def can_advance_watermark(
    *,
    baseline_id: int | None,
    candidate_id: int,
    existing_turn_ids: Sequence[int],
    owned_turn_ids: Iterable[int],
) -> bool:
    """True iff the watermark may advance to ``candidate_id``.

    Advance only when every persisted turn in ``(baseline_id, candidate_id]`` is
    one this invocation owns — i.e. nothing foreign was appended to the thread
    *during* this invocation's execution. Otherwise the watermark must stay put
    so the next gated resume re-seeds fresh.
    """
    owned = set(owned_turn_ids)
    base = _baseline(baseline_id)
    return not any(
        base < tid <= candidate_id and tid not in owned for tid in existing_turn_ids
    )


async def persisted_turn_ids(
    store: Any, platform: str, channel_id: str, thread_id: str
) -> list[int]:
    """Real (non-summary) persisted turn ids for a thread, ascending."""
    rows = await store.load_history(platform, channel_id, thread_id)
    return [r["_id"] for r in rows if isinstance(r.get("_id"), int)]


async def advance_watermark_if_clean(
    *,
    store: Any,
    platform: str,
    channel_id: str,
    thread_id: str,
    agent: str,
    baseline_id: int | None,
    owned_turn_ids: Iterable[int],
) -> bool:
    """Advance the persisted watermark to ``max(owned_turn_ids)`` when clean.

    Reads the turn ids **fresh** (to catch a foreign interleave that landed
    during execution) and only advances when ``can_advance_watermark`` holds.
    Returns whether the watermark advanced.
    """
    owned = set(owned_turn_ids)
    if not owned:
        return False
    candidate_id = max(owned)
    ids = await persisted_turn_ids(store, platform, channel_id, thread_id)
    if not can_advance_watermark(
        baseline_id=baseline_id,
        candidate_id=candidate_id,
        existing_turn_ids=ids,
        owned_turn_ids=owned,
    ):
        return False
    await store.update_session_watermark(
        platform, channel_id, thread_id, agent, candidate_id
    )
    return True
