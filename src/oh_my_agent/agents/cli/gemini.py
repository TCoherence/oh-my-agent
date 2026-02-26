from __future__ import annotations

from oh_my_agent.agents.cli.base import BaseCLIAgent


class GeminiCLIAgent(BaseCLIAgent):
    """Agent that delegates to the `gemini` CLI (Google Gemini CLI).

    https://github.com/google-gemini/gemini-cli
    """

    def __init__(
        self,
        cli_path: str = "gemini",
        model: str = "gemini-3-flash-preview",
        timeout: int = 300,
    ) -> None:
        super().__init__(cli_path=cli_path, timeout=timeout)
        self._model = model

    @property
    def name(self) -> str:
        return "gemini"

    def _build_command(self, prompt: str) -> list[str]:
        return [
            self._cli_path,
            "-p", prompt,
            "--model", self._model,
            "--yolo",           # non-interactive, auto-approve all tool calls
        ]
