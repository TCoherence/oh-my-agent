from __future__ import annotations

import pytest

from oh_my_agent.config_validator import validate_config


def _base_config(**router_overrides) -> dict:
    """Build a minimal valid config so only router-section issues surface."""
    router: dict = {"enabled": True}
    router.update(router_overrides)
    return {
        "gateway": {
            "channels": [
                {
                    "platform": "discord",
                    "token": "xxx",
                    "channel_id": "1",
                    "agents": ["claude"],
                }
            ]
        },
        "agents": {"claude": {"type": "cli", "cli_path": "/usr/bin/claude"}},
        "router": router,
    }


def _router_errors(result) -> list:
    return [e for e in result.errors if e.path.startswith("router.")]


# ── autonomy_threshold range ─────────────────────────────────────────── #


def test_autonomy_threshold_above_one_is_error():
    result = validate_config(_base_config(autonomy_threshold=1.5))
    errs = _router_errors(result)
    assert any(e.path == "router.autonomy_threshold" and e.severity == "error" for e in errs)


def test_autonomy_threshold_negative_is_error():
    result = validate_config(_base_config(autonomy_threshold=-0.1))
    errs = _router_errors(result)
    assert any(e.path == "router.autonomy_threshold" and e.severity == "error" for e in errs)


def test_autonomy_threshold_below_confidence_is_error():
    result = validate_config(
        _base_config(confidence_threshold=0.80, autonomy_threshold=0.50)
    )
    errs = _router_errors(result)
    matches = [
        e for e in errs
        if e.path == "router.autonomy_threshold" and e.severity == "error"
    ]
    assert matches, f"expected error, got {errs}"
    assert ">=" in matches[0].message or "confidence_threshold" in matches[0].message


def test_confidence_threshold_above_one_is_error():
    result = validate_config(_base_config(confidence_threshold=1.5))
    errs = _router_errors(result)
    assert any(e.path == "router.confidence_threshold" and e.severity == "error" for e in errs)


# ── extra_body ──────────────────────────────────────────────────────── #


def test_extra_body_non_dict_is_error():
    result = validate_config(_base_config(extra_body="not a dict"))
    errs = _router_errors(result)
    assert any(e.path == "router.extra_body" and e.severity == "error" for e in errs)


@pytest.mark.parametrize("reserved", ["messages", "model", "max_tokens", "temperature"])
def test_extra_body_reserved_keys_are_error(reserved):
    result = validate_config(_base_config(extra_body={reserved: "anything"}))
    errs = _router_errors(result)
    assert any(
        e.path == f"router.extra_body.{reserved}" and e.severity == "error"
        for e in errs
    )


# ── int/bool fields ─────────────────────────────────────────────────── #


def test_timeout_seconds_must_be_positive_int():
    result = validate_config(_base_config(timeout_seconds=0))
    errs = _router_errors(result)
    assert any(e.path == "router.timeout_seconds" and e.severity == "error" for e in errs)


def test_timeout_seconds_rejects_string():
    result = validate_config(_base_config(timeout_seconds="fast"))
    errs = _router_errors(result)
    assert any(e.path == "router.timeout_seconds" and e.severity == "error" for e in errs)


def test_max_retries_must_be_non_negative():
    result = validate_config(_base_config(max_retries=-1))
    errs = _router_errors(result)
    assert any(e.path == "router.max_retries" and e.severity == "error" for e in errs)


def test_context_turns_must_be_positive():
    result = validate_config(_base_config(context_turns=0))
    errs = _router_errors(result)
    assert any(e.path == "router.context_turns" and e.severity == "error" for e in errs)


def test_require_user_confirm_must_be_bool():
    result = validate_config(_base_config(require_user_confirm="yes"))
    errs = _router_errors(result)
    assert any(e.path == "router.require_user_confirm" and e.severity == "error" for e in errs)


def test_enabled_must_be_bool():
    result = validate_config(_base_config(enabled="yes"))
    errs = _router_errors(result)
    assert any(e.path == "router.enabled" and e.severity == "error" for e in errs)


# ── happy path ──────────────────────────────────────────────────────── #


