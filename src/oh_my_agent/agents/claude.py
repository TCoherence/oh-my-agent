from __future__ import annotations

import asyncio
import logging
import os

from oh_my_agent.agents.base import AgentResponse, BaseAgent

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300


class ClaudeAgent(BaseAgent):
    """Agent that delegates to the `claude` CLI."""

    def __init__(
        self,
        cli_path: str = "claude",
        max_turns: int = 25,
        allowed_tools: list[str] | None = None,
        model: str = "sonnet",
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._cli_path = cli_path
        self._max_turns = max_turns
        self._allowed_tools = allowed_tools or []
        self._model = model
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "claude"

    async def run(self, prompt: str) -> AgentResponse:
        cmd = self._build_command(prompt)
        logger.info("Running: %s", " ".join(cmd[:6]) + " ...")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
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
            return AgentResponse(
                text="",
                error=f"Claude CLI timed out after {self._timeout}s",
            )
        except FileNotFoundError:
            return AgentResponse(
                text="",
                error=f"Claude CLI not found at '{self._cli_path}'. Is it installed and on PATH?",
            )

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            logger.error("Claude CLI failed (rc=%d): %s", proc.returncode, err_msg)
            return AgentResponse(
                text="",
                error=f"Claude CLI exited with code {proc.returncode}: {err_msg[:500]}",
            )

        raw_output = stdout.decode(errors="replace").strip()
        return AgentResponse(text=raw_output)

    def _build_command(self, prompt: str) -> list[str]:
        cmd = [
            self._cli_path,
            "-p", prompt,
            "--output-format", "text",
            "--max-turns", str(self._max_turns),
            "--model", self._model,
            "--dangerously-skip-permissions",
        ]
        if self._allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self._allowed_tools)])
        return cmd

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Unset CLAUDE_CODE env vars to allow nesting when developing inside Claude Code
        env.pop("CLAUDECODE", None)
        return env
