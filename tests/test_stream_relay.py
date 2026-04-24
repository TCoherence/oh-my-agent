from __future__ import annotations

import asyncio

import pytest

from oh_my_agent.gateway.stream_relay import StreamingRelay


class FakeChannel:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []  # (thread, text)
        self.edits: list[tuple[str, str, str]] = []  # (thread, msg_id, text)
        self._next_id = 1000

    async def send(self, thread_id: str, text: str) -> str:
        mid = str(self._next_id)
        self._next_id += 1
        self.sent.append((thread_id, text))
        return mid

    async def edit_message(self, thread_id: str, message_id: str, text: str) -> None:
        self.edits.append((thread_id, message_id, text))


class RaisingEditChannel(FakeChannel):
    async def edit_message(self, thread_id: str, message_id: str, text: str) -> None:  # noqa: ARG002
        raise RuntimeError("kaboom")


@pytest.mark.asyncio
async def test_start_sends_placeholder_and_returns_id():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch, thread_id="t", attribution_prefix="-# via **claude**"
    )
    mid = await relay.start("⏳ thinking…")
    assert mid == "1000"
    assert relay.message_id == "1000"
    assert len(ch.sent) == 1
    assert "claude" in ch.sent[0][1]
    assert "thinking" in ch.sent[0][1]


@pytest.mark.asyncio
async def test_start_is_idempotent():
    ch = FakeChannel()
    relay = StreamingRelay(channel=ch, thread_id="t")
    mid1 = await relay.start("hi")
    mid2 = await relay.start("hi")
    assert mid1 == mid2
    assert len(ch.sent) == 1


@pytest.mark.asyncio
async def test_update_without_start_is_noop():
    ch = FakeChannel()
    relay = StreamingRelay(channel=ch, thread_id="t")
    await relay.update("partial")  # no start yet
    assert ch.edits == []


@pytest.mark.asyncio
async def test_update_collapses_bursty_writes():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch, thread_id="t", min_edit_interval=0.05
    )
    await relay.start("…")
    # Burst of rapid updates — they should collapse to ONE trailing edit
    # after the interval window elapses.
    await relay.update("a")
    await relay.update("ab")
    await relay.update("abc")
    await asyncio.sleep(0.15)  # let trailing edit fire
    # We expect at most two edits (initial trailing flush + possibly a leading
    # edit from the first update that was inside the window).
    assert len(ch.edits) <= 2
    assert ch.edits[-1][2].endswith("abc") or "abc" in ch.edits[-1][2]


@pytest.mark.asyncio
async def test_finalize_flushes_full_text_and_handles_overflow():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        min_edit_interval=0.01,
        max_chars=120,  # tiny cap forces overflow
    )
    await relay.start("…")
    long_text = "A" * 400
    delivered = await relay.finalize(long_text)
    # Anchor + overflow messages
    assert len(ch.edits) >= 1
    assert len(ch.sent) >= 2  # initial placeholder + at least one overflow
    # Total delivered content (edit body + any sends after placeholder) should
    # contain all 400 chars of A.
    final_edit_body = ch.edits[-1][2]
    overflow_bodies = [text for _, text in ch.sent[1:]]
    total = final_edit_body + "".join(overflow_bodies)
    assert total.count("A") == 400
    # delivered list starts with anchor id.
    assert delivered[0] == relay.message_id


@pytest.mark.asyncio
async def test_finalize_with_empty_text():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch, thread_id="t", attribution_prefix="-# via **claude**"
    )
    await relay.start("…")
    await relay.finalize("")
    # Anchor rewritten with "empty response" marker.
    assert ch.edits
    assert "empty response" in ch.edits[-1][2]


@pytest.mark.asyncio
async def test_finalize_cancels_pending_trailing_edit():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch, thread_id="t", min_edit_interval=5.0  # long window
    )
    await relay.start("…")
    await relay.update("partial-A")
    # A trailing task has been scheduled (because the window is long).
    assert relay._pending_task is not None
    await relay.finalize("final text")
    # After finalize, the pending task is cleared (either awaited or cancelled).
    assert relay._pending_task is None
    # Final anchor body contains "final text".
    assert ch.edits
    assert "final text" in ch.edits[-1][2]


@pytest.mark.asyncio
async def test_error_replaces_anchor_and_makes_finalize_noop():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch, thread_id="t", attribution_prefix="-# via **claude**"
    )
    await relay.start("…")
    await relay.error("rate limit")
    assert ch.edits
    assert "rate limit" in ch.edits[-1][2]
    # Subsequent finalize should be a no-op.
    before = len(ch.edits)
    await relay.finalize("would-be final")
    assert len(ch.edits) == before


