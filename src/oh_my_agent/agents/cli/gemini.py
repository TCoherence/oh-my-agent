from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.cli.base import (
    BaseCLIAgent,
    _build_prompt_with_history,
    _extract_cli_error,
    _should_clear_resumed_session,
    _stream_cli_process,
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
        timeout: int = 300,
        workspace: Path | None = None,
        passthrough_env: list[str] | None = None,
    ) -> None:
        super().__init__(cli_path=cli_path, timeout=timeout, workspace=workspace, passthrough_env=passthrough_env)
        self._model = model
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
        return [
            self._cli_path,
            "-p", prompt,
            "--model", self._model,
            "--yolo",               # non-interactive, auto-approve all tool calls
            "--output-format", "json",  # structured output with session_id + token stats
        ]

    def _build_resume_command(self, prompt: str, session_id: str) -> list[str]:
        """Build a command that resumes an existing Gemini session."""
        return [
            self._cli_path,
            "-p", prompt,
            "--resume", session_id,
            "--model", self._model,
            "--yolo",
            "--output-format", "json",
        ]

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
    ) -> AgentResponse:
        """Run the Gemini CLI.

        If *thread_id* is given and a session ID exists for it, uses
        ``--resume`` to continue the session (avoiding history flattening).
        """
        session_id = self._session_ids.get(thread_id) if thread_id else None
        cwd = self._resolve_cwd(workspace_override)

        # Augment prompt with image references
        if image_paths:
            prompt = self._augment_prompt_with_images(prompt, image_paths, cwd)

        if session_id:
            cmd = self._build_resume_command(prompt, session_id)
            logger.info("Resuming %s session %s ...", self.name, session_id[:12])
        else:
            full_prompt = _build_prompt_with_history(prompt, history)
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
                error_kind="cli_error",
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
