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
        # Elapsed time renders as `⏱ Ns` on the attribution line.
        import re as _re
        assert _re.search(r"⏱ \d+s", last_body), last_body
        # And preserve the italic "thinking" cue.
        assert "thinking" in last_body
    finally:
        await relay.finalize("wrap up")


@pytest.mark.asyncio
async def test_heartbeat_surfaces_tool_trail_on_second_subtext_line():
    """During heartbeat, tool names live on a dedicated ``-# 🔧 …`` line
    (not glued into the body) so they don't crowd the thinking placeholder."""
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
        # Tool trail is its own `-#` line, not merged into the body.
        assert "-# 🔧 Read" in last_body
        # Body itself stays the calm placeholder.
        assert last_body.strip().endswith("*thinking…*")
        # Elapsed lives on the first `-#` line, not in the body.
        lines = last_body.split("\n")
        assert lines[-1].strip() == "⏳ *thinking…*"
    finally:
        await relay.finalize("done")


@pytest.mark.asyncio
async def test_tool_trail_appears_during_streaming_phase():
    """After real text has arrived (heartbeat dead), a subsequent tool event
    must still surface on the ``-# 🔧 …`` trail line via an anchor edit."""
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        min_edit_interval=0.0,
        heartbeat_interval=0.0,  # no heartbeat interference
    )
    await relay.start("⏳ *thinking…*")
    await relay.update("first chunk of real text")
    # Now in streaming phase — heartbeat is dead, latest_text is non-empty.
    await relay.note_tool_use("Glob")
    assert ch.edits, "tool event during streaming should trigger an edit"
    last_body = ch.edits[-1][2]
    # Body keeps the live text, trail lands on the second `-#` line.
    assert "first chunk of real text" in last_body
    assert "-# 🔧 Glob" in last_body
    # First attribution line stays clean (no elapsed now that heartbeat is over).
    first_line = last_body.split("\n", 1)[0]
    assert "⏳" not in first_line


@pytest.mark.asyncio
async def test_tool_trail_keeps_full_trace_no_dedup():
    """Every tool invocation lands on the trail in arrival order — including
    consecutive duplicates. The trail IS the trace; we don't collapse it."""
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        min_edit_interval=0.0,
        heartbeat_interval=0.0,
    )
    await relay.start("⏳ *thinking…*")
    await relay.update("streaming…")
    for _ in range(5):
        await relay.note_tool_use("Bash")
    await relay.note_tool_use("Read")
    await relay.note_tool_use("Read")
    last_body = ch.edits[-1][2]
    trail_lines = [ln for ln in last_body.split("\n") if "🔧" in ln]
    # 7 entries wrap to two lines at 5 names per line.
    assert trail_lines == [
        "-# 🔧 Bash · Bash · Bash · Bash · Bash",
        "-# 🔧 Read · Read",
    ]
    assert relay.tool_count == 7


@pytest.mark.asyncio
async def test_tool_trail_wraps_to_multiple_lines_at_five_per_line():
    """All tool entries shown — every 5 entries roll into a new -# 🔧 line."""
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        min_edit_interval=0.0,
        heartbeat_interval=0.0,
    )
    await relay.start("⏳ *thinking…*")
    await relay.update("streaming…")
    for name in ("Glob", "Read", "Bash", "Edit", "Write", "Search", "Replace"):
        await relay.note_tool_use(name)
    last_body = ch.edits[-1][2]
    trail_lines = [ln for ln in last_body.split("\n") if "🔧" in ln]
    # 7 distinct names span two `-# 🔧` lines, no `(+N)` overflow.
    assert trail_lines == [
        "-# 🔧 Glob · Read · Bash · Edit · Write",
        "-# 🔧 Search · Replace",
    ]


@pytest.mark.asyncio
async def test_finalize_drops_live_trail_line_keeps_count_summary():
    """The live trail (``-# 🔧 A · B · C``) is for progress; on finalize we
    collapse it back to a single ``🔧 N tools`` figure on the attribution."""
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        min_edit_interval=0.0,
        heartbeat_interval=0.0,
    )
    await relay.start("⏳ *thinking…*")
    for name in ("Glob", "Read", "Bash"):
        await relay.note_tool_use(name)
    await relay.finalize("all done")
    last_body = ch.edits[-1][2]
    # Exactly one `-#` line (the summarized attribution), plus the final body.
    subtext_lines = [ln for ln in last_body.split("\n") if ln.startswith("-#")]
    assert len(subtext_lines) == 1
    # And that line has the count summary plus elapsed-time stamp, not the trail.
    assert "🔧 3 tools" in subtext_lines[0]
    assert "⏱ " in subtext_lines[0]
    assert " · " in subtext_lines[0]  # separator between attribution + count


@pytest.mark.asyncio
async def test_heartbeat_keeps_running_through_streaming_phase():
    """Heartbeat lives until ``finalize()``/``error()`` so the elapsed-time
    stamp on the attribution line keeps ticking even after the first text
    chunk arrives. (Previous behavior: heartbeat cancelled on first update.)

    Note: the elapsed display rounds down to whole seconds, so we need to
    sleep ≥ 1.0 s for the rendered ``⏱ Ns`` value to actually change and
    cause a real edit (otherwise the dedup guard in ``_flush_once`` would
    suppress the heartbeat-driven edit even though it fired).
    """
    ch = FakeChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        heartbeat_interval=0.5,
        min_edit_interval=0.0,
    )
    await relay.start("⏳ *thinking…*")
    await relay.update("real text arrived")
    edits_after_update = len(ch.edits)
    # ≥1.05 s lets the integer-second display roll from "0s" → "1s" so the
    # dedup guard releases and the heartbeat-driven edit actually lands.
    await asyncio.sleep(1.1)
    assert len(ch.edits) > edits_after_update, (
        "heartbeat should keep refreshing past first update()"
    )
    last_body = ch.edits[-1][2]
    # The streamed text is preserved …
    assert "real text arrived" in last_body
    # … and the attribution still carries the live `⏱ Ns` stamp with N≥1.
    import re as _re
    m = _re.search(r"⏱ (\d+)s", last_body)
    assert m is not None, last_body
    assert int(m.group(1)) >= 1
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


