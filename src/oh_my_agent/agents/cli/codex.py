from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from oh_my_agent.agents.base import AgentResponse, PartialTextHook, ToolUseHook
from oh_my_agent.agents.control_prompt import inject_control_protocol
from oh_my_agent.agents.events import (
    AgentEvent,
    SystemInitEvent,
    TextEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)
from oh_my_agent.agents.cli.base import (
    BaseCLIAgent,
    _build_prompt_with_history,
    _extract_cli_error,
    _should_clear_resumed_session,
    _stream_cli_process,
    classify_cli_error_kind,
)

logger = logging.getLogger(__name__)
_VALID_CODEX_SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}


def _extract_codex_text(event: dict) -> str:
    """Best-effort text extraction from a single Codex JSONL event."""
    # Newer Codex JSONL format:
    # {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
    item = event.get("item")
    if isinstance(item, dict):
        item_type = item.get("type", "")
        # Only include user-facing assistant text; skip reasoning items.
        if item_type in {"agent_message", "assistant_message", "message"}:
            text = item.get("text")
            if isinstance(text, str) and text:
                return text

            content = item.get("content", "")
            if isinstance(content, str) and content:
                return content
            if isinstance(content, list):
                parts = []
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    ctype = c.get("type", "")
                    if ctype in {"text", "output_text"}:
                        ctext = c.get("text", "")
                        if isinstance(ctext, str) and ctext:
                            parts.append(ctext)
                if parts:
                    return " ".join(parts)

    # Assistant message with content list (OpenAI message format)
    if event.get("role") == "assistant":
        content = event.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                c.get("text", "") for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            )
    # Direct text / output / response fields (varies by Codex version)
    for key in ("text", "output", "response"):
        val = event.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