def test_valid_router_config_has_no_router_errors():
    result = validate_config(
        _base_config(
            timeout_seconds=30,
            confidence_threshold=0.55,
            autonomy_threshold=0.90,
            max_retries=2,
            context_turns=10,
            require_user_confirm=True,
            extra_body={"reasoning": {"effort": "medium"}},
        )
    )
    errs = _router_errors(result)
    assert errs == [], f"unexpected router validation errors: {errs}"


def test_validate_absent_router_section_is_fine():
    config = {
        "gateway": {
            "channels": [
                {
                    "platform": "discord",
                    "token": "xxx",
                    "channel_id": "1",
                    "agents": ["claude"],
                }
            ]
        },
        "agents": {"claude": {"type": "cli", "cli_path": "/usr/bin/claude"}},
    }
    result = validate_config(config)
    assert _router_errors(result) == []


# ── Notifications (external push) ──────────────────────────────────── #


def _notif_errors(result, severity: str | None = None) -> list:
    return [
        e
        for e in result.errors
        if e.path.startswith("notifications")
        and (severity is None or e.severity == severity)
    ]


def _config_with_notifications(notif: dict) -> dict:
    base = _base_config()
    base["notifications"] = notif
    return base


def test_notifications_absent_is_fine():
    result = validate_config(_base_config())
    assert _notif_errors(result) == []


def test_notifications_disabled_is_fine_even_if_other_keys_invalid():
    # Disabled → other field validation is skipped (the user might have
    # half-configured the section while turning it off).
    result = validate_config(_config_with_notifications({
        "enabled": False,
        "provider": "garbage",
    }))
    assert _notif_errors(result, severity="error") == []


def test_notifications_unknown_provider_is_error(monkeypatch):
    monkeypatch.setenv("BARK_DEVICE_KEY", "abc")
    result = validate_config(_config_with_notifications({
        "enabled": True,
        "provider": "unknown",
    }))
    errors = _notif_errors(result, severity="error")
    assert any(e.path == "notifications.provider" for e in errors)


def test_notifications_bark_missing_device_key_env_is_error():
    result = validate_config(_config_with_notifications({
        "enabled": True,
        "provider": "bark",
        "bark": {"server": "https://api.day.app"},
    }))
    errors = _notif_errors(result, severity="error")
    assert any(e.path == "notifications.bark.device_key_env" for e in errors)


def test_notifications_bark_unset_env_is_warning(monkeypatch):
    monkeypatch.delenv("BARK_DEVICE_KEY", raising=False)
    result = validate_config(_config_with_notifications({
        "enabled": True,
        "provider": "bark",
        "bark": {"device_key_env": "BARK_DEVICE_KEY"},
    }))
    warnings = _notif_errors(result, severity="warning")
    assert any(e.path == "notifications.bark.device_key_env" for e in warnings)


def test_notifications_bark_with_set_env_is_clean(monkeypatch):
    monkeypatch.setenv("BARK_DEVICE_KEY", "abc")
    result = validate_config(_config_with_notifications({
        "enabled": True,
        "provider": "bark",
        "bark": {"device_key_env": "BARK_DEVICE_KEY"},
        "events": {"task_draft": True},
        "levels": {"task_draft": "timeSensitive"},
    }))
    assert _notif_errors(result, severity="error") == []


def test_notifications_invalid_level_is_error(monkeypatch):
    monkeypatch.setenv("BARK_DEVICE_KEY", "abc")
    result = validate_config(_config_with_notifications({
        "enabled": True,
        "provider": "bark",
        "bark": {"device_key_env": "BARK_DEVICE_KEY"},
        "levels": {"task_draft": "loud"},  # not a valid Bark level
    }))
    errors = _notif_errors(result, severity="error")
    assert any(e.path == "notifications.levels.task_draft" for e in errors)


def test_notifications_unknown_event_kind_is_warning(monkeypatch):
    monkeypatch.setenv("BARK_DEVICE_KEY", "abc")
    result = validate_config(_config_with_notifications({
        "enabled": True,
        "provider": "bark",
        "bark": {"device_key_env": "BARK_DEVICE_KEY"},
        "events": {"unknown_kind": True},
    }))
    warnings = _notif_errors(result, severity="warning")
    assert any(e.path == "notifications.events.unknown_kind" for e in warnings)


