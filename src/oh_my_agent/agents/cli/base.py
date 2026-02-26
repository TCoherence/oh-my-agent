from __future__ import annotations

import asyncio
import json
import logging
import os
from abc import abstractmethod
from collections.abc import AsyncIterator

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

    def _build_stream_command(self, prompt: str) -> list[str] | None:
        """Return the command for streaming mode, or None if not supported."""
        return None

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Allow nesting when running inside Claude Code
        env.pop("CLAUDECODE", None)
        return env

    @property
    def supports_streaming(self) -> bool:
        return self._build_stream_command("") is not None

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

    async def run_stream(
        self,
        prompt: str,
        history: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """Yield text chunks by reading stream-json output line by line.

        Subclasses that override ``_build_stream_command`` get streaming for
        free.  The default implementation falls back to ``run()``.
        """
        full_prompt = _build_prompt_with_history(prompt, history)
        stream_cmd = self._build_stream_command(full_prompt)

        if stream_cmd is None:
            # Fall back to non-streaming
            response = await self.run(prompt, history)
            if response.error:
                raise RuntimeError(response.error)
            yield response.text
            return

        logger.info("Streaming %s: %s ...", self.name, " ".join(stream_cmd[:4]))

        try:
            proc = await asyncio.create_subprocess_exec(
                *stream_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"{self.name} CLI not found at '{self._cli_path}'. Is it installed?"
            )

        assert proc.stdout is not None

        try:
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").strip()
                if not line:
                    continue
                text = self._parse_stream_line(line)
                if text:
                    yield text
        finally:
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    def _parse_stream_line(self, line: str) -> str | None:
        """Extract text content from a stream-json line.

        Override in subclasses for agent-specific stream formats.
        Default implementation handles Claude CLI ``stream-json`` format.
        """
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return None

        msg_type = obj.get("type")
        # Claude stream-json: assistant text messages
        if msg_type == "assistant" and obj.get("subtype") == "text":
            return obj.get("content", "")
        # Claude stream-json: final result
        if msg_type == "result":
            return obj.get("result", "")
        return None
