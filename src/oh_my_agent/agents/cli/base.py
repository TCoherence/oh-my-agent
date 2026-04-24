from __future__ import annotations

import asyncio
import json
import logging
import os
from abc import abstractmethod
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

from oh_my_agent.agents.base import AgentResponse, BaseAgent, PartialTextHook
from oh_my_agent.agents.events import (
    AgentEvent,
    CompleteEvent,
    ErrorEvent,
    SystemInitEvent,
    TextEvent,
    UsageEvent,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300

# Environment variable keys considered safe to pass to CLI subprocesses.
# Everything else is stripped unless explicitly listed in passthrough_env.
_SAFE_ENV_KEYS = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "SHELL", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
})

_SESSION_ERROR_MARKERS = (
    "session",
    "resume",
    "conversation",
    "checkpoint",
    "thread",
)

_INVALID_SESSION_MARKERS = (
    "not found",
    "no such",
    "unknown",
    "invalid",
    "expired",
    "missing",
    "does not exist",
    "cannot resume",
    "failed to resume",
)

# Best-effort substring markers for splitting a generic CLI failure into a
# more actionable ``error_kind``. Case-insensitive.
_AUTH_MARKERS = (
    "invalid api key",
    "authentication failed",
    "not authenticated",
    "not logged in",
    "please log in",
    "please login",
    "unauthorized",
    "401",
)
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota exceeded",
    "usage limit",
    "429",
)
_API_5XX_MARKERS = (
    "500 internal",
    "internal server error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "overloaded",
    "upstream",
    "502",
    "503",
    "504",
)


def classify_cli_error_kind(err_msg: str) -> str:
    """Best-effort classify a CLI error message into a retry-meaningful kind.

    Returns one of ``rate_limit``, ``api_5xx``, ``auth``, or ``cli_error``
    (fallback). Callers that already have a more specific kind (e.g.
    ``max_turns`` from parsing a structured result) should skip this.
    """
    lowered = err_msg.lower()
    if any(marker in lowered for marker in _RATE_LIMIT_MARKERS):
        return "rate_limit"
    if any(marker in lowered for marker in _API_5XX_MARKERS):
        return "api_5xx"
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return "auth"
    return "cli_error"


