"""v2 router refactor coverage.

The router used to expose 5 canonical intents:
``chat_reply`` / ``invoke_skill`` / ``oneoff_artifact`` /
``propose_repo_change`` / ``update_skill``.

v2 collapses them to 3, organized by "does this modify the source repo?":
``reply`` / ``artifact`` / ``repo_update``. All legacy names normalize at
parse time and on ``RouteDecision.__post_init__`` so test fixtures and
older router models keep working through the migration window.

These tests pin the v2-specific behaviors that ``test_router.py`` doesn't
explicitly cover (``force_draft`` propagation, ``draft:`` user prefix,
exhaustive legacy → v2 normalization matrix).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.gateway.manager import GatewayManager
from oh_my_agent.gateway.router import RouteDecision, normalize_intent
from oh_my_agent.runtime.policy import strip_draft_prefix

# ── Normalization matrix ──────────────────────────────────────────────── #


@pytest.mark.parametrize(
    "raw,expected",
    [
        # v2 canonical → identity
        ("reply", "reply"),
        ("artifact", "artifact"),
        ("repo_update", "repo_update"),
        # v1 canonical → v2
        ("chat_reply", "reply"),
        ("invoke_skill", "artifact"),
        ("oneoff_artifact", "artifact"),
        ("propose_repo_change", "repo_update"),
        ("update_skill", "repo_update"),
        # Pre-v1 aliases → v2
        ("reply_once", "reply"),
        ("invoke_existing_skill", "artifact"),
        ("propose_artifact_task", "artifact"),
        ("propose_repo_task", "repo_update"),
        ("propose_task", "repo_update"),
        ("create_skill", "repo_update"),
        ("repair_skill", "repo_update"),
        # Case/whitespace
        ("  REPLY ", "reply"),
        ("Repo_Update", "repo_update"),
        ("UPDATE_SKILL", "repo_update"),
        # Unknown → safe default
        ("", "reply"),
        ("garbage", "reply"),
        ("destroy_database", "reply"),
    ],
)
def test_normalize_intent_matrix(raw: str, expected: str) -> None:
    assert normalize_intent(raw) == expected


def test_route_decision_post_init_normalizes_legacy_decision() -> None:
    """Tests and direct callers can construct RouteDecision with v1 or
    pre-v1 intent names; ``__post_init__`` rewrites the field to v2
    canonical so the dispatcher (which only matches v2) sees one form."""
    d = RouteDecision(
        decision="propose_repo_change",
        confidence=0.9,
        goal="x",
        risk_hints=[],
        raw_text="{}",
    )
    assert d.decision == "repo_update"

    d2 = RouteDecision(
        decision="oneoff_artifact",
        confidence=0.8,
        goal="x",
        risk_hints=[],
        raw_text="{}",
    )
    assert d2.decision == "artifact"

    d3 = RouteDecision(
        decision="chat_reply",
        confidence=0.7,
        goal="",
        risk_hints=[],
        raw_text="{}",
    )
    assert d3.decision == "reply"


def test_route_decision_force_draft_defaults_false() -> None:
    d = RouteDecision(
        decision="artifact",
        confidence=0.9,
        goal="x",
        risk_hints=[],
        raw_text="{}",
    )
    assert d.force_draft is False


def test_route_decision_force_draft_passes_through() -> None:
    d = RouteDecision(
        decision="artifact",
        confidence=0.9,
        goal="x",
        risk_hints=[],
        raw_text="{}",
        force_draft=True,
    )
    assert d.force_draft is True


# ── force_draft parse coverage ──────────────────────────────────────── #


@pytest.mark.asyncio
async def test_router_parses_force_draft_true(monkeypatch) -> None:
    """Router payload's ``force_draft`` field round-trips into RouteDecision."""
    from oh_my_agent.gateway.router import OpenAICompatibleRouter

    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
    )

    def _fake_post(_payload):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"decision":"artifact","confidence":0.9,'
                            '"goal":"long market briefing","risk_hints":[],'
                            '"force_draft":true}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("跑一份 market briefing")
    assert out is not None
    assert out.decision == "artifact"
    assert out.force_draft is True


