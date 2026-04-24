"""Throttled message editor for streaming agent output to chat platforms.

Chat platforms (Discord in particular) rate-limit message edits — roughly one
edit per second per message. A naive "edit on every partial" loop burns the
bucket in a few hundred ms and then starts dropping updates. ``StreamingRelay``
is the middle-man that fixes this:

- the caller pumps in the *latest* accumulated assistant text via ``update()``;
- the relay collapses all updates inside the throttle window into a single
  trailing edit scheduled to fire when the window reopens, so the user sees
  ~1 Hz progress without us burning the rate-limit bucket;
- a background heartbeat rewrites the message every ~3 s for the entire
  duration of the run so the elapsed-time stamp on the ``-#`` attribution
  line keeps ticking, both before any text arrives (a ``⏳ *thinking…*``
  placeholder) and during ongoing text streaming;
- on ``finalize()`` the relay flushes the last frame and then appends any
  overflow chunks past the platform's 2000-char message cap as fresh messages
  using the existing ``chunk_message`` splitter.

The relay is deliberately platform-agnostic — it only talks to two methods on
``BaseChannel`` (``send`` + ``edit_message``) — so unit tests can drop a stub
channel in without spinning up Discord.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from oh_my_agent.utils.chunker import chunk_message

logger = logging.getLogger(__name__)


# Discord's message cap is 2000 chars; we leave a small safety margin so the
# attribution prefix + a trailing newline still fit.
_DEFAULT_MAX_MSG_CHARS = 1990

# Heartbeat cadence: how often to rewrite the placeholder while we're still
# waiting for the first TextEvent. 3 s is well above the 1 s min edit interval
# floor and keeps us comfortably under Discord's "5 edits / 5 s per message"
# budget even if a user lowers ``gateway.streaming.min_edit_interval_ms``.
_HEARTBEAT_INTERVAL_SECONDS = 3.0


class StreamingRelay:
    """Edit one chat message in-place as partial agent text arrives.

    Usage::

        relay = StreamingRelay(
            channel=channel,
            thread_id=tid,
            attribution_prefix="-# via **claude**",
        )
        await relay.start("⏳ *working…*")
        async for event in agent.stream(...):
            if isinstance(event, TextEvent):
                await relay.update(accumulated_text)
            elif isinstance(event, ToolUseEvent):
                await relay.note_tool_use(event.name)
        await relay.finalize(full_text, usage=response.usage)
    """

    def __init__(
        self,
        *,
        channel: Any,
        thread_id: str,
        attribution_prefix: str = "",
        min_edit_interval: float = 1.0,
        max_chars: int = _DEFAULT_MAX_MSG_CHARS,
        heartbeat_interval: float = _HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._channel = channel
        self._thread_id = thread_id
        self._attribution_prefix = attribution_prefix
        self._min_edit_interval = max(0.0, float(min_edit_interval))
        self._max_chars = int(max_chars)
        self._heartbeat_interval = max(0.0, float(heartbeat_interval))

        self._message_id: str | None = None
        self._latest_text: str = ""
        self._last_rendered: str | None = None
        self._last_edit_ts: float = 0.0
        self._pending_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._finalized = False

        # Heartbeat / tool-activity state.
        self._placeholder_base: str = ""
        self._start_ts: float = 0.0
        self._heartbeat_task: asyncio.Task | None = None
        self._tool_count: int = 0
        # Tool names in arrival order, no dedup. Drives the live multi-line
        # ``-# 🔧 …`` block (5 names per line). On finalize the block drops
        # and the attribution line gets a ``🔧 N tools`` summary.
        self._tool_trail: list[str] = []

    # --------------------------------------------------------------------
    # Public API
    # --------------------------------------------------------------------

    @property
    def message_id(self) -> str | None:
        """The id of the anchor message (set after ``start``)."""
        return self._message_id

    @property
    def tool_count(self) -> int:
        """Total ``note_tool_use`` calls observed during this run."""
        return self._tool_count

    async def start(self, placeholder: str = "⏳ *thinking…*") -> str | None:
        """Send the anchor message and return its id.

        Called once before the first ``update``. Subsequent calls are no-ops.
        Also launches the heartbeat coroutine that rewrites the placeholder
        with elapsed time until the first real text arrives.
        """
        if self._message_id is not None:
            return self._message_id
        self._placeholder_base = placeholder
        body = self._render(placeholder)
        self._message_id = await self._channel.send(self._thread_id, body)
        self._last_rendered = body
        now = time.monotonic()
        self._last_edit_ts = now
        self._start_ts = now
        if self._message_id is not None and self._heartbeat_interval > 0:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return self._message_id

    async def update(self, text: str) -> None:
        """Record the latest accumulated text; maybe edit the anchor message.

        If we're inside the throttle window, a trailing edit is scheduled.
        Callers can fire this as fast as they want — the relay collapses
        updates. The heartbeat keeps running through the streaming phase
        so the elapsed-time stamp on the attribution line stays fresh
        even between text chunks.
        """
        if self._finalized or self._message_id is None:
            return
        async with self._lock:
            self._latest_text = text
            elapsed = time.monotonic() - self._last_edit_ts
        if elapsed >= self._min_edit_interval:
            await self._flush_once()
        else:
            await self._ensure_pending_flush(self._min_edit_interval - elapsed)

    async def note_tool_use(self, name: str) -> None:
        """Record that a tool was invoked.

        Every invocation appends to ``_tool_trail`` (no dedup — the trail
        IS the trace) and bumps ``_tool_count``. While mid-stream this also
        schedules an anchor edit so the new entry shows up on the next
        ``-# 🔧 …`` line within the throttle window. On finalize the block
        drops and the attribution line gets the ``🔧 N tools`` summary.
        """
        if self._finalized:
            return
        cleaned = (name or "").strip()
        if not cleaned:
            return
        self._tool_trail.append(cleaned)
        self._tool_count += 1
        if self._message_id is None:
            return
        async with self._lock:
            elapsed = time.monotonic() - self._last_edit_ts
        if elapsed >= self._min_edit_interval:
            await self._flush_once()
        else:
            await self._ensure_pending_flush(self._min_edit_interval - elapsed)

    async def finalize(
        self,
        final_text: str,
        *,
        usage: dict | None = None,
        attribution_override: str | None = None,
    ) -> list[str]:
        """Render the final text onto the anchor + any overflow chunks.

        Returns the list of message ids delivered (anchor first, then overflow).
        ``usage`` (optional) is appended to the attribution line on the anchor.
        ``attribution_override`` (optional) replaces the prefix passed in at
        construction — useful when fallback picked a different agent than the
        placeholder assumed.
        """
        if self._finalized:
            return [self._message_id] if self._message_id else []
        self._finalized = True
        self._cancel_heartbeat()
        # Cancel any scheduled trailing edit — we're about to do the real one.
        pending = self._pending_task
        self._pending_task = None
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, Exception):
                pass

        attribution = attribution_override if attribution_override is not None else self._attribution_prefix
        attribution = self._decorate_attribution_for_finalize(attribution)
        if usage:
            try:
                # Local import to avoid a circular reference during test setup
                # (utils.usage imports nothing from gateway, but keeping the
                # relay importable from bare tests is nice).
                from oh_my_agent.utils.usage import append_usage_audit
                attribution = append_usage_audit(attribution, usage)
            except Exception:  # pragma: no cover - defensive
                pass

        # Figure out what will fit on the anchor message vs. overflow.
        first_chunk_budget = max(1, self._max_chars - len(attribution) - 1)
        first_chunks = chunk_message(final_text, max_size=first_chunk_budget)
        if not first_chunks:
            anchor_body = f"{attribution}\n*(empty response)*" if attribution else "*(empty response)*"
            await self._safe_edit(anchor_body)
            return [self._message_id] if self._message_id else []

        first_body = f"{attribution}\n{first_chunks[0]}" if attribution else first_chunks[0]
        await self._safe_edit(first_body)
        delivered: list[str] = [self._message_id] if self._message_id else []

        remainder = final_text[len(first_chunks[0]):].lstrip()
        remaining_chunks = chunk_message(remainder) if remainder else []
        for chunk in remaining_chunks:
            mid = await self._channel.send(self._thread_id, chunk)
            if mid:
                delivered.append(mid)
        return delivered

    async def error(self, message: str) -> None:
        """Replace the anchor with an error banner. Idempotent with finalize."""
        if self._finalized or self._message_id is None:
            return
        self._finalized = True
        self._cancel_heartbeat()
        pending = self._pending_task
        self._pending_task = None
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, Exception):
                pass
        body = f"{self._attribution_prefix}\n❌ {message[:1800]}" if self._attribution_prefix else f"❌ {message[:1800]}"
        await self._safe_edit(body)

    # --------------------------------------------------------------------
    # Internals
    # --------------------------------------------------------------------

    # The live tool trail wraps onto multiple ``-#`` lines — this many tool
    # names per line. Below ~5 the line gets too sparse on Discord; above ~6
    # it starts to wrap on narrower windows.
    _TOOLS_PER_LINE = 5

    def _render(self, body: str) -> str:
        """Join the live (multi-line) attribution block with the body."""
        lines = self._build_attribution_lines()
        if not lines:
            return body
        return "\n".join(lines) + "\n" + body

    def _build_attribution_lines(self) -> list[str]:
        """Current attribution block as ``-#`` subtext lines.

        Layout (pre-finalize):
          line 1: ``{attribution_prefix} · ⏱ Ns`` — elapsed time always
                  present once ``start()`` has run;
          lines 2+: ``-# 🔧 T1 · T2 · T3 · T4 · T5`` — every observed tool
                  call in order (no dedup), wrapped to ``_TOOLS_PER_LINE``
                  per line.
        """
        lines: list[str] = []
        attrib1 = self._attribution_prefix
        if not self._finalized and self._start_ts > 0:
            elapsed = max(0, int(time.monotonic() - self._start_ts))
            suffix = f"⏱ {self._format_elapsed(elapsed)}"
            attrib1 = f"{attrib1} · {suffix}" if attrib1 else f"-# {suffix}"
        if attrib1:
            lines.append(attrib1)
        if not self._finalized and self._tool_trail:
            lines.extend(self._format_tool_trail_lines())
        return lines

    def _format_tool_trail_lines(self) -> list[str]:
        """Multi-line ``-# 🔧 …`` block — every observed tool, no overflow."""
        if not self._tool_trail:
            return []
        out: list[str] = []
        for i in range(0, len(self._tool_trail), self._TOOLS_PER_LINE):
            chunk = self._tool_trail[i : i + self._TOOLS_PER_LINE]
            out.append(f"-# 🔧 {' · '.join(chunk)}")
        return out

    def _decorate_attribution_for_finalize(self, attribution: str) -> str:
        """Append ``· 🔧 N tool(s) · ⏱ Ns`` to the finalized attribution line."""
        parts: list[str] = []
        if self._tool_count > 0:
            plural = "s" if self._tool_count != 1 else ""
            parts.append(f"🔧 {self._tool_count} tool{plural}")
        if self._start_ts > 0:
            elapsed = max(0, int(time.monotonic() - self._start_ts))
            parts.append(f"⏱ {self._format_elapsed(elapsed)}")
        if not parts:
            return attribution
        suffix = " · ".join(parts)
        if not attribution:
            return suffix
        return f"{attribution} · {suffix}"

    @staticmethod
    def _format_elapsed(seconds: int) -> str:
        """Compact human duration: ``24s`` / ``2m 14s`` / ``1h 03m``."""
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            m, s = divmod(seconds, 60)
            return f"{m}m {s:02d}s"
        h, rest = divmod(seconds, 3600)
        m = rest // 60
        return f"{h}h {m:02d}m"

    def _render_heartbeat_body(self) -> str:
        """Placeholder body when no text has arrived yet.

        Elapsed time + tool trail live on the ``-#`` attribution lines, so the
        body itself just shows a calm ``*thinking…*`` cue.
        """
        base = self._placeholder_base.strip() or "⏳ *thinking…*"
        return base

    async def _heartbeat_loop(self) -> None:
        """Refresh the anchor every ``heartbeat_interval`` seconds.

        Runs from ``start()`` through ``finalize()`` / ``error()`` so the
        elapsed-time stamp on the attribution line keeps ticking both before
        and during text streaming. When text has arrived we re-render via
        the regular ``_flush_once`` path (which respects the lock and dedup
        check); when no text yet we re-render the placeholder body.
        """
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                if self._finalized:
                    return
                if self._latest_text:
                    await self._flush_once()
                    continue
                body = self._render(self._render_heartbeat_body())
                async with self._lock:
                    if self._finalized:
                        return
                    if body == self._last_rendered:
                        continue
                    self._last_rendered = body
                    self._last_edit_ts = time.monotonic()
                await self._safe_edit(body)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("StreamingRelay heartbeat failed", exc_info=True)

    def _cancel_heartbeat(self) -> None:
        task = self._heartbeat_task
        if task is None:
            return
        self._heartbeat_task = None
        if not task.done():
            task.cancel()

    async def _flush_once(self) -> None:
        """Render the current latest_text and push one edit if it changed."""
        async with self._lock:
            body = self._render(self._truncate_preview(self._latest_text))
            if body == self._last_rendered:
                return
            self._last_rendered = body
            self._last_edit_ts = time.monotonic()
        await self._safe_edit(body)

    async def _ensure_pending_flush(self, delay: float) -> None:
        """Schedule a single trailing edit to fire when the throttle window reopens."""
        if self._pending_task is not None and not self._pending_task.done():
            return  # a trailing edit is already scheduled
        self._pending_task = asyncio.create_task(self._delayed_flush(delay))

    async def _delayed_flush(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            if self._finalized:
                return
            await self._flush_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("StreamingRelay trailing flush failed", exc_info=True)

    def _truncate_preview(self, text: str) -> str:
        """Clip the live preview so it always fits in a single message."""
        # Account for the full live attribution block (may be 1 OR 2 ``-#``
        # lines) plus the single newline separating it from the body.
        lines = self._build_attribution_lines()
        attrib_block = "\n".join(lines)
        overhead = len(attrib_block) + (1 if attrib_block else 0)
        budget = max(1, self._max_chars - overhead)
        if len(text) <= budget:
            return text
        # Reserve 2 chars for the "\n…" suffix we append after trimming.
        trim_to = max(1, budget - 2)
        head = text[:trim_to]
        # Prefer breaking on a newline just before the trim point.
        last_nl = head.rfind("\n")
        if last_nl >= trim_to - 400:
            head = head[:last_nl]
        return head.rstrip() + "\n…"

    async def _safe_edit(self, body: str) -> None:
        if self._message_id is None:
            return
        try:
            await self._channel.edit_message(self._thread_id, self._message_id, body)
        except Exception:
            logger.debug(
                "StreamingRelay edit failed thread=%s msg=%s",
                self._thread_id,
                self._message_id,
                exc_info=True,
            )