# ── Runtime cleanup retention_hours_by_outcome ────────────────────────── #


def _config_with_runtime(runtime: object) -> dict:
    cfg = _base_config()
    cfg["runtime"] = runtime
    return cfg


def _runtime_errors(result, *, severity: str | None = None) -> list:
    matches = [e for e in result.errors if e.path.startswith("runtime")]
    if severity is not None:
        matches = [e for e in matches if e.severity == severity]
    return matches


def test_runtime_non_dict_is_error():
    result = validate_config(_config_with_runtime("garbage"))
    errs = _runtime_errors(result, severity="error")
    assert any(e.path == "runtime" for e in errs)
    assert not result.ok


def test_runtime_cleanup_non_dict_is_error():
    result = validate_config(_config_with_runtime({"cleanup": "broken"}))
    errs = _runtime_errors(result, severity="error")
    assert any(e.path == "runtime.cleanup" for e in errs)
    assert not result.ok


def test_runtime_cleanup_null_passes_validator():
    # YAML "cleanup: null" → Python None. Boot's normalizer fills it in;
    # the validator should not error on the None case.
    result = validate_config(_config_with_runtime({"cleanup": None}))
    errs = _runtime_errors(result, severity="error")
    assert not errs
    assert result.ok


def test_runtime_cleanup_by_outcome_non_dict_is_error():
    result = validate_config(_config_with_runtime({
        "cleanup": {"retention_hours_by_outcome": "x"}
    }))
    errs = _runtime_errors(result, severity="error")
    assert any(e.path == "runtime.cleanup.retention_hours_by_outcome" for e in errs)


def test_runtime_cleanup_by_outcome_negative_is_error():
    result = validate_config(_config_with_runtime({
        "cleanup": {"retention_hours_by_outcome": {"success": -5}}
    }))
    errs = _runtime_errors(result, severity="error")
    assert any(
        e.path == "runtime.cleanup.retention_hours_by_outcome.success" for e in errs
    )


def test_runtime_cleanup_by_outcome_non_int_is_error():
    result = validate_config(_config_with_runtime({
        "cleanup": {"retention_hours_by_outcome": {"failure": "twelve"}}
    }))
    errs = _runtime_errors(result, severity="error")
    assert any(
        e.path == "runtime.cleanup.retention_hours_by_outcome.failure" for e in errs
    )


def test_runtime_cleanup_by_outcome_valid_passes():
    result = validate_config(_config_with_runtime({
        "cleanup": {"retention_hours_by_outcome": {
            "success": 72, "failure": 336, "default": 168,
        }}
    }))
    errs = _runtime_errors(result, severity="error")
    assert not errs
    assert result.ok


def test_runtime_cleanup_retention_hours_non_int_is_error():
    result = validate_config(_config_with_runtime({
        "cleanup": {"retention_hours": "abc"}
    }))
    errs = _runtime_errors(result, severity="error")
    assert any(e.path == "runtime.cleanup.retention_hours" for e in errs)


def test_runtime_cleanup_retention_hours_float_is_error():
    result = validate_config(_config_with_runtime({
        "cleanup": {"retention_hours": 168.5}
    }))
    errs = _runtime_errors(result, severity="error")
    assert any(e.path == "runtime.cleanup.retention_hours" for e in errs)


def test_runtime_cleanup_retention_hours_bool_is_error():
    # int(True) == 1 silently coerces; reject so misconfig surfaces.
    result = validate_config(_config_with_runtime({
        "cleanup": {"retention_hours": True}
    }))
    errs = _runtime_errors(result, severity="error")
    assert any(e.path == "runtime.cleanup.retention_hours" for e in errs)


def test_runtime_cleanup_retention_hours_zero_is_ok():
    result = validate_config(_config_with_runtime({
        "cleanup": {"retention_hours": 0}
    }))
    errs = _runtime_errors(result, severity="error")
    assert not errs


