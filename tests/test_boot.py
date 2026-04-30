from __future__ import annotations

from oh_my_agent.boot import _apply_v052_defaults


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