@pytest.mark.asyncio
async def test_router_parses_force_draft_missing_defaults_false(monkeypatch) -> None:
    """Older router models that don't emit force_draft → default False."""
    from oh_my_agent.gateway.router import OpenAICompatibleRouter

    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
    )

    def _fake_post(_payload):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"decision":"artifact","confidence":0.9,'
                            '"goal":"quick summary","risk_hints":[]}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("总结一下")
    assert out is not None
    assert out.force_draft is False


@pytest.mark.asyncio
async def test_router_parses_force_draft_string_form(monkeypatch) -> None:
    """Some models emit ``force_draft`` as a string literal not a bool —
    the parser accepts ``"true"`` / ``"false"`` defensively."""
    from oh_my_agent.gateway.router import OpenAICompatibleRouter

    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
    )

    def _fake_post(_payload):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"decision":"artifact","confidence":0.9,'
                            '"goal":"long task","risk_hints":[],'
                            '"force_draft":"true"}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("跑一份 brief")
    assert out is not None
    assert out.force_draft is True


# ── strip_draft_prefix unit coverage ───────────────────────────────── #


@pytest.mark.parametrize(
    "raw,expected_text,expected_flag",
    [
        ("draft: do x", "do x", True),
        ("DRAFT: 跑一份 brief", "跑一份 brief", True),
        ("  draft :   do x", "do x", True),
        ("草稿: 调研 X", "调研 X", True),
        ("草稿： 调研 X", "调研 X", True),
        # No prefix
        ("hello world", "hello world", False),
        ("draft", "draft", False),  # no colon → not a prefix
        ("drafting plan", "drafting plan", False),  # word boundary
        ("", "", False),
    ],
)
def test_strip_draft_prefix(raw: str, expected_text: str, expected_flag: bool) -> None:
    stripped, found = strip_draft_prefix(raw)
    assert stripped == expected_text
    assert found is expected_flag


# ── Dispatcher behavior: force_draft flow end-to-end ─────────────────── #


def _make_msg(*, content: str, thread_id: str = "t1"):
    from oh_my_agent.gateway.base import IncomingMessage

    return IncomingMessage(
        platform="discord",
        channel_id="100",
        thread_id=thread_id,
        author="alice",
        author_id="user-1",
        content=content,
    )


def _make_session(*, channel: MagicMock, registry: MagicMock):
    from oh_my_agent.gateway.session import ChannelSession

    return ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )


def _make_channel():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)
    return channel


def _make_runtime():
    runtime = MagicMock()
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)
    runtime.create_artifact_task = AsyncMock()
    runtime.create_repo_change_task = AsyncMock()
    runtime.create_skill_task = AsyncMock()
    return runtime


def _make_registry():
    from unittest.mock import MagicMock

    from oh_my_agent.agents.base import AgentResponse
    from oh_my_agent.agents.registry import AgentRegistry

    mock_agent = MagicMock()
    mock_agent.name = "claude"
    registry = MagicMock(spec=AgentRegistry)
    registry.agents = [mock_agent]
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="ok")))
    return registry


@pytest.mark.asyncio
async def test_dispatcher_artifact_force_draft_false_auto_executes() -> None:
    """Default v2 behavior: ``artifact`` without ``force_draft`` runs
    immediately (no draft) — this is the user-facing change from v1's
    "all artifacts draft" default."""
    channel = _make_channel()
    runtime = _make_runtime()
    registry = _make_registry()

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="artifact",
            confidence=0.91,
            goal="Quick BTC summary",
            risk_hints=[],
            raw_text="{}",
            force_draft=False,
        )
    )

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime, intent_router=router)
    msg = _make_msg(content="BTC 现价怎样")
    await gm.handle_message(session, registry, msg)

    runtime.create_artifact_task.assert_called_once()
    kwargs = runtime.create_artifact_task.call_args.kwargs
    assert kwargs["force_draft"] is False
    # User notification reflects auto-execution.
    channel.send.assert_called()
    sent_text = channel.send.call_args.args[1]
    assert "started execution" in sent_text


