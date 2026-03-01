from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.cli.base import (
    BaseCLIAgent,
    _build_prompt_with_history,
    _extract_cli_error,
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

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        workspace_override: Path | None = None,
        log_path: Path | None = None,
    ) -> AgentResponse:
        """Run the Gemini CLI.

        If *thread_id* is given and a session ID exists for it, uses
        ``--resume`` to continue the session (avoiding history flattening).
        """
        session_id = self._session_ids.get(thread_id) if thread_id else None

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
                cwd=self._resolve_cwd(workspace_override),
                env=self._build_env(),
                timeout=self._timeout,
                log_path=log_path,
            )
        except asyncio.TimeoutError:
            return AgentResponse(text="", error=f"{self.name} CLI timed out after {self._timeout}s")
        except FileNotFoundError:
            return AgentResponse(
                text="",
                error=f"{self.name} CLI not found at '{self._cli_path}'. Is it installed?",
            )

        if returncode != 0:
            err_msg = _extract_cli_error(stderr, stdout)
            logger.error("%s CLI failed (rc=%d): %s", self.name, returncode, err_msg)
            # If resume fails, clear the session so next attempt starts fresh
            if session_id and thread_id:
                self.clear_session(thread_id)
            return AgentResponse(
                text="",
                error=f"{self.name} exited {returncode}: {err_msg[:400]}",
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