class _SlowEditChannel(FakeChannel):
    """Test channel where one edit_message call can be parked on a gate.

    ``arm_slow()`` flags the NEXT edit_message call as slow. That call
    signals when it parks (``_slow_started``) and waits on
    ``_slow_release`` before recording. All other calls record immediately,
    matching Discord accepting edits in arrival order.
    """

    def __init__(self) -> None:
        super().__init__()
        self._slow_release = asyncio.Event()
        self._slow_started = asyncio.Event()
        self._slow_armed = False

    def arm_slow(self) -> None:
        self._slow_armed = True

    def release_slow(self) -> None:
        self._slow_release.set()

    async def edit_message(
        self, thread_id: str, message_id: str, text: str
    ) -> None:
        cancelled_after_record = False
        if self._slow_armed:
            self._slow_armed = False  # consume the arm flag
            self._slow_started.set()
            try:
                await self._slow_release.wait()
            except asyncio.CancelledError:
                # Simulate Discord having already processed the edit before
                # asyncio-side cancel propagated — the HTTP request may have
                # been delivered server-side even though our coroutine was
                # cancelled. Record the edit and re-raise so the caller still
                # observes the cancellation.
                cancelled_after_record = True
        self.edits.append((thread_id, message_id, text))
        if cancelled_after_record:
            raise asyncio.CancelledError()


@pytest.mark.asyncio
async def test_heartbeat_inflight_edit_does_not_overwrite_finalize():
    """Race regression for task [67217ed9]: a heartbeat-driven ``_safe_edit``
    that is mid-flight when ``finalize()`` runs must NOT clobber the finalize
    body.

    Scenario:
      1. Heartbeat fires, takes the lock, computes the live body (claude
         attribution + ⏱ Ns + tool-trail), releases the lock, awaits
         ``_safe_edit(live_body)``.
      2. The Discord edit hangs (slow rate-limit slot / network).
      3. Codex finishes; manager calls
         ``relay.finalize(text, attribution_override="-# via **codex**")``.
      4. Finalize must wait for the in-flight heartbeat edit before issuing
         its own — otherwise the heartbeat edit lands LAST and the user
         sees the live streaming format (the [67217ed9] symptom).
    """
    ch = _SlowEditChannel()
    relay = StreamingRelay(
        channel=ch,
        thread_id="t",
        attribution_prefix="-# via **claude**",
        min_edit_interval=0.0,
        heartbeat_interval=0.02,
    )
    # Seed live state so the heartbeat body is meaningfully different from
    # the finalize body (matches the [67217ed9] observation: 2 `-# 🔧 Bash`
    # lines on the live body, single attribution line on finalize).
    relay._tool_trail.extend(["Bash", "Bash"])
    relay._tool_count = 2
    relay._latest_text = "partial assistant text"  # heartbeat will _flush_once

    ch.arm_slow()
    await relay.start("⏳ *thinking…*")

    # Wait for the heartbeat-driven slow edit to actually be parked inside
    # edit_message. (relay.start itself records once before the slow gate
    # arms — that's fine; we want the SECOND edit, the heartbeat one, to be
    # the slow one.) Re-arm the gate for the heartbeat call.
    if not ch._slow_armed:
        ch.arm_slow()
    try:
        await asyncio.wait_for(ch._slow_started.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pytest.fail("expected a heartbeat edit to start within 1s")

    # Codex finishes; manager calls finalize while the heartbeat edit is
    # still parked. With the fix, finalize awaits the heartbeat task before
    # issuing its own edit, so the heartbeat edit records FIRST and the
    # finalize edit records LAST.
    final_text = "All done — codex finished."
    finalize_task = asyncio.create_task(
        relay.finalize(final_text, attribution_override="-# via **codex**")
    )
    await asyncio.sleep(0.05)
    ch.release_slow()
    await asyncio.wait_for(finalize_task, timeout=1.0)
    await asyncio.sleep(0.05)

    assert ch.edits, "expected at least one edit to have been recorded"
    last_body = ch.edits[-1][2]
    assert "via **codex**" in last_body, (
        f"final edit should carry the finalize attribution, got: {last_body!r}"
    )
    assert final_text in last_body, (
        f"final edit should carry the final response text, got: {last_body!r}"
    )
    assert "-# 🔧 Bash" not in last_body, (
        "final edit should NOT contain the live tool-trail block "
        f"(heartbeat in-flight edit clobbered finalize): {last_body!r}"
    )


def test_format_elapsed_under_one_minute_uses_seconds():
    assert StreamingRelay._format_elapsed(0) == "0s"
    assert StreamingRelay._format_elapsed(1) == "1s"
    assert StreamingRelay._format_elapsed(59) == "59s"


def test_format_elapsed_minute_range_uses_m_s():
    assert StreamingRelay._format_elapsed(60) == "1m 00s"
    assert StreamingRelay._format_elapsed(75) == "1m 15s"
    assert StreamingRelay._format_elapsed(3599) == "59m 59s"


def test_format_elapsed_hour_range_uses_h_m():
    assert StreamingRelay._format_elapsed(3600) == "1h 00m"
    assert StreamingRelay._format_elapsed(3780) == "1h 03m"
    assert StreamingRelay._format_elapsed(7320) == "2h 02m"


