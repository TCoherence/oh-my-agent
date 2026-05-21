"""M2 PR3 / M3.5 — dashboard automation control API contract tests.

Drives the FastAPI app with a fake scheduler injected via co-location mode,
covering: list, fire (auth required), patch (validation), and the
readonly-mode 503 + auth 401/503 contract from M2 PR1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from oh_my_agent.dashboard.app import create_app  # noqa: E402


class _FakeRecord:
    def __init__(self, name, enabled=True, cron=None, interval_seconds=60):
        self.name = name
        self.enabled = enabled
        self.cron = cron
        self.interval_seconds = interval_seconds
        self.agent = "claude"
        self.skill_name = None
        self.platform = "discord"
        self.channel_id = "123"

    @property
    def schedule_kind(self):
        return "cron" if self.cron else "interval"


class _FakeScheduler:
    def __init__(self):
        self._records = {
            "daily": _FakeRecord("daily", enabled=True, interval_seconds=60),
            "weekly": _FakeRecord("weekly", enabled=False, cron="0 9 * * 1", interval_seconds=None),
        }
        self.fired: list[str] = []

    def list_automations(self):
        return [self._records[k] for k in sorted(self._records)]

    def compute_all_next_run_at(self):
        return {"daily": datetime(2026, 5, 22, tzinfo=timezone.utc), "weekly": None}

    async def fire_job_now(self, name):
        if name not in self._records:
            return "not_found"
        self.fired.append(name)
        return "ok"

    async def patch_automation(self, name, updates):
        if name not in self._records:
            raise ValueError(f"automation {name!r} not found")
        if updates.get("interval_seconds") == 0:
            raise ValueError("interval_seconds must be > 0")
        rec = self._records[name]
        for k, v in updates.items():
            setattr(rec, k, v)
        return rec


def _minimal_config(tmp_path: Path) -> dict:
    return {
        "runtime": {"state_path": str(tmp_path / "runtime.db")},
        "memory": {"path": str(tmp_path / "memory.db")},
    }


def _colocated_client(tmp_path: Path, *, auth_token=None) -> tuple[TestClient, _FakeScheduler]:
    sched = _FakeScheduler()
    app = create_app(
        _minimal_config(tmp_path),
        mode="colocated",
        scheduler=sched,
        auth_token=auth_token,
    )
    return TestClient(app), sched


def _readonly_client(tmp_path: Path) -> TestClient:
    app = create_app(_minimal_config(tmp_path))  # default readonly
    return TestClient(app)


def test_list_automations_colocated(tmp_path: Path):
    client, _ = _colocated_client(tmp_path)
    r = client.get("/api/v1/automations")
    assert r.status_code == 200
    items = r.json()["items"]
    assert {i["name"] for i in items} == {"daily", "weekly"}
    daily = next(i for i in items if i["name"] == "daily")
    assert daily["enabled"] is True
    assert daily["schedule_kind"] == "interval"
    assert daily["next_run_at"] is not None
    weekly = next(i for i in items if i["name"] == "weekly")
    assert weekly["schedule_kind"] == "cron"
    assert weekly["next_run_at"] is None


def test_list_automations_readonly_503(tmp_path: Path):
    client = _readonly_client(tmp_path)
    r = client.get("/api/v1/automations")
    assert r.status_code == 503


def test_fire_requires_auth_when_token_set(tmp_path: Path):
    client, sched = _colocated_client(tmp_path, auth_token="secret")
    # No auth header → 401
    r = client.post("/api/v1/automations/daily/fire")
    assert r.status_code == 401
    assert sched.fired == []
    # With auth header → 200
    r = client.post(
        "/api/v1/automations/daily/fire",
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 200
    assert r.json()["result"] == "ok"
    assert sched.fired == ["daily"]


def test_fire_503_when_token_unset(tmp_path: Path):
    """Colocated but no auth_token → write routes 503 (must configure token)."""
    client, sched = _colocated_client(tmp_path, auth_token=None)
    r = client.post("/api/v1/automations/daily/fire")
    assert r.status_code == 503
    assert sched.fired == []


def test_fire_unknown_automation_404(tmp_path: Path):
    client, _ = _colocated_client(tmp_path, auth_token="secret")
    r = client.post(
        "/api/v1/automations/nope/fire",
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 404


def test_fire_rejects_query_token(tmp_path: Path):
    """Codex M2 PR1: write routes reject ?token= query param."""
    client, sched = _colocated_client(tmp_path, auth_token="secret")
    r = client.post("/api/v1/automations/daily/fire?token=secret")
    assert r.status_code == 401
    assert sched.fired == []


def test_patch_automation_updates(tmp_path: Path):
    client, sched = _colocated_client(tmp_path, auth_token="secret")
    r = client.patch(
        "/api/v1/automations/daily",
        headers={"Authorization": "Bearer secret"},
        json={"enabled": False, "interval_seconds": 120},
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["interval_seconds"] == 120


def test_patch_rejects_invalid_value(tmp_path: Path):
    client, _ = _colocated_client(tmp_path, auth_token="secret")
    r = client.patch(
        "/api/v1/automations/daily",
        headers={"Authorization": "Bearer secret"},
        json={"interval_seconds": 0},
    )
    assert r.status_code == 400


def test_patch_rejects_no_patchable_keys(tmp_path: Path):
    client, _ = _colocated_client(tmp_path, auth_token="secret")
    r = client.patch(
        "/api/v1/automations/daily",
        headers={"Authorization": "Bearer secret"},
        json={"prompt": "malicious"},  # not whitelisted
    )
    assert r.status_code == 400


def test_patch_rejects_disallowed_key_mixed_with_allowed(tmp_path: Path):
    """Codex M2 PR3: disallowed key alongside an allowed one must 400,
    not silently drop the bad key."""
    client, sched = _colocated_client(tmp_path, auth_token="secret")
    r = client.patch(
        "/api/v1/automations/daily",
        headers={"Authorization": "Bearer secret"},
        json={"enabled": False, "prompt": "sneaky rewrite"},
    )
    assert r.status_code == 400
    # The allowed key must NOT have been applied
    assert sched._records["daily"].enabled is True


def test_fire_already_firing_409(tmp_path: Path):
    """already_firing → 409 Conflict (manual fire refused)."""
    client, sched = _colocated_client(tmp_path, auth_token="secret")

    async def _already(name):
        return "already_firing"

    sched.fire_job_now = _already  # type: ignore[method-assign]
    r = client.post(
        "/api/v1/automations/daily/fire",
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 409


def test_patch_readonly_503(tmp_path: Path):
    client = _readonly_client(tmp_path)
    r = client.patch("/api/v1/automations/daily", json={"enabled": False})
    assert r.status_code == 503