@pytest.mark.asyncio
async def test_edit_errors_are_swallowed():
    ch = RaisingEditChannel()
    relay = StreamingRelay(channel=ch, thread_id="t", min_edit_interval=0.0)
    await relay.start("…")
    # Should not raise even though edit_message blows up.
    await relay.update("hello")
    await relay.finalize("done")  # relay must not propagate


@pytest.mark.asyncio
async def test_finalize_without_start_is_empty():
    ch = FakeChannel()
    relay = StreamingRelay(channel=ch, thread_id="t")
    delivered = await relay.finalize("anything")
    # No anchor ever created — relay degrades gracefully.
    assert delivered == []
    assert ch.edits == []


@pytest.mark.asyncio
async def test_truncate_preview_keeps_message_under_cap():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        min_edit_interval=0.0,
        max_chars=120,
    )
    await relay.start("…")
    long_preview = "X" * 1000
    await relay.update(long_preview)
    await asyncio.sleep(0.02)  # allow any trailing edit
    # Every edit body must be <= 120 chars (our max_chars cap).
    for _, _, body in ch.edits:
        assert len(body) <= 120


@pytest.mark.asyncio
async def test_note_tool_use_records_count_and_last_name():
    ch = FakeChannel()
    relay = StreamingRelay(channel=ch, thread_id="t")
    await relay.start("…")
    await relay.note_tool_use("Read")
    await relay.note_tool_use("Bash")
    await relay.note_tool_use("")  # empty/whitespace → ignored
    await relay.note_tool_use("   ")
    assert relay.tool_count == 2


@pytest.mark.asyncio
async def test_finalize_attribution_includes_tool_count_suffix():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        min_edit_interval=0.0,
    )
    await relay.start("…")
    await relay.note_tool_use("Read")
    await relay.note_tool_use("Edit")
    await relay.finalize("all done")
    assert ch.edits
    last_body = ch.edits[-1][2]
    # Pluralized "tools" (count=2), on the attribution line
    assert "🔧 2 tools" in last_body
    assert "all done" in last_body


@pytest.mark.asyncio
async def test_finalize_attribution_singular_tool():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        min_edit_interval=0.0,
    )
    await relay.start("…")
    await relay.note_tool_use("Read")
    await relay.finalize("done")
    last_body = ch.edits[-1][2]
    assert "🔧 1 tool" in last_body
    assert "🔧 1 tools" not in last_body  # singular


@pytest.mark.asyncio
async def test_finalize_no_tools_no_suffix():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        min_edit_interval=0.0,
    )
    await relay.start("…")
    await relay.finalize("done")
    last_body = ch.edits[-1][2]
    assert "🔧" not in last_body


@pytest.mark.asyncio
async def test_heartbeat_rewrites_placeholder_with_elapsed_time():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        heartbeat_interval=0.04,
    )
    await relay.start("⏳ *thinking…*")
    # Wait long enough for at least one heartbeat tick.
    await asyncio.sleep(0.12)
    try:
        # There should be at least one heartbeat-driven edit, and no
        # real "update()" text was ever passed.
        assert ch.edits, "heartbeat should have produced at least one edit"
        last_body = ch.edits[-1][2]
        # Format should include an elapsed-seconds bit like "(Ns)".
        import re as _re
        assert _re.search(r"\(\d+s\)", last_body), last_body
        # And preserve the italic "thinking" cue.
        assert "thinking" in last_body
    finally:
        await relay.finalize("wrap up")


@pytest.mark.asyncio
async def test_heartbeat_includes_tool_name_when_known():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        heartbeat_interval=0.04,
    )
    await relay.start("⏳ *thinking…*")
    await relay.note_tool_use("Read")
    await asyncio.sleep(0.12)
    try:
        assert ch.edits, "expected at least one heartbeat edit"
        last_body = ch.edits[-1][2]
        assert "using Read" in last_body
    finally:
        await relay.finalize("done")


@pytest.mark.asyncio
async def test_heartbeat_cancelled_on_first_update():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        heartbeat_interval=0.04,
        min_edit_interval=0.0,
    )
    await relay.start("⏳ *thinking…*")
    await relay.update("real text arrived")
    # Sleep past two heartbeat cycles; no further placeholder edits should land.
    edits_after_update = len(ch.edits)
    await asyncio.sleep(0.15)
    # The heartbeat task has been cancelled, so no NEW placeholder edits
    # should appear. Only the (already fired) update edit is in place.
    assert len(ch.edits) == edits_after_update
    await relay.finalize("done")


@pytest.mark.asyncio
async def test_heartbeat_cancelled_on_finalize():
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        heartbeat_interval=0.04,
    )
    await relay.start("⏳ *thinking…*")
    # Finalize before any heartbeat tick fires.
    await relay.finalize("shortcut")
    # Background task should have been cancelled/cleared.
    assert relay._heartbeat_task is None
    # And no further placeholder edits should arrive.
    before = len(ch.edits)
    await asyncio.sleep(0.12)
    assert len(ch.edits) == before