@pytest.mark.asyncio
async def test_dispatcher_artifact_force_draft_true_creates_draft() -> None:
    """Router-emitted ``force_draft=True`` propagates into the artifact
    task's ``force_draft`` kwarg AND triggers the draft-notification text."""
    channel = _make_channel()
    runtime = _make_runtime()
    registry = _make_registry()

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="artifact",
            confidence=0.91,
            goal="Daily market briefing",
            risk_hints=[],
            raw_text="{}",
            force_draft=True,
        )
    )

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime, intent_router=router)
    msg = _make_msg(content="跑一份每日市场简报")
    await gm.handle_message(session, registry, msg)

    runtime.create_artifact_task.assert_called_once()
    kwargs = runtime.create_artifact_task.call_args.kwargs
    assert kwargs["force_draft"] is True
    channel.send.assert_called()
    sent_text = channel.send.call_args.args[1]
    assert "created a draft" in sent_text


@pytest.mark.asyncio
async def test_dispatcher_draft_prefix_overrides_router_force_draft() -> None:
    """Even when the router emits ``force_draft=False``, an explicit
    ``draft:`` prefix on the user's message forces the approval gate.
    This is a hard user opt-in — should always win."""
    channel = _make_channel()
    runtime = _make_runtime()
    registry = _make_registry()

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="artifact",
            confidence=0.91,
            goal="Quick BTC summary",
            risk_hints=[],
            raw_text="{}",
            force_draft=False,
        )
    )

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime, intent_router=router)
    msg = _make_msg(content="draft: BTC 现价怎样")
    await gm.handle_message(session, registry, msg)

    runtime.create_artifact_task.assert_called_once()
    kwargs = runtime.create_artifact_task.call_args.kwargs
    assert kwargs["force_draft"] is True


@pytest.mark.asyncio
async def test_dispatcher_known_skill_artifact_force_draft_propagates(tmp_path) -> None:
    """Known-skill ``artifact`` honors router-emitted ``force_draft=True``.
    Default is auto-approve, but the router can opt heavy skills
    (market-briefing-*, paper-digest, etc.) into the approval gate.
    Codex round-1 of WS A review BLOCK-fix."""
    channel = _make_channel()
    runtime = _make_runtime()
    registry = _make_registry()

    skills_root = tmp_path / "skills"
    (skills_root / "paper-digest").mkdir(parents=True)
    (skills_root / "paper-digest" / "SKILL.md").write_text(
        "---\nname: paper-digest\ndescription: Daily arxiv summary\n---\n",
        encoding="utf-8",
    )
    syncer = MagicMock()
    syncer._skills_path = skills_root  # noqa: SLF001

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="artifact",
            confidence=0.91,
            goal="",
            risk_hints=[],
            raw_text="{}",
            skill_name="paper-digest",
            force_draft=True,
        )
    )

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime, intent_router=router, skill_syncer=syncer)
    msg = _make_msg(content="跑下 paper-digest 看下今天 arxiv")
    await gm.handle_message(session, registry, msg)

    runtime.create_artifact_task.assert_called_once()
    kwargs = runtime.create_artifact_task.call_args.kwargs
    assert kwargs["skill_name"] == "paper-digest"
    assert kwargs["source"] == "router_invoke_skill"
    assert kwargs.get("force_draft") is True
    # Force-draft path must NOT pass auto_approve=True (mutually exclusive).
    assert kwargs.get("auto_approve") is not True


@pytest.mark.asyncio
async def test_dispatcher_known_skill_artifact_draft_prefix_overrides(tmp_path) -> None:
    """``draft:`` user prefix forces draft even when the router emits
    ``force_draft=False`` for a known skill. Hard user override."""
    channel = _make_channel()
    runtime = _make_runtime()
    registry = _make_registry()

    skills_root = tmp_path / "skills"
    (skills_root / "paper-digest").mkdir(parents=True)
    (skills_root / "paper-digest" / "SKILL.md").write_text(
        "---\nname: paper-digest\ndescription: Daily arxiv summary\n---\n",
        encoding="utf-8",
    )
    syncer = MagicMock()
    syncer._skills_path = skills_root  # noqa: SLF001

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="artifact",
            confidence=0.91,
            goal="",
            risk_hints=[],
            raw_text="{}",
            skill_name="paper-digest",
            force_draft=False,
        )
    )

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime, intent_router=router, skill_syncer=syncer)
    msg = _make_msg(content="draft: 跑下 paper-digest")
    await gm.handle_message(session, registry, msg)

    runtime.create_artifact_task.assert_called_once()
    kwargs = runtime.create_artifact_task.call_args.kwargs
    assert kwargs.get("force_draft") is True