def _extract_cli_error(stderr_raw: bytes, stdout_raw: bytes) -> str:
    """Best-effort extraction of useful CLI error text.

    Some CLIs (e.g. Claude) return structured error details on stdout while
    exiting non-zero, leaving stderr empty.
    """
    stderr_text = stderr_raw.decode(errors="replace").strip()
    if stderr_text:
        return stderr_text

    stdout_text = stdout_raw.decode(errors="replace").strip()
    if not stdout_text:
        return "(no stdout/stderr)"

    try:
        data = json.loads(stdout_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return stdout_text

    if isinstance(data, dict):
        for key in ("error", "result", "message"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        err_obj = data.get("error")
        if isinstance(err_obj, dict):
            msg = err_obj.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()

    return stdout_text


def _build_prompt_with_history(prompt: str, history: list[dict] | None) -> str:
    """Prepend conversation history to the prompt for stateless CLI agents."""
    if not history:
        return prompt

    lines = ["Previous conversation:"]
    for turn in history:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        label = turn.get("author") if role == "user" else turn.get("agent", "assistant")
        lines.append(f"[{label or role}] {content}")

    lines.append("")
    lines.append("Current message:")
    lines.append(prompt)
    return "\n".join(lines)


def _should_clear_resumed_session(err_msg: str) -> bool:
    """Return True when an error looks like an invalid/stale session failure."""
    lowered = err_msg.lower()
    return (
        any(marker in lowered for marker in _SESSION_ERROR_MARKERS)
        and any(marker in lowered for marker in _INVALID_SESSION_MARKERS)
    )


def _bounded_log_excerpt(log_path: Path | None, *, max_chars: int = 2000) -> str | None:
    if log_path is None or not log_path.exists():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return None
    if not text:
        return None
    return text[-max_chars:]


async def _stream_cli_lines(
    *cmd: str,
    cwd: str | None,
    env: dict[str, str],
    timeout: int | None,
    cancel: asyncio.Event | None = None,
    log_path: Path | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """Spawn the CLI subprocess and yield ``(stream_label, line)`` tuples as output arrives.

    ``stream_label`` is ``"stdout"`` / ``"stderr"``. The generator exits normally
    once the subprocess has exited and both pipes are drained. The final
    return code is exposed via :class:`_StreamState` in a side channel — see
    :meth:`BaseCLIAgent.stream` for the typical consumption pattern.

    ``cancel`` — when set during iteration, the subprocess is killed and the
    generator stops. Semantically equivalent to ``AbortSignal.abort()`` — a
    single event threaded from the runtime worker collapses the cancellation
    path down from the current five-hop (intent-parse → DB status write →
    heartbeat poll → Task.cancel → proc.kill).
    """
    log_handle = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8")
        log_handle.write(f"$ {' '.join(cmd)}\n")
        if cwd:
            log_handle.write(f"[cwd] {cwd}\n")
        log_handle.write("\n")
        log_handle.flush()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    # Pump both pipes into a queue; one consumer pulls interleaved frames.
    queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

    async def _pump(stream, label: str) -> None:
        try:
            while True:
                chunk = await stream.readline()
                if not chunk:
                    break
                text = chunk.decode(errors="replace").rstrip("\n")
                if log_handle is not None:
                    log_handle.write(f"[{label}] {text}\n")
                    log_handle.flush()
                await queue.put((label, text))
        finally:
            await queue.put(None)

    stdout_pump = asyncio.create_task(_pump(proc.stdout, "stdout"), name="cli:pump-stdout")
    stderr_pump = asyncio.create_task(_pump(proc.stderr, "stderr"), name="cli:pump-stderr")

    cancel_waiter: asyncio.Task | None = None
    if cancel is not None:
        cancel_waiter = asyncio.create_task(cancel.wait(), name="cli:cancel-wait")

    timeout_waiter: asyncio.Task | None = None
    if timeout is not None:
        timeout_waiter = asyncio.create_task(asyncio.sleep(timeout), name="cli:timeout")

    pumps_remaining = 2
    killed_reason: str | None = None
    try:
        while pumps_remaining > 0:
            get_task = asyncio.create_task(queue.get(), name="cli:queue-get")
            watchers: set[asyncio.Task] = {get_task}
            if cancel_waiter is not None:
                watchers.add(cancel_waiter)
            if timeout_waiter is not None:
                watchers.add(timeout_waiter)

            done, _ = await asyncio.wait(watchers, return_when=asyncio.FIRST_COMPLETED)

            if get_task not in done:
                get_task.cancel()
                with suppress(asyncio.CancelledError):
                    await get_task
                if cancel_waiter is not None and cancel_waiter in done:
                    killed_reason = "cancelled"
                elif timeout_waiter is not None and timeout_waiter in done:
                    killed_reason = "timeout"
                proc.kill()
                break

            item = get_task.result()
            if item is None:
                pumps_remaining -= 1
                continue
            yield item

        # Drain any remaining buffered frames from the pumps after cancel/timeout.
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(stdout_pump, stderr_pump, return_exceptions=True),
                timeout=2,
            )
        while not queue.empty():
            item = queue.get_nowait()
            if item is None:
                continue
            yield item

        # Ensure process is reaped.
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=2)
        if proc.returncode is None:
            proc.kill()
            await proc.wait()

        if killed_reason == "timeout":
            raise asyncio.TimeoutError()
    except (asyncio.CancelledError, GeneratorExit):
        proc.kill()
        with suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=2)
        raise
    finally:
        for t in (cancel_waiter, timeout_waiter, stdout_pump, stderr_pump):
            if t is not None and not t.done():
                t.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await t
        if log_handle is not None:
            log_handle.write(f"\n[exit] {proc.returncode}\n")
            log_handle.close()


