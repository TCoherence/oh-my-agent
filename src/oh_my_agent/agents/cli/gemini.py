from __future__ import annotations

import json
import logging
from pathlib import Path

from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.cli.base import BaseCLIAgent

logger = logging.getLogger(__name__)


class GeminiCLIAgent(BaseCLIAgent):
    """Agent that delegates to the `gemini` CLI (Google Gemini CLI).

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

    @property
    def name(self) -> str:
        return "gemini"

    def _build_command(self, prompt: str) -> list[str]:
        return [
            self._cli_path,
            "-p", prompt,
            "--model", self._model,
            "--yolo",               # non-interactive, auto-approve all tool calls
            "--output-format", "json",  # structured output with token stats
        ]

    def _parse_output(self, raw: str) -> AgentResponse:
        """Parse Gemini JSON output to extract response text and token usage."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            # Gemini occasionally returns plain text even with --output-format json
            return AgentResponse(text=raw)

        text = data.get("response", "")
        if not text:
            return AgentResponse(text=raw)

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
