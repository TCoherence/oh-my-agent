from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from oh_my_agent.boot import BootContext, clear_slash_commands


def _make_ctx(channels: list[dict]) -> BootContext:
    return BootContext(
        config={"gateway": {"channels": channels}},
        config_path=Path("/tmp/dummy.yaml"),
        project_root=Path("/tmp"),
        runtime_root=Path("/tmp"),
        logger=logging.getLogger("test_clear_commands"),
    )


@pytest.mark.asyncio
async def test_clear_slash_commands_calls_helper_per_discord_channel(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_clear(token: str, channel_id: str) -> str:
        calls.append((token, channel_id))
        return "guild:42+global"

    monkeypatch.setattr(
        "oh_my_agent.gateway.platforms.discord.clear_application_commands",
        fake_clear,
    )

    ctx = _make_ctx(
        [
            {"platform": "discord", "token": "tok-A", "channel_id": "111"},
            {"platform": "discord", "token": "tok-B", "channel_id": "222"},
        ]
    )

    rc = await clear_slash_commands(ctx)

    assert rc == 0
    assert calls == [("tok-A", "111"), ("tok-B", "222")]


@pytest.mark.asyncio
async def test_clear_slash_commands_skips_non_discord(monkeypatch) -> None:
    fake = AsyncMock(return_value="global")
    monkeypatch.setattr(
        "oh_my_agent.gateway.platforms.discord.clear_application_commands",
        fake,
    )

    ctx = _make_ctx(
        [
            {"platform": "slack", "token": "x", "channel_id": "1"},
            {"platform": "discord", "token": "tok", "channel_id": "999"},
        ]
    )

    rc = await clear_slash_commands(ctx)

    assert rc == 0
    fake.assert_awaited_once_with("tok", "999")


@pytest.mark.asyncio
async def test_clear_slash_commands_returns_error_when_no_channels() -> None:
    ctx = _make_ctx([])
    rc = await clear_slash_commands(ctx)
    assert rc == 1


@pytest.mark.asyncio
async def test_clear_slash_commands_counts_failures(monkeypatch) -> None:
    async def fake_clear(token: str, channel_id: str) -> str:
        if token == "good":
            return "global"
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "oh_my_agent.gateway.platforms.discord.clear_application_commands",
        fake_clear,
    )

    ctx = _make_ctx(
        [
            {"platform": "discord", "token": "good", "channel_id": "1"},
            {"platform": "discord", "token": "bad", "channel_id": "2"},
        ]
    )

    rc = await clear_slash_commands(ctx)
    assert rc == 1


@pytest.mark.asyncio
async def test_clear_slash_commands_missing_token_is_failure(monkeypatch) -> None:
    fake = AsyncMock(return_value="global")
    monkeypatch.setattr(
        "oh_my_agent.gateway.platforms.discord.clear_application_commands",
        fake,
    )

    ctx = _make_ctx(
        [{"platform": "discord", "channel_id": "1"}],  # token missing
    )

    rc = await clear_slash_commands(ctx)
    assert rc == 1
    fake.assert_not_awaited()
