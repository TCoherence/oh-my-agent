from __future__ import annotations

import pytest

from oh_my_agent.boot import _apply_v052_defaults, verify_integrity


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


def test_apply_v052_defaults_skips_non_dict_runtime():
    cfg = {"runtime": "garbage"}
    # Must not crash — validator will reject the bad shape.
    _apply_v052_defaults(cfg)
    assert cfg["runtime"] == "garbage"


def test_apply_v052_defaults_skips_non_dict_cleanup():
    cfg = {"runtime": {"cleanup": "garbage"}}
    _apply_v052_defaults(cfg)
    # cleanup is preserved; validator catches it.
    assert cfg["runtime"]["cleanup"] == "garbage"


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


def test_apply_v052_defaults_skips_non_dict_intermediate():
    """Non-dict nested mapping (e.g. auth.providers) should not crash —
    function bails so validate_config can surface the error.
    """
    cfg = {"auth": {"providers": "broken"}}
    _apply_v052_defaults(cfg)
    # Function must not raise; cleanup chain after auth never runs but
    # that's expected because validation rejects this config.


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
