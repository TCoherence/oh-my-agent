from __future__ import annotations

from oh_my_agent.agents.cli.base import BaseCLIAgent


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
        timeout: int = 300,
    ) -> None:
        super().__init__(cli_path=cli_path, timeout=timeout)
        self._model = model

    @property
    def name(self) -> str:
        return "codex"

    def _build_command(self, prompt: str) -> list[str]:
        return [
            self._cli_path,
            "exec",
            "--full-auto",          # auto-approve + sandbox
            "--model", self._model,
            prompt,
        ]
