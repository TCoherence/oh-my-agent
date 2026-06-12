from __future__ import annotations

import logging

import pytest

from oh_my_agent.boot import (
    ConfigShapeError,
    _apply_agent_env_overrides,
    _apply_v052_defaults,
    _build_agent,
    _resolve_owner_user_ids,
    verify_integrity,
)


def test_apply_v052_defaults_normalizes_null_runtime():
    cfg = {"runtime": None}
    _apply_v052_defaults(cfg)
    # null is replaced with a real dict and the cleanup defaults apply.
    assert isinstance(cfg["runtime"], dict)
    assert cfg["runtime"]["cleanup"]["enabled"] is True
    assert cfg["runtime"]["cleanup"]["retention_hours"] == 168


def test_apply_v052_defaults_normalizes_null_cleanup():
    cfg = {"runtime": {"cleanup": None}}
    _apply_v052_defaults(cfg)
    assert isinstance(cfg["runtime"]["cleanup"], dict)
    assert cfg["runtime"]["cleanup"]["enabled"] is True


def test_apply_v052_defaults_raises_on_non_dict_runtime():
    cfg = {"runtime": "garbage"}
    with pytest.raises(ConfigShapeError) as exc_info:
        _apply_v052_defaults(cfg)
    assert "runtime" in str(exc_info.value)


def test_apply_v052_defaults_raises_on_non_dict_cleanup():
    cfg = {"runtime": {"cleanup": "garbage"}}
    with pytest.raises(ConfigShapeError) as exc_info:
        _apply_v052_defaults(cfg)
    assert "runtime.cleanup" in str(exc_info.value)


def test_apply_v052_defaults_preserves_existing_by_outcome():
    cfg = {
        "runtime": {
            "cleanup": {
                "retention_hours_by_outcome": {"success": 72, "failure": 336},
            }
        }
    }
    _apply_v052_defaults(cfg)
    bo = cfg["runtime"]["cleanup"]["retention_hours_by_outcome"]
    assert bo == {"success": 72, "failure": 336}


def test_apply_v052_defaults_raises_on_nested_non_dict():
    """Non-dict nested mapping raises ConfigShapeError so the misconfig
    surfaces clearly via verify_integrity instead of crashing later in
    runtime code with an opaque AttributeError.
    """
    cfg = {"auth": {"providers": "broken"}}
    with pytest.raises(ConfigShapeError) as exc_info:
        _apply_v052_defaults(cfg)
    assert "auth.providers" in str(exc_info.value)


def test_verify_integrity_exits_on_invalid_config(tmp_path, capsys, monkeypatch):
    """End-to-end regression: bad config rejected via stderr+sys.exit(1)
    before any defaulting can crash.
    """
    config = tmp_path / "config.yaml"
    config.write_text(
        "gateway:\n"
        "  channels:\n"
        "    - platform: discord\n"
        "      token: x\n"
        "      channel_id: '1'\n"
        "      agents: [claude]\n"
        "agents:\n"
        "  claude:\n"
        "    type: cli\n"
        "    cli_path: /usr/bin/claude\n"
        "runtime: garbage\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OMA_CONFIG_PATH", str(config))
    with pytest.raises(SystemExit) as exc_info:
        verify_integrity()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "runtime" in err
    assert "must be a mapping" in err


def test_verify_integrity_exits_on_nested_shape_error(
    tmp_path, capsys, monkeypatch,
):
    """If the validator misses a nested mapping shape (e.g. evaluation
    sub-dict, providers map), ConfigShapeError from defaulting is caught
    and surfaced via stderr+exit(1) — not an opaque crash.
    """
    config = tmp_path / "config.yaml"
    config.write_text(
        "gateway:\n"
        "  channels:\n"
        "    - platform: discord\n"
        "      token: x\n"
        "      channel_id: '1'\n"
        "      agents: [claude]\n"
        "agents:\n"
        "  claude:\n"
        "    type: cli\n"
        "    cli_path: /usr/bin/claude\n"
        "skills:\n"
        "  evaluation: broken\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OMA_CONFIG_PATH", str(config))
    with pytest.raises(SystemExit) as exc_info:
        verify_integrity()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "skills.evaluation" in err
    assert "must be a mapping" in err