async def _stream_cli_process(
    *cmd: str,
    cwd: str | None,
    env: dict[str, str],
    timeout: int,
    log_path: Path | None = None,
) -> tuple[int, bytes, bytes]:
    log_handle = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8")
        log_handle.write(f"$ {' '.join(cmd)}\n")
        if cwd:
            log_handle.write(f"[cwd] {cwd}\n")
        log_handle.write("\n")
        log_handle.flush()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    stdout_buf = bytearray()
    stderr_buf = bytearray()

    async def _pump(stream, buffer: bytearray, label: str) -> None:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            buffer.extend(chunk)
            if log_handle is not None:
                text = chunk.decode(errors="replace")
                log_handle.write(f"[{label}] {text}")
                if not text.endswith("\n"):
                    log_handle.write("\n")
                log_handle.flush()

    stdout_task = asyncio.create_task(_pump(proc.stdout, stdout_buf, "stdout"))
    stderr_task = asyncio.create_task(_pump(proc.stderr, stderr_buf, "stderr"))
    try:
        returncode = await asyncio.wait_for(proc.wait(), timeout=timeout)
        await asyncio.gather(stdout_task, stderr_task)
        return returncode, bytes(stdout_buf), bytes(stderr_buf)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise
    finally:
        if log_handle is not None:
            log_handle.write(f"\n[exit] {proc.returncode}\n")
            log_handle.close()


