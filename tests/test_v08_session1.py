"""Tests for v0.8 Session 1A, 1B, 1C.

1A — Service-layer types + BaseChannel defaults
1B — Config validator
1C — Schema version tracking
"""

from __future__ import annotations

import pytest

from oh_my_agent.config_validator import (
    ConfigError,
    ValidationResult,
    validate_config,
)
from oh_my_agent.gateway.base import (
    ActionDescriptor,
    BaseChannel,
    InteractivePrompt,
)
from oh_my_agent.gateway.services.types import (
    AutomationInfo,
    AutomationStatusResult,
    DoctorResult,
    DoctorSection,
    ServiceResult,
    TaskActionResult,
    TaskListResult,
    TaskSummary,
)
from oh_my_agent.memory.store import (
    CURRENT_SCHEMA_VERSION,
    SplitSQLiteMemoryStore,
    SQLiteMemoryStore,
)


class _StubChannel(BaseChannel):
    """Minimal concrete channel for testing BaseChannel defaults."""

    def __init__(self):
        self._sent: list[tuple[str, str]] = []

    @property
    def platform(self) -> str:
        return "stub"

    @property
    def channel_id(self) -> str:
        return "ch-1"

    async def start(self, on_message) -> None:
        pass

    async def create_thread(self, msg, name) -> str:
        return "thread-new"

    async def send(self, thread_id: str, text: str) -> str | None:
        self._sent.append((thread_id, text))
        return f"msg-{len(self._sent)}"


# -- Service result types -------------------------------------------------


class TestServiceResultTypes:
    def test_service_result_fields(self):
        r = ServiceResult(success=True, message="ok")
        assert r.success is True
        assert r.message == "ok"

    def test_task_summary(self):
        ts = TaskSummary(task_id="t1", status="RUNNING", task_type="artifact", goal="build")
        assert ts.step_info is None

    def test_task_action_result_inherits_service_result(self):
        r = TaskActionResult(success=False, message="denied", task_id="t1", task_status="FAILED")
        assert isinstance(r, ServiceResult)
        assert r.task_id == "t1"
        assert r.detail is None

    def test_task_list_result_default_empty(self):
        r = TaskListResult(success=True, message="ok")
        assert r.tasks == []

    def test_doctor_result(self):
        sec = DoctorSection(title="Memory", lines=["ok"])
        dr = DoctorResult(success=True, message="healthy", sections=[sec])
        assert len(dr.sections) == 1
        assert dr.sections[0].title == "Memory"

    def test_automation_info_defaults(self):
        ai = AutomationInfo(name="daily-report", enabled=True)
        assert ai.schedule is None
        assert ai.last_run_at is None

    def test_automation_status_result(self):
        info = AutomationInfo(name="j1", enabled=False)
        r = AutomationStatusResult(success=True, message="1 job", automations=[info])
        assert len(r.automations) == 1


# -- BaseChannel defaults -------------------------------------------------


