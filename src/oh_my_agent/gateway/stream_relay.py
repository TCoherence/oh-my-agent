"""Throttled message editor for streaming agent output to chat platforms.

Chat platforms (Discord in particular) rate-limit message edits — roughly one
edit per second per message. A naive "edit on every partial" loop burns the
bucket in a few hundred ms and then starts dropping updates. ``StreamingRelay``
is the middle-man that fixes this:

- the caller pumps in the *latest* accumulated assistant text via ``update()``;
- the relay collapses all updates inside the throttle window into a single
  trailing edit scheduled to fire when the window reopens, so the user sees
  ~1 Hz progress without us burning the rate-limit bucket;
- while no text has arrived yet (the model is reasoning / running tools),
  a background heartbeat rewrites the placeholder with an elapsed-time suffix
  and the most recent tool name so the user can tell the run is still alive
  instead of staring at a frozen ``⏳ *thinking…*``;
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
        self._last_tool_name: str | None = None
        self._tool_count: int = 0

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
        updates.
        """
        if self._finalized or self._message_id is None:
            return
        self._cancel_heartbeat()
        async with self._lock:
            self._latest_text = text
            elapsed = time.monotonic() - self._last_edit_ts
        if elapsed >= self._min_edit_interval:
            await self._flush_once()
        else:
            await self._ensure_pending_flush(self._min_edit_interval - elapsed)

    async def note_tool_use(self, name: str) -> None:
        """Record that a tool was invoked.

        We only track the *name* and a running count — no arguments, no
        output. The heartbeat picks up the new tool name on its next tick;
        ``finalize()`` appends ``· 🔧 N tools`` to the attribution. Callers
        that don't want this signal never invoke the hook.
        """
        if self._finalized:
            return
        cleaned = (name or "").strip()
        if not cleaned:
            return
        self._last_tool_name = cleaned
        self._tool_count += 1

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
        attribution = self._decorate_attribution_with_tools(attribution)
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

    def _render(self, body: str) -> str:
        if self._attribution_prefix:
            return f"{self._attribution_prefix}\n{body}"
        return body

    def _decorate_attribution_with_tools(self, attribution: str) -> str:
        """Append ``· 🔧 N tool(s)`` to the attribution if any were seen."""
        if self._tool_count <= 0:
            return attribution
        suffix = f" · 🔧 {self._tool_count} tool{'s' if self._tool_count != 1 else ''}"
        if not attribution:
            return suffix.lstrip(" ·")
        return f"{attribution}{suffix}"

    def _render_heartbeat_body(self) -> str:
        """Build the placeholder body for the current heartbeat tick."""
        elapsed = max(0, int(time.monotonic() - self._start_ts))
        # Start from the original placeholder label and append status, so users
        # who configured a custom placeholder still see their text.
        base = self._placeholder_base.strip() or "⏳ *thinking…*"
        # If the base is a classic italic placeholder like ``⏳ *thinking…*``,
        # inject elapsed/tool status inside the italics so formatting stays
        # tight. Otherwise just append plainly.
        italic_body = base.startswith("*") and base.endswith("*") and len(base) >= 2
        if italic_body:
            inner = base[1:-1]
        else:
            # Treat forms like ``⏳ *thinking…*`` where the emoji prefix sits
            # outside the italic span.
            lead, sep, rest = base.partition("*")
            if sep and rest.endswith("*") and len(rest) >= 1:
                inner = rest[:-1]
                prefix = lead
            else:
                inner = None
                prefix = ""
        status_bits = [f"({elapsed}s)"]
        if self._last_tool_name:
            status_bits.append(f"using {self._last_tool_name}")
        status = " · ".join(status_bits)
        if italic_body:
            return f"*{inner} · {status}*"
        if inner is not None:
            return f"{prefix}*{inner} · {status}*"
        return f"{base} · {status}"

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                if self._finalized or self._latest_text:
                    return
                body = self._render(self._render_heartbeat_body())
                async with self._lock:
                    if self._finalized or self._latest_text:
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
        # 1 char reserved for the newline between attribution and body.
        budget = max(1, self._max_chars - len(self._attribution_prefix) - 1)
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