class BaseCLIAgent(BaseAgent):
    """Base class for agents that wrap a CLI tool as a subprocess.

    Args:
        cli_path: Path or name of the CLI executable.
        timeout: Seconds before the subprocess is killed.
        workspace: Working directory for the subprocess. When set, the agent
            is confined to this directory (all CLI sandboxes are cwd-scoped).
            Also activates environment variable sanitization.
        passthrough_env: Additional env var names to forward to the subprocess
            beyond the safe-key whitelist. Only meaningful when ``workspace``
            is set; ignored in legacy (no-workspace) mode.
    """

    def __init__(
        self,
        cli_path: str,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        workspace: Path | None = None,
        passthrough_env: list[str] | None = None,
        trace_writer=None,
    ) -> None:
        self._cli_path = cli_path
        self._timeout = timeout
        self._workspace = workspace
        self._passthrough_env = passthrough_env  # None = not configured
        self._trace_writer = trace_writer

    def set_trace_writer(self, trace_writer) -> None:
        """Inject a trace writer post-construction (boot plumbing convenience)."""
        self._trace_writer = trace_writer

    async def _emit_trace_events(self, stdout_raw: bytes, *, thread_id: str | None) -> None:
        """Feed parsed stream events to the trace writer, if configured.

        Runs post-hoc over the already-captured stdout so ``run()`` keeps
        its current shape. No-op when no trace writer is set.
        """
        writer = self._trace_writer
        if writer is None:
            return
        try:
            text = stdout_raw.decode(errors="replace")
        except Exception:
            return
        tid = thread_id or "-"
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events = self._parse_stream_line(line)
            except Exception:
                continue
            for event in events:
                try:
                    await writer.append(agent=self.name, thread_id=tid, event=event)
                except Exception:
                    logger.debug("trace_writer.append failed", exc_info=True)

    @property
    def _cwd(self) -> str | None:
        """Working directory for subprocesses, or None to inherit."""
        return str(self._workspace) if self._workspace else None

    def _resolve_cwd(self, workspace_override: Path | None = None) -> str | None:
        if workspace_override is not None:
            return str(workspace_override)
        return self._cwd

    @abstractmethod
    def _build_command(self, prompt: str) -> list[str]:
        """Return the full command to run, with prompt included."""
        ...

    def _build_env(self) -> dict[str, str]:
        """Build the environment dict for the subprocess.

        When ``workspace`` or ``passthrough_env`` is configured, uses a
        whitelist model: only ``_SAFE_ENV_KEYS`` plus explicit
        ``passthrough_env`` keys are forwarded.  Otherwise falls back to the
        full inherited environment (backward-compatible default).
        """
        if self._workspace is None and self._passthrough_env is None:
            # Legacy mode: no workspace configured → inherit full environment.
            env = os.environ.copy()
            env.pop("CLAUDECODE", None)
            return env

        # Whitelist mode: strip secrets from the subprocess environment.
        env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
        for key in (self._passthrough_env or []):
            if key in os.environ:
                env[key] = os.environ[key]
        env.pop("CLAUDECODE", None)
        return env

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        workspace_override: Path | None = None,
        log_path: Path | None = None,
        on_partial: PartialTextHook | None = None,
    ) -> AgentResponse:
        if on_partial is not None:
            return await self._run_streamed(
                prompt=prompt,
                history=history,
                on_partial=on_partial,
                workspace_override=workspace_override,
                log_path=log_path,
            )
        full_prompt = _build_prompt_with_history(prompt, history)
        cmd = self._build_command(full_prompt)
        logger.info("Running %s: %s ...", self.name, " ".join(cmd[:4]))

        try:
            returncode, stdout, stderr = await _stream_cli_process(
                *cmd,
                cwd=self._resolve_cwd(workspace_override),
                env=self._build_env(),
                timeout=self._timeout,
                log_path=log_path,
            )
        except asyncio.TimeoutError:
            return AgentResponse(
                text="",
                error=f"{self.name} CLI timed out after {self._timeout}s",
                error_kind="timeout",
                partial_text=_bounded_log_excerpt(log_path),
                terminal_reason="timeout",
            )
        except FileNotFoundError:
            return AgentResponse(
                text="",
                error=f"{self.name} CLI not found at '{self._cli_path}'. Is it installed?",
                error_kind="cli_error",
            )

        if returncode != 0:
            err_msg = _extract_cli_error(stderr, stdout)
            logger.error("%s CLI failed (rc=%d): %s", self.name, returncode, err_msg)
            return AgentResponse(
                text="",
                error=f"{self.name} exited {returncode}: {err_msg[:400]}",
                error_kind="cli_error",
            )

        return self._parse_output(stdout.decode(errors="replace").strip())

    async def _run_streamed(
        self,
        *,
        prompt: str,
        history: list[dict] | None,
        on_partial: PartialTextHook,
        workspace_override: Path | None,
        log_path: Path | None,
        thread_id: str | None = None,
    ) -> AgentResponse:
        """Drive ``self.stream()`` and build an AgentResponse from the events.

        Subclasses with custom command logic (e.g. session resume) can call this
        directly with ``prompt``/``history`` that already account for resume.
        """
        accumulated: list[str] = []
        usage: dict | None = None
        session_id: str | None = None
        last_error: ErrorEvent | None = None

        try:
            async for event in self.stream(
                prompt,
                history,
                workspace_override=workspace_override,
                log_path=log_path,
            ):
                if isinstance(event, TextEvent):
                    accumulated.append(event.text)
                    with suppress(Exception):
                        await on_partial("\n".join(accumulated))
                elif isinstance(event, SystemInitEvent):
                    if event.session_id:
                        session_id = event.session_id
                elif isinstance(event, UsageEvent):
                    collected = {
                        "input_tokens": event.input_tokens,
                        "output_tokens": event.output_tokens,
                        "cache_read_input_tokens": event.cache_read_input_tokens,
                        "cache_creation_input_tokens": event.cache_creation_input_tokens,
                        "cost_usd": event.cost_usd,
                    }
                    cleaned = {k: v for k, v in collected.items() if v is not None}
                    usage = cleaned or None
                elif isinstance(event, ErrorEvent):
                    last_error = event
                elif isinstance(event, CompleteEvent):
                    if event.session_id and not session_id:
                        session_id = event.session_id
                    if event.text and not accumulated:
                        accumulated.append(event.text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("%s streaming failed: %s", self.name, exc, exc_info=True)
            return AgentResponse(
                text="\n".join(accumulated),
                error=f"{self.name} streaming failed: {exc}",
                error_kind="cli_error",
            )

        if last_error is not None:
            return AgentResponse(
                text="\n".join(accumulated),
                error=last_error.message,
                error_kind=last_error.error_kind or "cli_error",
            )

        text = "\n".join(accumulated)
        if thread_id and session_id and hasattr(self, "_session_ids"):
            try:
                self._session_ids[thread_id] = session_id  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                pass
        return AgentResponse(text=text, usage=usage)

    def _parse_output(self, raw: str) -> AgentResponse:
        """Parse subprocess stdout into an AgentResponse.

        Override in subclasses that emit structured (JSON/JSONL) output.
        The default implementation treats stdout as plain text.
        """
        return AgentResponse(text=raw)

    def _parse_stream_line(self, line: str) -> list[AgentEvent]:
        """Translate one line of CLI stdout into zero or more :class:`AgentEvent`.

        The default implementation wraps the line in a plain :class:`TextEvent`.
        Subclasses that emit structured output (Claude's stream-json, Codex's
        JSONL event stream) override this to return typed ``ToolUseEvent`` /
        ``ThinkingEvent`` / ``SystemInitEvent`` / etc.
        """
        stripped = line.strip()
        if not stripped:
            return []
        return [TextEvent(text=stripped, agent=self.name)]

    async def stream(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        cancel: asyncio.Event | None = None,
        workspace_override: Path | None = None,
        log_path: Path | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run the CLI and yield typed :class:`AgentEvent` objects as they arrive.

        The default implementation shells out via :func:`_stream_cli_lines`,
        calls :meth:`_parse_stream_line` on each stdout line, and yields a
        :class:`CompleteEvent` at the end with the accumulated plain-text view.
        Stderr is buffered and only surfaced (as an :class:`ErrorEvent`) on
        non-zero exit.

        ``cancel`` — optional asyncio.Event. When set, the subprocess is killed
        and the generator exits without yielding a CompleteEvent.
        """
        full_prompt = _build_prompt_with_history(prompt, history)
        cmd = self._build_command(full_prompt)
        logger.info("Streaming %s: %s ...", self.name, " ".join(cmd[:4]))

        collected_text: list[str] = []
        stderr_lines: list[str] = []

        try:
            async for stream_label, line in _stream_cli_lines(
                *cmd,
                cwd=self._resolve_cwd(workspace_override),
                env=self._build_env(),
                timeout=self._timeout,
                cancel=cancel,
                log_path=log_path,
            ):
                if stream_label == "stderr":
                    stderr_lines.append(line)
                    continue
                events = self._parse_stream_line(line)
                for event in events:
                    yield event
                    if isinstance(event, TextEvent):
                        collected_text.append(event.text)
        except asyncio.TimeoutError:
            yield ErrorEvent(
                message=f"{self.name} CLI timed out after {self._timeout}s",
                error_kind="timeout",
                agent=self.name,
            )
            return
        except FileNotFoundError:
            yield ErrorEvent(
                message=f"{self.name} CLI not found at '{self._cli_path}'. Is it installed?",
                error_kind="cli_error",
                agent=self.name,
            )
            return

        if cancel is not None and cancel.is_set():
            # Cancelled mid-stream; skip the complete marker.
            return

        if stderr_lines:
            # Non-fatal stderr still gets surfaced as an error event so the
            # caller can surface it (e.g. into the session diary).
            err_msg = "\n".join(stderr_lines)[-400:]
            yield ErrorEvent(
                message=err_msg,
                error_kind=classify_cli_error_kind(err_msg),
                agent=self.name,
            )

        yield CompleteEvent(
            text="\n".join(collected_text),
            agent=self.name,
        )
