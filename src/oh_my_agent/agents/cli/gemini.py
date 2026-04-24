from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from oh_my_agent.agents.base import AgentResponse, PartialTextHook, ToolUseHook
from oh_my_agent.agents.cli.base import (
    BaseCLIAgent,
    _build_prompt_with_history,
    _extract_cli_error,
    _should_clear_resumed_session,
    _stream_cli_process,
    classify_cli_error_kind,
)
from oh_my_agent.agents.control_prompt import inject_control_protocol
from oh_my_agent.agents.events import (
    AgentEvent,
    SystemInitEvent,
    TextEvent,
    UsageEvent,
)

logger = logging.getLogger(__name__)


class GeminiCLIAgent(BaseCLIAgent):
    """Agent that delegates to the `gemini` CLI (Google Gemini CLI).

    Supports session resume via ``--resume <session_id>``.

    https://github.com/google-gemini/gemini-cli
    """

    def __init__(
        self,
        cli_path: str = "gemini",
        model: str = "gemini-3-flash-preview",
        yolo: bool = True,
        extra_args: list[str] | None = None,
        timeout: int = 300,
        workspace: Path | None = None,
        passthrough_env: list[str] | None = None,
    ) -> None:
        super().__init__(cli_path=cli_path, timeout=timeout, workspace=workspace, passthrough_env=passthrough_env)
        self._model = model
        self._yolo = bool(yolo)
        self._extra_args = [str(arg) for arg in (extra_args or []) if str(arg).strip()]
        # thread_id → Gemini CLI session ID (for --resume)
        self._session_ids: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "gemini"

    def get_session_id(self, thread_id: str) -> str | None:
        return self._session_ids.get(thread_id)

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        self._session_ids[thread_id] = session_id

    def clear_session(self, thread_id: str) -> None:
        self._session_ids.pop(thread_id, None)

    def _build_command(self, prompt: str) -> list[str]:
        cmd = [
            self._cli_path,
            "-p", prompt,
            "--model", self._model,
            "--output-format", "json",  # structured output with session_id + token stats
        ]
        if self._yolo:
            cmd.append("--yolo")  # non-interactive, auto-approve all tool calls
        if self._extra_args:
            cmd.extend(self._extra_args)
        return cmd

    def _build_resume_command(self, prompt: str, session_id: str) -> list[str]:
        """Build a command that resumes an existing Gemini session."""
        cmd = [
            self._cli_path,
            "-p", prompt,
            "--resume", session_id,
            "--model", self._model,
            "--output-format", "json",
        ]
        if self._yolo:
            cmd.append("--yolo")
        if self._extra_args:
            cmd.extend(self._extra_args)
        return cmd

    def _augment_prompt_with_images(
        self, prompt: str, image_paths: list[Path], cwd: str | Path | None
    ) -> str:
        """Copy images to the workspace and prepend file-reference instructions."""
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
            lines.append(f"An image file is available at `{ref}`. Please read and analyze it.")
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
        on_partial: PartialTextHook | None = None,
        on_tool_use: ToolUseHook | None = None,
    ) -> AgentResponse:
        """Run the Gemini CLI.

        If *thread_id* is given and a session ID exists for it, uses
        ``--resume`` to continue the session (avoiding history flattening).

        When ``on_partial`` or ``on_tool_use`` is provided and no images are
        attached, the call switches to the streaming path. Gemini currently
        emits only a final JSON object, so ``on_tool_use`` in practice never
        fires for this agent — the plumbing is still accepted for symmetry.
        """
        prompt = inject_control_protocol(prompt)
        session_id = self._session_ids.get(thread_id) if thread_id else None
        cwd = self._resolve_cwd(workspace_override)

        # Augment prompt with image references
        if image_paths:
            prompt = self._augment_prompt_with_images(prompt, image_paths, cwd)

        streaming = (on_partial is not None or on_tool_use is not None)

        if session_id:
            cmd = self._build_resume_command(prompt, session_id)
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
            cmd = self._build_command(full_prompt)
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

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            # Gemini occasionally returns plain text even with --output-format json
            return AgentResponse(text=raw)

        text = data.get("response", "")
        if not text:
            return AgentResponse(text=raw)

        # Capture and store session_id for future resume
        new_session_id = data.get("session_id")
        if new_session_id and thread_id:
            self._session_ids[thread_id] = new_session_id
            logger.info("Stored %s session %s for thread %s", self.name, new_session_id[:12], thread_id)

        # Extract token stats from stats.models.<model-name>.tokens
        usage: dict | None = None
        models = data.get("stats", {}).get("models", {})
        if models:
            total_prompt = total_candidates = total_cached = 0
            for model_stats in models.values():
                tokens = model_stats.get("tokens", {})
                total_prompt += tokens.get("prompt", 0)
                total_candidates += tokens.get("candidates", 0)
                total_cached += tokens.get("cached", 0)
            if total_prompt or total_candidates:
                usage = {
                    "input_tokens": total_prompt,
                    "output_tokens": total_candidates,
                    "cache_read_input_tokens": total_cached,
                }

        return AgentResponse(text=text, raw=data, usage=usage)

    def _extract_usage_from_stats(self, stats: dict | None) -> dict | None:
        if not isinstance(stats, dict):
            return None
        models = stats.get("models")
        if not isinstance(models, dict) or not models:
            return None
        total_prompt = total_candidates = total_cached = 0
        for model_stats in models.values():
            if not isinstance(model_stats, dict):
                continue
            tokens = model_stats.get("tokens") or {}
            if not isinstance(tokens, dict):
                continue
            total_prompt += int(tokens.get("prompt", 0) or 0)
            total_candidates += int(tokens.get("candidates", 0) or 0)
            total_cached += int(tokens.get("cached", 0) or 0)
        if not (total_prompt or total_candidates):
            return None
        return {
            "input_tokens": total_prompt,
            "output_tokens": total_candidates,
            "cache_read_input_tokens": total_cached,
        }

    def _parse_stream_line(self, line: str) -> list[AgentEvent]:
        """Map one line of Gemini stdout to AgentEvents.

        Gemini ``--output-format json`` emits a single JSON object once the
        full response is ready (not per-token), so the streaming path
        would otherwise show users the raw JSON literal. Detect that here
        and extract the ``response`` field. Plaintext fallback for
        non-JSON lines preserves progressive display when the CLI is
        invoked without ``--output-format json``.
        """
        stripped = line.strip()
        if not stripped:
            return []
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
            except (json.JSONDecodeError, TypeError, ValueError):
                data = None
            if isinstance(data, dict):
                events: list[AgentEvent] = []
                sid = data.get("session_id")
                if isinstance(sid, str) and sid:
                    events.append(SystemInitEvent(session_id=sid, raw=data, agent=self.name))
                text = data.get("response")
                if isinstance(text, str) and text:
                    events.append(TextEvent(text=text, agent=self.name))
                usage = self._extract_usage_from_stats(data.get("stats"))
                if usage:
                    events.append(
                        UsageEvent(
                            input_tokens=usage.get("input_tokens"),
                            output_tokens=usage.get("output_tokens"),
                            cache_read_input_tokens=usage.get("cache_read_input_tokens"),
                            agent=self.name,
                        )
                    )
                if events:
                    return events
        return [TextEvent(text=stripped, agent=self.name)]

    def _parse_output(self, raw: str) -> AgentResponse:
        """Parse Gemini JSON output (used by base class run(); session_id not captured here)."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return AgentResponse(text=raw)

        text = data.get("response", "")
        if not text:
            return AgentResponse(text=raw)

        usage: dict | None = None
        models = data.get("stats", {}).get("models", {})
        if models:
            total_prompt = total_candidates = total_cached = 0
            for model_stats in models.values():
                tokens = model_stats.get("tokens", {})
                total_prompt += tokens.get("prompt", 0)
                total_candidates += tokens.get("candidates", 0)
                total_cached += tokens.get("cached", 0)
            if total_prompt or total_candidates:
                usage = {
                    "input_tokens": total_prompt,
                    "output_tokens": total_candidates,
                    "cache_read_input_tokens": total_cached,
                }

        return AgentResponse(text=text, raw=data, usage=usage)
