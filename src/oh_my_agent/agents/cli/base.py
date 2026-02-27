from __future__ import annotations

import asyncio
import json
import logging
import os
from abc import abstractmethod
from pathlib import Path

from oh_my_agent.agents.base import AgentResponse, BaseAgent

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300

# Environment variable keys considered safe to pass to CLI subprocesses.
# Everything else is stripped unless explicitly listed in passthrough_env.
_SAFE_ENV_KEYS = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "SHELL", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
})


def _extract_cli_error(stderr_raw: bytes, stdout_raw: bytes) -> str:
    """Best-effort extraction of useful CLI error text.

    Some CLIs (e.g. Claude) return structured error details on stdout while
    exiting non-zero, leaving stderr empty.
    """
    stderr_text = stderr_raw.decode(errors="replace").strip()
    if stderr_text:
        return stderr_text

    stdout_text = stdout_raw.decode(errors="replace").strip()
    if not stdout_text:
        return "(no stdout/stderr)"

    try:
        data = json.loads(stdout_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return stdout_text

    if isinstance(data, dict):
        for key in ("error", "result", "message"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        err_obj = data.get("error")
        if isinstance(err_obj, dict):
            msg = err_obj.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()

    return stdout_text


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
    """Base class for agents that wrap a CLI tool as a subprocess.

    Args:
        cli_path: Path or name of the CLI executable.
        timeout: Seconds before the subprocess is killed.
        workspace: Working directory for the subprocess. When set, the agent
            is confined to this directory (all CLI sandboxes are cwd-scoped).
            Also activates environment variable sanitization.
        passthrough_env: Additional env var names to forward to the subprocess
            beyond the safe-key whitelist. Only meaningful when ``workspace``
            is set; ignored in legacy (no-workspace) mode.
    """

    def __init__(
        self,
        cli_path: str,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        workspace: Path | None = None,
        passthrough_env: list[str] | None = None,
    ) -> None:
        self._cli_path = cli_path
        self._timeout = timeout
        self._workspace = workspace
        self._passthrough_env = passthrough_env  # None = not configured

    @property
    def _cwd(self) -> str | None:
        """Working directory for subprocesses, or None to inherit."""
        return str(self._workspace) if self._workspace else None

    def _resolve_cwd(self, workspace_override: Path | None = None) -> str | None:
        if workspace_override is not None:
            return str(workspace_override)
        return self._cwd

    @abstractmethod
    def _build_command(self, prompt: str) -> list[str]:
        """Return the full command to run, with prompt included."""
        ...

    def _build_env(self) -> dict[str, str]:
        """Build the environment dict for the subprocess.

        When ``workspace`` or ``passthrough_env`` is configured, uses a
        whitelist model: only ``_SAFE_ENV_KEYS`` plus explicit
        ``passthrough_env`` keys are forwarded.  Otherwise falls back to the
        full inherited environment (backward-compatible default).
        """
        if self._workspace is None and self._passthrough_env is None:
            # Legacy mode: no workspace configured → inherit full environment.
            env = os.environ.copy()
            env.pop("CLAUDECODE", None)
            return env

        # Whitelist mode: strip secrets from the subprocess environment.
        env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
        for key in (self._passthrough_env or []):
            if key in os.environ:
                env[key] = os.environ[key]
        env.pop("CLAUDECODE", None)
        return env

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        workspace_override: Path | None = None,
    ) -> AgentResponse:
        full_prompt = _build_prompt_with_history(prompt, history)
        cmd = self._build_command(full_prompt)
        logger.info("Running %s: %s ...", self.name, " ".join(cmd[:4]))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self._resolve_cwd(workspace_override),
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
            return AgentResponse(
                text="",
                error=f"{self.name} exited {proc.returncode}: {err_msg[:400]}",
            )

        return self._parse_output(stdout.decode(errors="replace").strip())

    def _parse_output(self, raw: str) -> AgentResponse:
        """Parse subprocess stdout into an AgentResponse.

        Override in subclasses that emit structured (JSON/JSONL) output.
        The default implementation treats stdout as plain text.
        """
        return AgentResponse(text=raw)