class TestBaseChannelDefaults:
    @pytest.fixture
    def ch(self):
        return _StubChannel()

    @pytest.mark.asyncio
    async def test_stop_is_noop(self, ch):
        await ch.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_edit_message_is_noop(self, ch):
        await ch.edit_message("t1", "m1", "new text")  # should not raise

    @pytest.mark.asyncio
    async def test_send_interactive_falls_back_to_send(self, ch):
        prompt = InteractivePrompt(
            text="Choose one",
            actions=[ActionDescriptor(id="a", label="Accept")],
        )
        msg_id = await ch.send_interactive("t1", prompt)
        assert msg_id is not None
        assert ch._sent[-1] == ("t1", "Choose one")

    @pytest.mark.asyncio
    async def test_update_interactive_falls_back_to_edit(self, ch):
        prompt = InteractivePrompt(text="Updated", actions=[])
        await ch.update_interactive("t1", "m1", prompt)
        # edit_message is a no-op on _StubChannel, so just verify no crash

    @pytest.mark.asyncio
    async def test_supports_buttons_default_false(self, ch):
        assert ch.supports_buttons() is False

    @pytest.mark.asyncio
    async def test_send_dm_default_none(self, ch):
        result = await ch.send_dm("user1", "hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_render_user_mention_default(self, ch):
        assert ch.render_user_mention("U123") == "`U123`"


# -- ActionDescriptor / InteractivePrompt ----------------------------------


class TestInteractivePromptDataclasses:
    def test_action_descriptor_defaults(self):
        ad = ActionDescriptor(id="ok", label="OK")
        assert ad.style == "secondary"
        assert ad.disabled is False

    def test_interactive_prompt_minimal(self):
        p = InteractivePrompt(text="hello")
        assert p.actions == []
        assert p.idempotency_key is None
        assert p.entity_id is None

    def test_interactive_prompt_with_actions(self):
        actions = [
            ActionDescriptor(id="approve", label="Approve", style="success"),
            ActionDescriptor(id="reject", label="Reject", style="danger"),
        ]
        p = InteractivePrompt(text="Approve?", actions=actions, entity_id="task-1")
        assert len(p.actions) == 2
        assert p.entity_id == "task-1"


# ── 1B: Config validator ────────────────────────────────────────────── #

_MINIMAL_VALID = {
    "gateway": {
        "channels": [
            {
                "platform": "discord",
                "token": "tok-abc",
                "channel_id": "12345",
                "agents": ["claude"],
            }
        ]
    },
    "agents": {
        "claude": {"type": "cli", "cli_path": "/usr/bin/claude"},
    },
}


class TestConfigValidator:
    def test_minimal_valid_config(self):
        r = validate_config(_MINIMAL_VALID)
        assert r.ok
        assert r.errors == []
        assert "valid" in r.summary().lower()

    def test_missing_gateway(self):
        r = validate_config({"agents": {"a": {"type": "cli"}}})
        assert not r.ok
        paths = [e.path for e in r.errors]
        assert "gateway" in paths

    def test_missing_agents_section(self):
        cfg = {
            "gateway": {
                "channels": [
                    {"platform": "discord", "token": "t", "channel_id": "1", "agents": ["x"]}
                ]
            }
        }
        r = validate_config(cfg)
        assert not r.ok
        assert any(e.path == "agents" for e in r.errors)

    def test_unsupported_platform(self):
        cfg = {
            **_MINIMAL_VALID,
            "gateway": {
                "channels": [
                    {"platform": "telegram", "token": "t", "channel_id": "1", "agents": ["x"]}
                ]
            },
        }
        r = validate_config(cfg)
        assert any("unsupported platform" in e.message for e in r.errors)

    def test_slack_rejected_with_specific_message(self):
        """Slack must be explicitly rejected in 1.0 with a pointer to the upgrade guide."""
        cfg = {
            **_MINIMAL_VALID,
            "gateway": {
                "channels": [
                    {"platform": "slack", "token": "t", "channel_id": "1", "agents": ["x"]}
                ]
            },
        }
        r = validate_config(cfg)
        assert not r.ok
        slack_errors = [e for e in r.errors if e.path.endswith(".platform")]
        assert len(slack_errors) == 1
        msg = slack_errors[0].message
        assert "not supported in 1.0" in msg
        assert "upgrade-guide" in msg
        # Ensure the generic "unsupported platform" wording isn't also emitted.
        assert "expected one of" not in msg

    def test_missing_token(self):
        cfg = {
            **_MINIMAL_VALID,
            "gateway": {
                "channels": [
                    {"platform": "discord", "token": "", "channel_id": "1", "agents": ["x"]}
                ]
            },
        }
        r = validate_config(cfg)
        assert any("token" in e.path for e in r.errors)

    def test_missing_channel_id(self):
        cfg = {
            **_MINIMAL_VALID,
            "gateway": {
                "channels": [
                    {"platform": "discord", "token": "t", "agents": ["x"]}
                ]
            },
        }
        r = validate_config(cfg)
        assert any("channel_id" in e.path for e in r.errors)

    def test_empty_agents_list_in_channel(self):
        cfg = {
            **_MINIMAL_VALID,
            "gateway": {
                "channels": [
                    {"platform": "discord", "token": "t", "channel_id": "1", "agents": []}
                ]
            },
        }
        r = validate_config(cfg)
        assert any("agents" in e.path for e in r.errors)

    def test_unsupported_agent_type(self):
        cfg = {
            **_MINIMAL_VALID,
            "agents": {"bad": {"type": "grpc"}},
        }
        r = validate_config(cfg)
        assert any("unsupported type" in e.message for e in r.errors)

    def test_cli_agent_missing_cli_path_is_warning(self):
        cfg = {
            **_MINIMAL_VALID,
            "agents": {"claude": {"type": "cli"}},
        }
        r = validate_config(cfg)
        # Should be a warning, not an error — config is still ok
        assert r.ok
        warnings = [e for e in r.errors if e.severity == "warning"]
        assert any("cli_path" in e.path for e in warnings)

    def test_invalid_log_level_is_warning(self):
        cfg = {**_MINIMAL_VALID, "logging": {"level": "VERBOSE"}}
        r = validate_config(cfg)
        assert r.ok  # warning only
        assert any("level" in e.path for e in r.errors)

    def test_negative_retention_is_warning(self):
        cfg = {**_MINIMAL_VALID, "logging": {"service_retention_days": -1}}
        r = validate_config(cfg)
        assert any("service_retention_days" in e.path for e in r.errors)

    def test_optional_sections_must_be_mappings(self):
        # Non-dict top-level sections are hard errors so boot exits before
        # _apply_v052_defaults' setdefault chains crash.
        cfg = {**_MINIMAL_VALID, "runtime": "bad", "memory": [1, 2]}
        r = validate_config(cfg)
        errors = [e for e in r.errors if e.severity == "error"]
        sections_with_errors = {e.path for e in errors}
        assert "runtime" in sections_with_errors
        assert "memory" in sections_with_errors
        assert not r.ok

    def test_empty_channels_list(self):
        cfg = {
            **_MINIMAL_VALID,
            "gateway": {"channels": []},
        }
        r = validate_config(cfg)
        assert not r.ok
        assert any("non-empty" in e.message for e in r.errors)

    def test_config_error_str(self):
        e = ConfigError("gateway.token", "is required", "error")
        assert "[ERROR]" in str(e)
        w = ConfigError("agents.cli_path", "not set", "warning")
        assert "[WARNING]" in str(w)

    def test_validation_result_summary_counts(self):
        vr = ValidationResult(errors=[
            ConfigError("a", "bad", "error"),
            ConfigError("b", "meh", "warning"),
            ConfigError("c", "worse", "error"),
        ])
        s = vr.summary()
        assert "2 error(s)" in s
        assert "1 warning(s)" in s


# ── 1C: Schema version tracking ─────────────────────────────────────── #


class TestSchemaVersion:
    @pytest.fixture
    async def store(self, tmp_path):
        s = SQLiteMemoryStore(tmp_path / "test.db")
        await s.init()
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_schema_version_initialised_after_init(self, store):
        v = await store.get_schema_version()
        assert v == CURRENT_SCHEMA_VERSION

    @pytest.mark.asyncio
    async def test_set_schema_version_round_trip(self, store):
        await store.set_schema_version(42)
        assert await store.get_schema_version() == 42

    @pytest.mark.asyncio
    async def test_schema_version_survives_reconnect(self, tmp_path):
        path = tmp_path / "persist.db"
        s1 = SQLiteMemoryStore(path)
        await s1.init()
        await s1.set_schema_version(7)
        await s1.close()

        s2 = SQLiteMemoryStore(path)
        await s2.init()
        assert await s2.get_schema_version() == 7
        await s2.close()

    @pytest.mark.asyncio
    async def test_current_schema_version_is_positive(self):
        assert CURRENT_SCHEMA_VERSION >= 1


# ── 1C bonus: SplitSQLiteMemoryStore routes schema methods ──────────── #


class TestSplitStoreSchemaVersion:
    @pytest.mark.asyncio
    async def test_split_store_schema_version_routed(self, tmp_path):
        split = SplitSQLiteMemoryStore(
            conversation_path=tmp_path / "conv.db",
            runtime_state_path=tmp_path / "runtime.db",
            skills_telemetry_path=tmp_path / "skills.db",
        )
        await split.init()

        # Should route to runtime store
        v = await split.get_schema_version()
        assert v == CURRENT_SCHEMA_VERSION

        await split.set_schema_version(99)
        assert await split.get_schema_version() == 99

        await split.close()
