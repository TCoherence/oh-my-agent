from __future__ import annotations

from oh_my_agent.agents.cli.base import BaseCLIAgent


class ClaudeAgent(BaseCLIAgent):
    """Agent that delegates to the `claude` CLI."""

    def __init__(
        self,
        cli_path: str = "claude",
        max_turns: int = 25,
        allowed_tools: list[str] | None = None,
        model: str = "sonnet",
        timeout: int = 300,
    ) -> None:
        super().__init__(cli_path=cli_path, timeout=timeout)
        self._max_turns = max_turns
        self._allowed_tools = allowed_tools or []
        self._model = model

    @property
    def name(self) -> str:
        return "claude"

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
