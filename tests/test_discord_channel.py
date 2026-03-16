from types import SimpleNamespace

import pytest

from oh_my_agent.runtime.types import HitlPrompt
from oh_my_agent.gateway.base import OutgoingAttachment
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


class _FakeDiscordThread:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send(self, content=None, file=None, files=None):
        self.calls.append({"content": content, "file": file, "files": files})
        return SimpleNamespace(id=123)


@pytest.mark.asyncio
async def test_send_attachment_uploads_png(tmp_path):
    channel = DiscordChannel(token="x", channel_id="100")
    thread = _FakeDiscordThread()
    png = tmp_path / "qr.png"
    png.write_bytes(b"png")

    async def _fake_resolve(_thread_id: str):
        return thread

    channel._resolve_channel = _fake_resolve  # type: ignore[method-assign]

    msg_id = await channel.send_attachment(
        "thread-1",
        OutgoingAttachment(
            filename="qr.png",
            content_type="image/png",
            local_path=png,
            caption="QR",
        ),
    )

    assert msg_id == "123"
    assert thread.calls
    assert thread.calls[0]["content"] == "QR"


class _FakeDiscordClient:
    def __init__(self) -> None:
        self.views: list[tuple[object, int | None]] = []

    def add_view(self, view, *, message_id=None) -> None:
        self.views.append((view, message_id))


class _FakeRuntimeService:
    async def list_active_hitl_prompts(self, *, platform=None, channel_id=None, limit=100):
        del platform, channel_id, limit
        return [
            HitlPrompt(
                id="hitl-1",
                target_kind="thread",
                platform="discord",
                channel_id="100",
                thread_id="200",
                task_id=None,
                agent_name="codex",
                status="waiting",
                question="Pick one",
                details="Single choice.",
                choices=(
                    {"id": "politics", "label": "Politics daily", "description": "Geopolitics"},
                    {"id": "finance", "label": "Finance daily", "description": None},
                ),
                selected_choice_id=None,
                selected_choice_label=None,
                selected_choice_description=None,
                control_envelope_json="{}",
                resume_context={},
                session_id_snapshot="sess-1",
                prompt_message_id="123456789",
                created_by="owner-1",
            )
        ]


@pytest.mark.asyncio
async def test_rehydrate_hitl_prompt_views_restores_active_prompt():
    channel = DiscordChannel(token="x", channel_id="100", owner_user_ids={"owner-1"})
    channel.set_runtime_service(_FakeRuntimeService())
    client = _FakeDiscordClient()

    await channel._rehydrate_hitl_prompt_views(client)  # type: ignore[arg-type]

    assert len(client.views) == 1
    _view, message_id = client.views[0]
    assert message_id == 123456789


class _FakeDiscordDMChannel:
    def __init__(self) -> None:
        self.id = 555
        self.sent: list[str] = []

    async def send(self, text):
        self.sent.append(text)
        return SimpleNamespace(id=987)


class _FakeDiscordUser:
    def __init__(self) -> None:
        self.dm_channel = None

    async def create_dm(self):
        self.dm_channel = _FakeDiscordDMChannel()
        return self.dm_channel


@pytest.mark.asyncio
async def test_send_dm_uses_dm_channel(tmp_path):
    del tmp_path
    channel = DiscordChannel(token="x", channel_id="100")
    fake_user = _FakeDiscordUser()
    channel._client = SimpleNamespace(  # type: ignore[attr-defined]
        get_user=lambda _uid: fake_user,
        fetch_user=None,
        get_channel=lambda _cid: fake_user.dm_channel,
        fetch_channel=lambda _cid: fake_user.dm_channel,
    )

    msg_id = await channel.send_dm("42", "hello owner")

    assert msg_id == "987"
    assert fake_user.dm_channel is not None
    assert fake_user.dm_channel.sent == ["hello owner"]


def test_render_user_mention_uses_discord_syntax():
    channel = DiscordChannel(token="x", channel_id="100")
    assert channel.render_user_mention("42") == "<@42>"
