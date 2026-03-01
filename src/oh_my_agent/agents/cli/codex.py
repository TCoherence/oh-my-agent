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

    Uses ``codex exec --full-auto`` which auto-approves all tool calls
    and runs in a sandboxed environment by default.

    Supports session resume via ``codex exec resume <session_id>``.

    https://github.com/openai/codex
    """

    def __init__(
        self,
        cli_path: str = "codex",
        model: str = "o4-mini",
        skip_git_repo_check: bool = True,
        timeout: int = 300,
        workspace: Path | None = None,
        passthrough_env: list[str] | None = None,
    ) -> None:
        super().__init__(cli_path=cli_path, timeout=timeout, workspace=workspace, passthrough_env=passthrough_env)
        self._model = model
        self._skip_git_repo_check = skip_git_repo_check
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

    def _build_command(self, prompt: str) -> list[str]:
        cmd = [
            self._cli_path,
            "exec",
            "--full-auto",          # auto-approve + workspace sandbox
            "--model", self._model,
            "--json",               # JSONL event stream with usage in turn.completed
        ]
        if self._skip_git_repo_check:
            cmd.append("--skip-git-repo-check")
        cmd.append(prompt)
        return cmd

    def _build_resume_command(self, prompt: str, session_id: str) -> list[str]:
        """Build a command that resumes an existing Codex session."""
        cmd = [
            self._cli_path,
            "exec",
            "resume",
            session_id,
            "--full-auto",
            "--model", self._model,
            "--json",
        ]
        if self._skip_git_repo_check:
            cmd.append("--skip-git-repo-check")
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
    ) -> AgentResponse:
        """Run the Codex CLI.

        If *thread_id* is given and a session ID exists for it, uses
        ``codex exec resume`` to continue the session (avoiding history flattening).
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
