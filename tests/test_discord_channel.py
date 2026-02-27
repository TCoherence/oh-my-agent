from types import SimpleNamespace

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