class CodexCLIAgent(BaseCLIAgent):
    """Agent that delegates to the OpenAI ``codex`` CLI.

    Uses ``codex exec`` with explicit sandbox flags by default,
    with configurable sandbox and bypass flags.

    Supports session resume via ``codex exec resume <session_id>``.

    https://github.com/openai/codex
    """

    def __init__(
        self,
        cli_path: str = "codex",
        model: str = "o4-mini",
        skip_git_repo_check: bool = True,
        sandbox_mode: str = "workspace-write",
        dangerously_bypass_approvals_and_sandbox: bool = False,
        extra_args: list[str] | None = None,
        timeout: int = 300,
        workspace: Path | None = None,
        passthrough_env: list[str] | None = None,
    ) -> None:
        super().__init__(cli_path=cli_path, timeout=timeout, workspace=workspace, passthrough_env=passthrough_env)
        self._model = model
        self._skip_git_repo_check = skip_git_repo_check
        normalized_sandbox_mode = str(sandbox_mode).strip() or "workspace-write"
        if normalized_sandbox_mode not in _VALID_CODEX_SANDBOX_MODES:
            raise ValueError(
                f"Unsupported Codex sandbox mode '{sandbox_mode}'. "
                f"Expected one of {sorted(_VALID_CODEX_SANDBOX_MODES)}."
            )
        self._sandbox_mode = normalized_sandbox_mode
        self._dangerously_bypass = bool(dangerously_bypass_approvals_and_sandbox)
        self._extra_args = [str(arg) for arg in (extra_args or []) if str(arg).strip()]
        # thread_id → Codex CLI session ID (thread_id from thread.started event)
        self._session_ids: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "codex"

    def get_session_id(self, thread_id: str) -> str | None:
        return self._session_ids.get(thread_id)

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        self._session_ids[thread_id] = session_id

    def clear_session(self, thread_id: str) -> None:
        self._session_ids.pop(thread_id, None)

    def _automation_flags(self) -> list[str]:
        if self._dangerously_bypass:
            return ["--dangerously-bypass-approvals-and-sandbox"]
        return ["--sandbox", self._sandbox_mode]

    def _resume_automation_flags(self) -> list[str]:
        if self._dangerously_bypass:
            return ["--dangerously-bypass-approvals-and-sandbox"]
        # `codex exec resume` currently does not accept `--sandbox` directly.
        # Use config override so resume runs keep the configured sandbox mode.
        return ["-c", f'sandbox_mode="{self._sandbox_mode}"']

    def _build_command(
        self, prompt: str, *, image_paths: list[Path] | None = None
    ) -> list[str]:
        cmd = [
            self._cli_path,
            "exec",
            *self._automation_flags(),
            "--model", self._model,
            "--json",               # JSONL event stream with usage in turn.completed
        ]
        if self._skip_git_repo_check:
            cmd.append("--skip-git-repo-check")
        if self._extra_args:
            cmd.extend(self._extra_args)
        if image_paths:
            cmd.extend(["--image", ",".join(str(p) for p in image_paths)])
        cmd.append(prompt)
        return cmd

    def _build_resume_command(
        self, prompt: str, session_id: str, *, image_paths: list[Path] | None = None
    ) -> list[str]:
        """Build a command that resumes an existing Codex session."""
        cmd = [
            self._cli_path,
            "exec",
            "resume",
            session_id,
            *self._resume_automation_flags(),
            "--model", self._model,
            "--json",
        ]
        if self._skip_git_repo_check:
            cmd.append("--skip-git-repo-check")
        if self._extra_args:
            cmd.extend(self._extra_args)
        if image_paths:
            cmd.extend(["--image", ",".join(str(p) for p in image_paths)])
        cmd.append(prompt)
        return cmd

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        workspace_override: Path | None = None,
        log_path: Path | None = None,
        image_paths: list[Path] | None = None,
        on_partial: PartialTextHook | None = None,
        on_tool_use: ToolUseHook | None = None,
    ) -> AgentResponse:
        """Run the Codex CLI.

        If *thread_id* is given and a session ID exists for it, uses
        ``codex exec resume`` to continue the session (avoiding history flattening).

        When ``on_partial`` or ``on_tool_use`` is provided, the call switches
        to the streaming path on **both** fresh and resume invocations. Only
        image-bearing turns stay in block mode today (``--image`` argv +
        streaming fold together but we keep the simple path until we need it).
        """
        prompt = inject_control_protocol(prompt)
        session_id = self._session_ids.get(thread_id) if thread_id else None

        streaming = (on_partial is not None or on_tool_use is not None)

        if session_id:
            cmd = self._build_resume_command(prompt, session_id, image_paths=image_paths)
            if streaming and not image_paths:
                logger.info("Streaming %s (resume session %s) ...", self.name, session_id[:12])
                return await self._run_streamed(
                    prompt=prompt,
                    history=None,
                    on_partial=on_partial,
                    on_tool_use=on_tool_use,
                    workspace_override=workspace_override,
                    log_path=log_path,
                    thread_id=thread_id,
                    command=cmd,
                )
            logger.info("Resuming %s session %s ...", self.name, session_id[:12])
        else:
            full_prompt = _build_prompt_with_history(prompt, history)
            if streaming and not image_paths:
                logger.info("Streaming %s (new session) ...", self.name)
                return await self._run_streamed(
                    prompt=full_prompt,
                    history=None,
                    on_partial=on_partial,
                    on_tool_use=on_tool_use,
                    workspace_override=workspace_override,
                    log_path=log_path,
                    thread_id=thread_id,
                )
            cmd = self._build_command(full_prompt, image_paths=image_paths)
            logger.info("Running %s (new session) ...", self.name)

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
            return AgentResponse(
                text="",
                error=f"{self.name} exited {returncode}: {err_msg[:400]}",
                error_kind=classify_cli_error_kind(err_msg),
            )

        raw = stdout.decode(errors="replace").strip()

        await self._emit_trace_events(stdout, thread_id=thread_id)

        # Capture Codex session ID from the thread.started event
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if event.get("type") == "thread.started":
                    new_session_id = event.get("thread_id")
                    if new_session_id and thread_id:
                        self._session_ids[thread_id] = new_session_id
                        logger.info(
                            "Stored %s session %s for thread %s",
                            self.name, new_session_id[:12], thread_id,
                        )
                    break
            except json.JSONDecodeError:
                continue

        return self._parse_output(raw)

    def _parse_stream_line(self, line: str) -> list[AgentEvent]:
        """Map one JSONL line of Codex event output to AgentEvents.

        Mirrors Agentara's Codex event mapping: ``thread.started`` sets up the
        session, ``item.started`` / ``item.completed`` frames carry typed
        payloads (agent_message, reasoning, command_execution, file_change,
        mcp_tool_call, web_search), and ``turn.completed`` carries token usage.
        """
        stripped = line.strip()
        if not stripped:
            return []
        try:
            event = json.loads(stripped)
        except (json.JSONDecodeError, TypeError, ValueError):
            return []
        if not isinstance(event, dict):
            return []

        etype = event.get("type", "")
        if etype == "thread.started":
            return [
                SystemInitEvent(
                    session_id=event.get("thread_id"),
                    raw=event,
                    agent=self.name,
                )
            ]

        if etype == "turn.completed":
            usage_obj = event.get("usage") or {}
            return [
                UsageEvent(
                    input_tokens=usage_obj.get("input_tokens"),
                    output_tokens=usage_obj.get("output_tokens"),
                    cache_read_input_tokens=usage_obj.get("cached_input_tokens"),
                    agent=self.name,
                )
            ]

        item = event.get("item")
        if not isinstance(item, dict):
            return []

        item_type = item.get("type", "")
        item_id = str(item.get("id") or "")

        if item_type in {"agent_message", "assistant_message", "message"}:
            # Codex re-emits the same item on item.started and item.completed;
            # only take the completed frame to avoid duplicating streamed text.
            if etype != "item.completed":
                return []
            text = _extract_codex_text(event)
            if not text:
                return []
            return [TextEvent(text=text, agent=self.name)]

        if item_type == "reasoning":
            if etype != "item.completed":
                return []
            text = item.get("text") or item.get("summary") or ""
            if not isinstance(text, str) or not text:
                return []
            return [ThinkingEvent(text=text, agent=self.name)]

        if item_type == "command_execution":
            if etype == "item.started":
                command = item.get("command") or item.get("cmd") or ""
                return [
                    ToolUseEvent(
                        tool_id=item_id,
                        name="Bash",
                        input={"command": command} if command else {},
                        agent=self.name,
                    )
                ]
            if etype == "item.completed":
                output = (
                    item.get("aggregated_output")
                    or item.get("output")
                    or item.get("stdout")
                    or ""
                )
                is_error = bool(item.get("exit_code", 0))
                return [
                    ToolResultEvent(
                        tool_id=item_id,
                        name="Bash",
                        output=str(output),
                        is_error=is_error,
                        agent=self.name,
                    )
                ]
            return []

        if item_type == "file_change":
            if etype != "item.completed":
                return []
            # Each change frame can list multiple path edits.
            changes = item.get("changes") or []
            if not isinstance(changes, list):
                changes = [item]
            out: list[AgentEvent] = []
            for change in changes:
                if not isinstance(change, dict):
                    continue
                op = str(change.get("operation") or change.get("op") or "edit")
                path = change.get("path") or item.get("path") or ""
                out.append(
                    ToolUseEvent(
                        tool_id=item_id,
                        name=op.capitalize() if op else "Edit",
                        input={"path": str(path)},
                        agent=self.name,
                    )
                )
            return out

        if item_type == "mcp_tool_call":
            name = str(item.get("name") or "MCP")
            if etype == "item.started":
                args = item.get("arguments") or item.get("input") or {}
                if not isinstance(args, dict):
                    args = {"raw": str(args)}
                return [
                    ToolUseEvent(
                        tool_id=item_id,
                        name=name,
                        input=args,
                        agent=self.name,
                    )
                ]
            if etype == "item.completed":
                output = item.get("output") or item.get("result") or ""
                is_error = bool(item.get("is_error") or item.get("error"))
                return [
                    ToolResultEvent(
                        tool_id=item_id,
                        name=name,
                        output=str(output),
                        is_error=is_error,
                        agent=self.name,
                    )
                ]

        if item_type == "web_search":
            if etype == "item.started":
                return [
                    ToolUseEvent(
                        tool_id=item_id,
                        name="WebSearch",
                        input={"query": str(item.get("query") or "")},
                        agent=self.name,
                    )
                ]
            if etype == "item.completed":
                return [
                    ToolResultEvent(
                        tool_id=item_id,
                        name="WebSearch",
                        output=str(item.get("result") or item.get("output") or ""),
                        agent=self.name,
                    )
                ]

        return []

    def _parse_output(self, raw: str) -> AgentResponse:
        """Parse Codex JSONL event stream to extract response text and token usage."""
        text_parts: list[str] = []
        usage: dict | None = None
        saw_json = False

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                saw_json = True
                etype = event.get("type", "")

                if etype == "turn.completed":
                    u = event.get("usage", {})
                    if u:
                        usage = {
                            "input_tokens": u.get("input_tokens", 0),
                            "output_tokens": u.get("output_tokens", 0),
                            # Codex names its cache field "cached_input_tokens"
                            "cache_read_input_tokens": u.get("cached_input_tokens", 0),
                        }
                else:
                    text = _extract_codex_text(event)
                    if text:
                        text_parts.append(text)

            except json.JSONDecodeError:
                # Non-JSON line → treat as plain text (shouldn't happen with --json)
                text_parts.append(line)

        # If JSON was parsed but no text extracted, fall back to raw output
        text = "\n".join(text_parts).strip() or raw.strip()
        if saw_json and not text_parts:
            logger.warning("codex --json: no text extracted from JSONL events, using raw output")

        return AgentResponse(text=text, usage=usage)
