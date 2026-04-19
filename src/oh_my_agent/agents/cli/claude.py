from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.control_prompt import inject_control_protocol
from oh_my_agent.agents.cli.base import (
    BaseCLIAgent,
    _bounded_log_excerpt,
    _build_prompt_with_history,
    _extract_cli_error,
    _should_clear_resumed_session,
    _stream_cli_process,
    classify_cli_error_kind,
)

logger = logging.getLogger(__name__)


def _parse_claude_stream_json(raw: str) -> tuple[str | None, dict | None]:
    """Parse Claude CLI stream-json NDJSON stdout.

    Returns ``(init_session_id, final_frame)`` where:
    - ``init_session_id`` is taken from the first ``type=system subtype=init`` event.
    - ``final_frame`` is the last ``type=result`` event (shape identical to
      the legacy ``--output-format json`` single-frame response).

    Falls back to treating ``raw`` as a single JSON object when NDJSON parsing
    finds no ``result`` event. This keeps compatibility with error paths where
    the Claude CLI currently emits a single frame (e.g. ``error_max_turns``).
    """
    init_session_id: str | None = None
    final_frame: dict | None = None
    stream_saw_events = False

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(ev, dict):
            continue
        stream_saw_events = True
        ev_type = ev.get("type")
        if (
            ev_type == "system"
            and ev.get("subtype") == "init"
            and init_session_id is None
        ):
            sid = ev.get("session_id")
            if isinstance(sid, str) and sid:
                init_session_id = sid
        elif ev_type == "result":
            final_frame = ev

    # Fallback: stdout may be a single JSON object (legacy single-frame mode
    # or an error frame that isn't NDJSON-formatted).
    if final_frame is None and not stream_saw_events:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                final_frame = data
                if init_session_id is None:
                    sid = data.get("session_id")
                    if isinstance(sid, str) and sid:
                        init_session_id = sid
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    return init_session_id, final_frame