@pytest.mark.asyncio
async def test_dispatcher_known_skill_artifact_default_auto_approves(tmp_path) -> None:
    """Default known-skill ``artifact`` (no force_draft, no prefix) takes
    the existing auto-approve path — unchanged from v1 invoke_skill."""
    channel = _make_channel()
    runtime = _make_runtime()
    registry = _make_registry()

    skills_root = tmp_path / "skills"
    (skills_root / "paper-digest").mkdir(parents=True)
    (skills_root / "paper-digest" / "SKILL.md").write_text(
        "---\nname: paper-digest\ndescription: Daily arxiv summary\n---\n",
        encoding="utf-8",
    )
    syncer = MagicMock()
    syncer._skills_path = skills_root  # noqa: SLF001

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="artifact",
            confidence=0.91,
            goal="",
            risk_hints=[],
            raw_text="{}",
            skill_name="paper-digest",
            force_draft=False,
        )
    )

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime, intent_router=router, skill_syncer=syncer)
    msg = _make_msg(content="跑下 paper-digest")
    await gm.handle_message(session, registry, msg)

    runtime.create_artifact_task.assert_called_once()
    kwargs = runtime.create_artifact_task.call_args.kwargs
    assert kwargs.get("auto_approve") is True
    assert kwargs.get("force_draft") is not True


@pytest.mark.asyncio
async def test_dispatcher_repo_update_with_skill_always_drafts(tmp_path) -> None:
    """v2 doctrine: ``repo_update`` with skill_name always drafts. Codex
    round-1 of WS A review BLOCK-fix — the legacy v1 high-confidence
    auto-run path is removed."""
    channel = _make_channel()
    runtime = _make_runtime()
    registry = _make_registry()

    skills_root = tmp_path / "skills"
    (skills_root / "paper-digest").mkdir(parents=True)
    (skills_root / "paper-digest" / "SKILL.md").write_text(
        "---\nname: paper-digest\ndescription: x\n---\n",
        encoding="utf-8",
    )
    syncer = MagicMock()
    syncer._skills_path = skills_root  # noqa: SLF001

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="repo_update",
            confidence=0.95,  # high confidence — in v1 this auto-ran
            goal="Improve summary length",
            risk_hints=[],
            raw_text="{}",
            skill_name="paper-digest",
            force_draft=False,  # explicit; dispatcher should ignore
        )
    )

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime, intent_router=router, skill_syncer=syncer)
    msg = _make_msg(content="paper-digest 的 summary 太短了")
    await gm.handle_message(session, registry, msg)

    runtime.create_skill_task.assert_called_once()
    kwargs = runtime.create_skill_task.call_args.kwargs
    assert kwargs.get("force_draft") is True
    assert kwargs["skill_name"] == "paper-digest"


@pytest.mark.asyncio
async def test_dispatcher_repo_update_always_drafts_regardless_of_force_draft() -> None:
    """``repo_update`` (v2 canonical for any repo-modifying task) always
    drafts. The ``force_draft`` field is documented as ignored for this
    intent; the dispatcher must hardcode ``force_draft=True``."""
    channel = _make_channel()
    runtime = _make_runtime()
    registry = _make_registry()

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="repo_update",
            confidence=0.91,
            goal="Fix typo in README",
            risk_hints=[],
            raw_text="{}",
            force_draft=False,  # explicitly false; dispatcher should ignore
        )
    )

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime, intent_router=router)
    msg = _make_msg(content="把 README 里的 typo 修一下")
    await gm.handle_message(session, registry, msg)

    runtime.create_repo_change_task.assert_called_once()
    kwargs = runtime.create_repo_change_task.call_args.kwargs
    assert kwargs["force_draft"] is True
