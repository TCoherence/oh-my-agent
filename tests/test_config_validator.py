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
