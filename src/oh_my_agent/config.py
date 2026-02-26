from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """Application configuration loaded from environment variables."""

    discord_bot_token: str
    discord_channel_id: int
    claude_max_turns: int = 25
    claude_allowed_tools: list[str] = field(
        default_factory=lambda: ["Bash", "Read", "Edit", "Glob", "Grep"]
    )
    claude_model: str = "sonnet"

    @classmethod
    def from_env(cls) -> Config:
        load_dotenv()

        token = os.environ["DISCORD_BOT_TOKEN"]
        channel_id = int(os.environ["DISCORD_CHANNEL_ID"])

        allowed_tools_raw = os.getenv("CLAUDE_ALLOWED_TOOLS", "")
        if allowed_tools_raw.strip():
            allowed_tools = [t.strip() for t in allowed_tools_raw.split(",") if t.strip()]
        else:
            allowed_tools = cls.__dataclass_fields__["claude_allowed_tools"].default_factory()

        return cls(
            discord_bot_token=token,
            discord_channel_id=channel_id,
            claude_max_turns=int(os.getenv("CLAUDE_MAX_TURNS", "25")),
            claude_allowed_tools=allowed_tools,
            claude_model=os.getenv("CLAUDE_MODEL", "sonnet"),
        )