def test_runtime_cleanup_interval_minutes_negative_is_error():
    result = validate_config(_config_with_runtime({
        "cleanup": {"interval_minutes": -1}
    }))
    errs = _runtime_errors(result, severity="error")
    assert any(e.path == "runtime.cleanup.interval_minutes" for e in errs)


def test_runtime_cleanup_interval_minutes_zero_is_ok():
    # Runtime clamps to a 1-minute floor; validator accepts 0.
    result = validate_config(_config_with_runtime({
        "cleanup": {"interval_minutes": 0}
    }))
    errs = _runtime_errors(result, severity="error")
    assert not errs


def test_runtime_merge_gate_non_dict_is_error():
    result = validate_config(_config_with_runtime({"merge_gate": "broken"}))
    errs = _runtime_errors(result, severity="error")
    assert any(e.path == "runtime.merge_gate" for e in errs)


def test_short_workspace_non_dict_is_error():
    cfg = _base_config()
    cfg["short_workspace"] = "broken"
    result = validate_config(cfg)
    errs = [e for e in result.errors if e.path == "short_workspace" and e.severity == "error"]
    assert errs
    assert not result.ok


def test_automations_non_dict_is_error():
    cfg = _base_config()
    cfg["automations"] = "broken"
    result = validate_config(cfg)
    errs = [e for e in result.errors if e.path == "automations" and e.severity == "error"]
    assert errs
    assert not result.ok


def test_runtime_cleanup_enabled_string_is_error():
    """``bool("false")`` returns True silently; reject string YAML
    intended as bool."""
    result = validate_config(_config_with_runtime({
        "cleanup": {"enabled": "false"}
    }))
    errs = _runtime_errors(result, severity="error")
    assert any(e.path == "runtime.cleanup.enabled" for e in errs)


def test_runtime_cleanup_prune_git_worktrees_int_is_error():
    result = validate_config(_config_with_runtime({
        "cleanup": {"prune_git_worktrees": 1}
    }))
    errs = _runtime_errors(result, severity="error")
    assert any(e.path == "runtime.cleanup.prune_git_worktrees" for e in errs)


def test_runtime_merge_gate_enabled_string_is_error():
    result = validate_config(_config_with_runtime({
        "merge_gate": {"enabled": "yes"}
    }))
    errs = _runtime_errors(result, severity="error")
    assert any(e.path == "runtime.merge_gate.enabled" for e in errs)


def test_short_workspace_enabled_string_is_error():
    cfg = _base_config()
    cfg["short_workspace"] = {"enabled": "false"}
    result = validate_config(cfg)
    errs = [e for e in result.errors if e.path == "short_workspace.enabled" and e.severity == "error"]
    assert errs


def test_short_workspace_ttl_hours_float_is_error():
    cfg = _base_config()
    cfg["short_workspace"] = {"ttl_hours": 24.5}
    result = validate_config(cfg)
    errs = [e for e in result.errors if e.path == "short_workspace.ttl_hours" and e.severity == "error"]
    assert errs


def test_short_workspace_root_int_is_error():
    cfg = _base_config()
    cfg["short_workspace"] = {"root": 12345}
    result = validate_config(cfg)
    errs = [e for e in result.errors if e.path == "short_workspace.root" and e.severity == "error"]
    assert errs


def test_automations_dump_channels_non_dict_is_error():
    cfg = _base_config()
    cfg["automations"] = {"dump_channels": "broken"}
    result = validate_config(cfg)
    errs = [e for e in result.errors if e.path == "automations.dump_channels" and e.severity == "error"]
    assert errs
    assert not result.ok


def test_automations_dump_channels_missing_platform_is_error():
    cfg = _base_config()
    cfg["automations"] = {"dump_channels": {"oma_dump": {"channel_id": "123"}}}
    result = validate_config(cfg)
    errs = [
        e for e in result.errors
        if e.path == "automations.dump_channels.oma_dump.platform"
        and e.severity == "error"
    ]
    assert errs


def test_runtime_cleanup_missing_section_passes():
    result = validate_config(_config_with_runtime({}))
    errs = _runtime_errors(result, severity="error")
    assert not errs
    assert result.ok
