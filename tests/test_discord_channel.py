from types import SimpleNamespace
import asyncio

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


class _FakeLimiter:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self, tokens: int = 1) -> None:
        del tokens
        self.calls += 1


@pytest.mark.asyncio
async def test_send_attachment_uploads_png(tmp_path):
    channel = DiscordChannel(token="x", channel_id="100")
    thread = _FakeDiscordThread()
    limiter = _FakeLimiter()
    png = tmp_path / "qr.png"
    png.write_bytes(b"png")

    async def _fake_resolve(_thread_id: str):
        return thread

    channel._resolve_channel = _fake_resolve  # type: ignore[method-assign]
    channel._rate_limiter = limiter  # type: ignore[attr-defined]

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
    assert limiter.calls == 1
    assert thread.calls[0]["content"] == "QR"


@pytest.mark.asyncio
async def test_send_observes_rate_limiter():
    channel = DiscordChannel(token="x", channel_id="100")
    thread = _FakeDiscordThread()
    limiter = _FakeLimiter()

    async def _fake_resolve(_thread_id: str):
        return thread

    channel._resolve_channel = _fake_resolve  # type: ignore[method-assign]
    channel._rate_limiter = limiter  # type: ignore[attr-defined]

    msg_id = await channel.send("thread-1", "hello")

    assert msg_id == "123"
    assert limiter.calls == 1


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


def test_render_hitl_prompt_message_shows_resolving_state():
    channel = DiscordChannel(token="x", channel_id="100")
    prompt = HitlPrompt(
        id="hitl-1",
        target_kind="thread",
        platform="discord",
        channel_id="100",
        thread_id="200",
        task_id=None,
        agent_name="codex",
        status="resolving",
        question="Pick one",
        details="Single choice.",
        choices=(
            {"id": "ai", "label": "AI daily", "description": "Five layers"},
        ),
        selected_choice_id="ai",
        selected_choice_label="AI daily",
        selected_choice_description="Five layers",
        control_envelope_json="{}",
        resume_context={},
        session_id_snapshot="sess-1",
        prompt_message_id="123456789",
        created_by="owner-1",
    )

    text = channel._render_hitl_prompt_message(prompt)

    assert text.startswith("**Input recorded**")
    assert "Status: resuming the agent with your choice." in text


class _FakeInteractionResponse:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def send_message(self, text, ephemeral=False):
        self._events.append(f"send_message:{text}:{ephemeral}")

    async def defer(self, ephemeral=False):
        self._events.append(f"defer:{ephemeral}")


class _FakeInteractionFollowup:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def send(self, text, ephemeral=False):
        self._events.append(f"followup:{text}:{ephemeral}")


class _FakeRuntimeInteractionService:
    def __init__(self, events: list[str], prompt: HitlPrompt) -> None:
        self._events = events
        self._prompt = prompt

    async def get_hitl_prompt(self, prompt_id: str):
        self._events.append(f"get:{prompt_id}:{self._prompt.status}")
        return self._prompt

    async def answer_hitl_prompt(self, prompt_id: str, *, choice_id: str, actor_id: str):
        self._events.append(f"answer:start:{prompt_id}:{choice_id}:{actor_id}")
        await asyncio.sleep(0)
        self._prompt = HitlPrompt(
            **{
                **self._prompt.__dict__,
                "status": "completed",
                "selected_choice_id": choice_id,
                "selected_choice_label": "AI daily",
                "selected_choice_description": "Five layers",
            }
        )
        self._events.append(f"answer:done:{prompt_id}")
        return f"Interactive prompt `{prompt_id}` answered and resumed successfully."

    async def cancel_hitl_prompt(self, prompt_id: str, *, actor_id: str):
        self._events.append(f"cancel:{prompt_id}:{actor_id}")
        return f"Interactive prompt `{prompt_id}` cancelled."


@pytest.mark.asyncio
async def test_handle_hitl_interaction_updates_prompt_to_resolving_before_resume():
    events: list[str] = []
    channel = DiscordChannel(token="x", channel_id="100", owner_user_ids={"42"})
    prompt = HitlPrompt(
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
            {"id": "ai", "label": "AI daily", "description": "Five layers"},
            {"id": "finance", "label": "Finance daily", "description": "Macro"},
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
    channel.set_runtime_service(_FakeRuntimeInteractionService(events, prompt))
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        response=_FakeInteractionResponse(events),
        followup=_FakeInteractionFollowup(events),
        message=SimpleNamespace(id=999),
        channel_id=200,
    )
    channel.update_interactive = _fake_update_interactive(events)  # type: ignore[method-assign]

    await channel._handle_hitl_interaction(  # type: ignore[arg-type]
        interaction,
        prompt_id="hitl-1",
        choice_id="ai",
        cancel=False,
    )

    assert events[0] == "defer:True"
    assert events[1].startswith("get:hitl-1:waiting")
    assert events[2].startswith("update:200:999:**Input recorded**")
    assert events[3] == "followup:Input recorded. Resuming now...:True"
    assert events[4] == "answer:start:hitl-1:ai:42"
    assert "answer:done:hitl-1" in events
    assert events[-1].startswith("update:200:999:**Input resolved**")


def _fake_update_interactive(events: list[str]):
    async def _inner(thread_id: str, message_id: str, prompt):
        events.append(f"update:{thread_id}:{message_id}:{prompt.text}")

    return _inner


@pytest.mark.asyncio
async def test_send_hitl_prompt_uses_send_interactive():
    channel = DiscordChannel(token="x", channel_id="100")
    prompt = HitlPrompt(
        id="hitl-1",
        target_kind="thread",
        platform="discord",
        channel_id="100",
        thread_id="200",
        task_id=None,
        agent_name="codex",
        status="waiting",
        question="Pick one",
        details=None,
        choices=(
            {"id": "ai", "label": "AI daily", "description": None},
        ),
        selected_choice_id=None,
        selected_choice_label=None,
        selected_choice_description=None,
        control_envelope_json="{}",
        resume_context={},
        session_id_snapshot=None,
        prompt_message_id=None,
        created_by="owner-1",
    )
    captured: list[tuple[str, str, str | None]] = []

    async def _fake_send_interactive(thread_id: str, interactive_prompt):
        captured.append((thread_id, interactive_prompt.entity_kind, interactive_prompt.entity_id))
        return "msg-1"

    channel.send_interactive = _fake_send_interactive  # type: ignore[method-assign]

    msg_id = await channel.send_hitl_prompt(thread_id="200", prompt=prompt)

    assert msg_id == "msg-1"
    assert captured == [("200", "hitl", "hitl-1")]


@pytest.mark.asyncio
async def test_send_task_draft_uses_send_interactive():
    channel = DiscordChannel(token="x", channel_id="100")
    channel.set_runtime_service(object())
    captured: list[tuple[str, str, str | None, str | None]] = []

    async def _fake_send_interactive(thread_id: str, prompt):
        captured.append((thread_id, prompt.text, prompt.entity_kind, prompt.idempotency_key))
        return "msg-2"

    channel.send_interactive = _fake_send_interactive  # type: ignore[method-assign]

    msg_id = await channel.send_task_draft(
        thread_id="200",
        draft_text="Approve this task",
        task_id="task-1",
        nonce="nonce-1",
        actions=["approve", "reject"],
    )

    assert msg_id == "msg-2"
    assert captured == [("200", "Approve this task", "task", "nonce-1")]