def test_verify_integrity_validate_only_ok(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(
        "gateway:\n"
        "  channels:\n"
        "    - platform: discord\n"
        "      token: x\n"
        "      channel_id: '1'\n"
        "      agents: [claude]\n"
        "agents:\n"
        "  claude:\n"
        "    type: cli\n"
        "    cli_path: /usr/bin/claude\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OMA_CONFIG_PATH", str(config))
    with pytest.raises(SystemExit) as exc_info:
        verify_integrity(validate_only=True)
    assert exc_info.value.code == 0


def test_verify_integrity_exits_on_invalid_bool_env(tmp_path, capsys, monkeypatch):
    """A malformed agent env override (ValueError from _parse_env_bool) is
    surfaced as a one-line stderr message + exit(1), not a raw traceback.
    """
    config = tmp_path / "config.yaml"
    config.write_text(
        "gateway:\n"
        "  channels:\n"
        "    - platform: discord\n"
        "      token: x\n"
        "      channel_id: '1'\n"
        "      agents: [claude]\n"
        "agents:\n"
        "  claude:\n"
        "    type: cli\n"
        "    cli_path: /usr/bin/claude\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OMA_CONFIG_PATH", str(config))
    monkeypatch.setenv("OMA_AGENT_GEMINI_YOLO", "maybe")
    with pytest.raises(SystemExit) as exc_info:
        verify_integrity()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "OMA_AGENT_GEMINI_YOLO" in err
    assert "Traceback" not in err


# ── agent defaults: provider-keyed, no phantom entries ───────────────── #


def test_apply_v052_defaults_does_not_fabricate_agents():
    cfg = {"agents": {"claude": {"type": "cli"}}}
    _apply_v052_defaults(cfg)
    assert set(cfg["agents"]) == {"claude"}


def test_apply_v052_defaults_applies_claude_defaults_by_provider():
    cfg = {"agents": {"claude_opus": {"provider": "claude"}}}
    _apply_v052_defaults(cfg)
    opus = cfg["agents"]["claude_opus"]
    assert opus["dangerously_skip_permissions"] is False
    assert opus["permission_mode"] is None
    assert opus["extra_args"] == []
    # No phantom claude/gemini/codex entries fabricated alongside.
    assert set(cfg["agents"]) == {"claude_opus"}


def test_apply_v052_defaults_applies_provider_defaults_by_name():
    cfg = {"agents": {"gemini": {}, "codex": {}}}
    _apply_v052_defaults(cfg)
    assert cfg["agents"]["gemini"]["yolo"] is True
    assert cfg["agents"]["codex"]["sandbox_mode"] == "workspace-write"
    assert cfg["agents"]["codex"]["dangerously_bypass_approvals_and_sandbox"] is False
    assert "claude" not in cfg["agents"]


def test_apply_agent_env_overrides_does_not_fabricate_agents(monkeypatch):
    monkeypatch.setenv("OMA_AGENT_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS", "true")
    cfg = {"agents": {"gemini": {}}}
    _apply_agent_env_overrides(cfg)
    assert set(cfg["agents"]) == {"gemini"}


def test_apply_agent_env_overrides_targets_provider_not_name(monkeypatch):
    monkeypatch.setenv("OMA_AGENT_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS", "true")
    cfg = {"agents": {"claude_opus": {"provider": "claude"}}}
    _apply_agent_env_overrides(cfg)
    assert cfg["agents"]["claude_opus"]["dangerously_skip_permissions"] is True


def test_build_agent_claude_fallback_defaults_to_safe_permissions():
    agent = _build_agent("claude", {"type": "cli"})
    assert agent._dangerously_skip_permissions is False


# ── owner_user_ids defensive resolution ──────────────────────────────── #


def _logger() -> logging.Logger:
    return logging.getLogger("test_boot.owner_ids")


def test_resolve_owner_user_ids_list():
    cfg = {"access": {"owner_user_ids": ["123456789", 987654321]}}
    assert _resolve_owner_user_ids(cfg, _logger()) == {"123456789", "987654321"}


def test_resolve_owner_user_ids_scalar_string_is_coerced():
    # A bare YAML string must become a one-element set, not a per-char set.
    cfg = {"access": {"owner_user_ids": "123456789"}}
    assert _resolve_owner_user_ids(cfg, _logger()) == {"123456789"}


def test_resolve_owner_user_ids_scalar_int_is_coerced():
    cfg = {"access": {"owner_user_ids": 123456789}}
    assert _resolve_owner_user_ids(cfg, _logger()) == {"123456789"}


def test_resolve_owner_user_ids_non_mapping_access_logs_and_skips(caplog):
    cfg = {"access": "somestring"}
    with caplog.at_level(logging.ERROR):
        result = _resolve_owner_user_ids(cfg, _logger())
    assert result == set()
    assert any("access" in r.message for r in caplog.records)


def test_resolve_owner_user_ids_bad_shape_logs_and_skips(caplog):
    cfg = {"access": {"owner_user_ids": {"id": "123"}}}
    with caplog.at_level(logging.ERROR):
        result = _resolve_owner_user_ids(cfg, _logger())
    assert result == set()
    assert any("owner_user_ids" in r.message for r in caplog.records)


def test_resolve_owner_user_ids_absent_is_empty():
    assert _resolve_owner_user_ids({}, _logger()) == set()
    assert _resolve_owner_user_ids({"access": {}}, _logger()) == set()
