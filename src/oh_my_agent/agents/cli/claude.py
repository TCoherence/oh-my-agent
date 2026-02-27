from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.cli.base import BaseCLIAgent, _build_prompt_with_history, _extract_cli_error

logger = logging.getLogger(__name__)


class ClaudeAgent(BaseCLIAgent):
    """Agent that delegates to the ``claude`` CLI.

    Supports batch and session resume modes:

    - **Batch**: ``--output-format json`` (default, extracts session_id)
    - **Session resume**: ``--resume <session_id>`` to continue a prior session
      without re-flattening history.
    """

    def __init__(
        self,
        cli_path: str = "claude",
        max_turns: int = 25,
        allowed_tools: list[str] | None = None,
        model: str = "sonnet",
        timeout: int = 300,
        workspace: Path | None = None,
        passthrough_env: list[str] | None = None,
    ) -> None:
        super().__init__(cli_path=cli_path, timeout=timeout, workspace=workspace, passthrough_env=passthrough_env)
        self._max_turns = max_turns
        self._allowed_tools = allowed_tools or []
        self._model = model
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
            "--dangerously-skip-permissions",
        ]
        if self._allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self._allowed_tools)])
        return cmd

    def _build_command(self, prompt: str) -> list[str]:
        cmd = self._base_command(prompt)
        cmd.extend(["--output-format", "text"])
        return cmd

    def _build_resume_command(self, prompt: str, session_id: str) -> list[str]:
        """Build a command that resumes an existing Claude session."""
        cmd = [
            self._cli_path,
            "-p", prompt,
            "--resume", session_id,
            "--output-format", "json",
            "--max-turns", str(self._max_turns),
            "--model", self._model,
            "--dangerously-skip-permissions",
        ]
        if self._allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self._allowed_tools)])
        return cmd

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
    ) -> AgentResponse:
        """Run the Claude CLI.

        If *thread_id* is given and a session ID exists for it, uses
        ``--resume`` to continue the session (avoiding history flattening).
        """
        session_id = self._session_ids.get(thread_id) if thread_id else None

        if session_id:
            # Resume existing session — send only the new prompt
            cmd = self._build_resume_command(prompt, session_id)
            logger.info("Resuming %s session %s ...", self.name, session_id[:12])
        else:
            # Fresh session — flatten history into prompt
            full_prompt = _build_prompt_with_history(prompt, history)
            cmd = self._base_command(full_prompt)
            cmd.extend(["--output-format", "json"])
            logger.info("Running %s (new session) ...", self.name)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self._cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return AgentResponse(text="", error=f"{self.name} CLI timed out after {self._timeout}s")
        except FileNotFoundError:
            return AgentResponse(
                text="",
                error=f"{self.name} CLI not found at '{self._cli_path}'. Is it installed?",
            )

        if proc.returncode != 0:
            err_msg = _extract_cli_error(stderr, stdout)
            logger.error("%s CLI failed (rc=%d): %s", self.name, proc.returncode, err_msg)
            # If resume fails, clear the session so next attempt starts fresh
            if session_id and thread_id:
                self.clear_session(thread_id)
            return AgentResponse(
                text="",
                error=f"{self.name} exited {proc.returncode}: {err_msg[:400]}",
            )

        raw = stdout.decode(errors="replace").strip()

        # Parse JSON output to extract result, session_id, and usage
        try:
            data = json.loads(raw)
            text = data.get("result", raw)
            new_session_id = data.get("session_id")
            if new_session_id and thread_id:
                self._session_ids[thread_id] = new_session_id
                logger.info("Stored session %s for thread %s", new_session_id[:12], thread_id)

            # Collect usage info: token counts + optional cost.
            # Claude CLI outputs "total_cost_usd" (not "cost_usd").
            usage: dict | None = None
            cost = data.get("total_cost_usd") or data.get("cost_usd")
            if "usage" in data or cost is not None:
                usage = {**data.get("usage", {})}
                if cost is not None:
                    usage["cost_usd"] = cost

            return AgentResponse(text=text, raw=data, usage=usage)
        except (json.JSONDecodeError, TypeError):
            return AgentResponse(text=raw)
