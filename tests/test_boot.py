from __future__ import annotations

import pytest

from oh_my_agent.boot import (
    ConfigShapeError,
    _apply_v052_defaults,
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
