from types import SimpleNamespace

import pytest

from oh_my_agent.gateway.platforms.discord import DiscordChannel


def test_extract_guild_id_prefers_channel_guild():
    channel = SimpleNamespace(guild=SimpleNamespace(id=12345), guild_id=99999)
    assert DiscordChannel._extract_guild_id(channel) == 12345


def test_extract_guild_id_falls_back_to_guild_id():
    channel = SimpleNamespace(guild=None, guild_id=67890)
    assert DiscordChannel._extract_guild_id(channel) == 67890


def test_extract_guild_id_returns_none_for_dm_like_channel():
    channel = SimpleNamespace(guild=None, guild_id=None)
    assert DiscordChannel._extract_guild_id(channel) is None


class _FakeTree:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []

    def copy_global_to(self, *, guild) -> None:
        self.calls.append(("copy_global_to", guild.id))

    def clear_commands(self, *, guild) -> None:
        self.calls.append(("clear_commands", getattr(guild, "id", None)))

    async def sync(self, *, guild=None):
        self.calls.append(("sync", getattr(guild, "id", None)))


@pytest.mark.asyncio
async def test_sync_command_tree_moves_commands_to_guild_and_clears_global():
    channel = DiscordChannel(token="x", channel_id="100")

    async def _fake_resolve(_target_id: int) -> int | None:
        return 12345

    channel._resolve_target_guild_id = _fake_resolve  # type: ignore[method-assign]
    tree = _FakeTree()

    scope = await channel._sync_command_tree(tree, 100)

    assert scope == "guild:12345"
    assert tree.calls == [
        ("copy_global_to", 12345),
        ("clear_commands", None),
        ("sync", None),
        ("sync", 12345),
    ]