class ClaudeAgent(BaseCLIAgent):
    """Agent that delegates to the ``claude`` CLI.

    Uses ``--output-format stream-json --verbose`` so the CLI emits an NDJSON
    event stream (system init, assistant text/thinking/tool_use, user
    tool_result, rate_limit_event, final result). The stream is tee'd into the
    agent log for per-turn visibility, matching the Codex ``--json`` behavior.

    Supports fresh and session resume modes:

    - **Fresh**: flattens history into the prompt, stores the ``session_id``
      from the ``type=result`` (or ``type=system init``) event.
    - **Session resume**: ``--resume <session_id>`` to continue a prior
      session without re-flattening history.
    """

    def __init__(
        self,
        cli_path: str = "claude",
        max_turns: int = 25,
        allowed_tools: list[str] | None = None,
        model: str = "sonnet",
        dangerously_skip_permissions: bool = True,
        permission_mode: str | None = None,
        extra_args: list[str] | None = None,
        timeout: int = 300,
        workspace: Path | None = None,
        passthrough_env: list[str] | None = None,
    ) -> None:
        super().__init__(cli_path=cli_path, timeout=timeout, workspace=workspace, passthrough_env=passthrough_env)
        self._max_turns = max_turns
        self._allowed_tools = allowed_tools or []
        self._model = model
        self._dangerously_skip_permissions = bool(dangerously_skip_permissions)
        normalized_permission_mode = str(permission_mode).strip() if permission_mode is not None else None
        self._permission_mode = normalized_permission_mode or None
        self._extra_args = [str(arg) for arg in (extra_args or []) if str(arg).strip()]
        # thread_id → Claude CLI session ID (for --resume)
        self._session_ids: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "claude"

    def get_session_id(self, thread_id: str) -> str | None:
        return self._session_ids.get(thread_id)

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        self._session_ids[thread_id] = session_id

    def clear_session(self, thread_id: str) -> None:
        self._session_ids.pop(thread_id, None)

    def _base_command(self, prompt: str) -> list[str]:
        cmd = [
            self._cli_path,
            "-p", prompt,
            "--max-turns", str(self._max_turns),
            "--model", self._model,
        ]
        if self._permission_mode:
            cmd.extend(["--permission-mode", self._permission_mode])
        elif self._dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if self._allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self._allowed_tools)])
        if self._extra_args:
            cmd.extend(self._extra_args)
        return cmd

    def _build_command(self, prompt: str) -> list[str]:
        cmd = self._base_command(prompt)
        cmd.extend(["--output-format", "stream-json", "--verbose"])
        return cmd

    def _build_resume_command(self, prompt: str, session_id: str) -> list[str]:
        """Build a command that resumes an existing Claude session."""
        cmd = [
            self._cli_path,
            "-p", prompt,
            "--resume", session_id,
            "--output-format", "stream-json",
            "--verbose",
            "--max-turns", str(self._max_turns),
            "--model", self._model,
        ]
        if self._permission_mode:
            cmd.extend(["--permission-mode", self._permission_mode])
        elif self._dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if self._allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self._allowed_tools)])
        if self._extra_args:
            cmd.extend(self._extra_args)
        return cmd

    def _augment_prompt_with_images(
        self, prompt: str, image_paths: list[Path], cwd: str | Path | None
    ) -> str:
        """Copy images to the workspace and prepend Read-tool instructions to the prompt."""
        if not image_paths:
            return prompt
        lines: list[str] = []
        cwd_path = Path(cwd) if cwd else None
        for img in image_paths:
            if not img.is_file():
                continue
            if cwd_path:
                dest_dir = cwd_path / "_attachments"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / img.name
                shutil.copy2(img, dest)
                ref = f"_attachments/{img.name}"
            else:
                ref = str(img)
            lines.append(
                f"Read the image file at `{ref}` using the Read tool to see its contents."
            )
        if not lines:
            return prompt
        return "\n".join(lines) + "\n\n" + prompt

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        workspace_override: Path | None = None,
        log_path: Path | None = None,
        image_paths: list[Path] | None = None,
    ) -> AgentResponse:
        """Run the Claude CLI.

        If *thread_id* is given and a session ID exists for it, uses
        ``--resume`` to continue the session (avoiding history flattening).
        """
        prompt = inject_control_protocol(prompt)
        session_id = self._session_ids.get(thread_id) if thread_id else None
        cwd = self._resolve_cwd(workspace_override)

        # Augment prompt with image references
        if image_paths:
            prompt = self._augment_prompt_with_images(prompt, image_paths, cwd)

        if session_id:
            # Resume existing session — send only the new prompt
            cmd = self._build_resume_command(prompt, session_id)
            logger.info("Resuming %s session %s ...", self.name, session_id[:12])
        else:
            # Fresh session — flatten history into prompt
            full_prompt = _build_prompt_with_history(prompt, history)
            cmd = self._base_command(full_prompt)
            cmd.extend(["--output-format", "stream-json", "--verbose"])
            logger.info("Running %s (new session) ...", self.name)

        try:
            returncode, stdout, stderr = await _stream_cli_process(
                *cmd,
                cwd=cwd,
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
            # Only discard a stored session when the CLI reports it as invalid/stale.
            if session_id and thread_id and _should_clear_resumed_session(err_msg):
                self.clear_session(thread_id)
            partial_text = None
            terminal_reason = None
            error_kind = "cli_error"
            raw_error: dict | None = None
            stdout_text = stdout.decode(errors="replace").strip()
            if stdout_text:
                try:
                    raw_error = json.loads(stdout_text)
                except (json.JSONDecodeError, TypeError, ValueError):
                    raw_error = None
            if isinstance(raw_error, dict):
                if str(raw_error.get("subtype", "")).strip() == "error_max_turns":
                    error_kind = "max_turns"
                    terminal_reason = "max_turns"
                    partial_text = (
                        raw_error.get("result")
                        if isinstance(raw_error.get("result"), str) and raw_error.get("result", "").strip()
                        else None
                    )
                if terminal_reason is None:
                    raw_terminal = raw_error.get("terminal_reason")
                    if isinstance(raw_terminal, str) and raw_terminal.strip():
                        terminal_reason = raw_terminal.strip()
            if error_kind == "cli_error":
                error_kind = classify_cli_error_kind(err_msg)
            partial_text = partial_text or _bounded_log_excerpt(log_path)
            return AgentResponse(
                text="",
                raw=raw_error,
                error=f"{self.name} exited {returncode}: {err_msg[:400]}",
                error_kind=error_kind,
                partial_text=partial_text,
                terminal_reason=terminal_reason,
            )

        raw = stdout.decode(errors="replace").strip()

        # Parse stream-json NDJSON output: pull session_id from init event and
        # pull the final result / usage / cost from the last result event.
        init_session_id, final_frame = _parse_claude_stream_json(raw)

        if final_frame is None:
            # Degraded: no result frame found. Return raw so caller isn't
            # silently given an empty string.
            if init_session_id and thread_id:
                self._session_ids[thread_id] = init_session_id
            return AgentResponse(text=raw)

        text = final_frame.get("result")
        if not isinstance(text, str) or not text:
            text = raw

        new_session_id = final_frame.get("session_id") or init_session_id
        if new_session_id and thread_id:
            self._session_ids[thread_id] = new_session_id
            logger.info(
                "Stored session %s for thread %s", new_session_id[:12], thread_id
            )

        # Collect usage info: token counts + optional cost.
        # Claude CLI outputs "total_cost_usd" (not "cost_usd").
        usage: dict | None = None
        cost = final_frame.get("total_cost_usd") or final_frame.get("cost_usd")
        if "usage" in final_frame or cost is not None:
            usage = {**final_frame.get("usage", {})}
            if cost is not None:
                usage["cost_usd"] = cost

        return AgentResponse(text=text, raw=final_frame, usage=usage)
