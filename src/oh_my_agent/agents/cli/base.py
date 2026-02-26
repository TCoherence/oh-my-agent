from __future__ import annotations

import asyncio
import logging
import os
from abc import abstractmethod

from oh_my_agent.agents.base import AgentResponse, BaseAgent

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300


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


class BaseCLIAgent(BaseAgent):
    """Base class for agents that wrap a CLI tool as a subprocess."""

    def __init__(self, cli_path: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._cli_path = cli_path
        self._timeout = timeout

    @abstractmethod
    def _build_command(self, prompt: str) -> list[str]:
        """Return the full command to run, with prompt included."""
        ...

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Allow nesting when running inside Claude Code
        env.pop("CLAUDECODE", None)
        return env

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
    ) -> AgentResponse:
        full_prompt = _build_prompt_with_history(prompt, history)
        cmd = self._build_command(full_prompt)
        logger.info("Running %s: %s ...", self.name, " ".join(cmd[:4]))

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
            return AgentResponse(text="", error=f"{self.name} CLI timed out after {self._timeout}s")
        except FileNotFoundError:
            return AgentResponse(
                text="",
                error=f"{self.name} CLI not found at '{self._cli_path}'. Is it installed?",
            )

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            logger.error("%s CLI failed (rc=%d): %s", self.name, proc.returncode, err_msg)
            return AgentResponse(
                text="",
                error=f"{self.name} exited {proc.returncode}: {err_msg[:400]}",
            )

        return AgentResponse(text=stdout.decode(errors="replace").strip())
