from __future__ import annotations

import json
import logging
from pathlib import Path

from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.cli.base import BaseCLIAgent

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

    @property
    def name(self) -> str:
        return "codex"

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
