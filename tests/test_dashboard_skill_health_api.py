"""M2 PR4 — skill health API + manual enable/disable contract tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from oh_my_agent.dashboard.app import create_app  # noqa: E402


def _seed_runtime_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE runtime_tasks (
            id TEXT PRIMARY KEY, platform TEXT, channel_id TEXT, thread_id TEXT,
            created_by TEXT, goal TEXT, status TEXT, skill_name TEXT,
            error TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        "INSERT INTO runtime_tasks (id, goal, status, skill_name, error, created_at) "
        "VALUES ('t1', 'g', 'COMPLETED', 'paper-digest', NULL, datetime('now','-1 days'))"
    )
    conn.execute(
        "INSERT INTO runtime_tasks (id, goal, status, skill_name, error, created_at) "
        "VALUES ('t2', 'g', 'FAILED', 'paper-digest', 'boom', datetime('now','-2 days'))"
    )
    conn.commit()
    conn.close()


class _FakeStore:
    def __init__(self):
        self.overrides: dict[str, bool] = {}

    async def list_auto_disabled_skills(self):
        return set()

    async def list_manual_disabled_skills(self):
        return {k for k, v in self.overrides.items() if v is False}

    async def set_skill_override(self, skill_name, *, enabled):
        self.overrides[skill_name] = enabled


def _config(tmp_path: Path) -> dict:
    db = tmp_path / "runtime.db"
    _seed_runtime_db(db)
    (tmp_path / "memory").mkdir(exist_ok=True)
    (tmp_path / "memory" / "memories.yaml").write_text("[]\n", encoding="utf-8")
    return {
        "runtime": {"state_path": str(db)},
        "memory": {"path": str(tmp_path / "memory.db"), "judge": {"memory_dir": str(tmp_path / "memory")}},
    }


def test_skills_health_lists(tmp_path: Path):
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    r = client.get("/api/v1/skills/health")
    assert r.status_code == 200
    items = r.json()["items"]
    pd = next(i for i in items if i["skill"] == "paper-digest")
    assert pd["runs_30d"] == 2
    assert pd["success_rate"] == 0.5
    assert pd["last_failure_reason"] == "boom"


def test_skill_enable_disable_requires_auth(tmp_path: Path):
    store = _FakeStore()
    app = create_app(
        _config(tmp_path), mode="colocated", store=store, auth_token="secret"
    )
    client = TestClient(app)
    # No auth → 401
    r = client.post("/api/v1/skills/paper-digest/disable")
    assert r.status_code == 401
    # With auth → 200
    r = client.post(
        "/api/v1/skills/paper-digest/disable",
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert store.overrides["paper-digest"] is False
    # Re-enable
    r = client.post(
        "/api/v1/skills/paper-digest/enable",
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 200
    assert store.overrides["paper-digest"] is True


def test_skill_disable_readonly_503(tmp_path: Path):
    app = create_app(_config(tmp_path))  # readonly
    client = TestClient(app)
    r = client.post("/api/v1/skills/paper-digest/disable")
    assert r.status_code == 503


def test_skills_health_reflects_disabled(tmp_path: Path):
    store = _FakeStore()
    store.overrides["paper-digest"] = False  # disabled
    app = create_app(_config(tmp_path), mode="colocated", store=store, auth_token="x")
    client = TestClient(app)
    # Global auth middleware covers reads too when a token is configured.
    r = client.get("/api/v1/skills/health", headers={"Authorization": "Bearer x"})
    items = r.json()["items"]
    pd = next(i for i in items if i["skill"] == "paper-digest")
    assert pd["disabled"] is True


def test_skill_recent_tasks(tmp_path: Path):
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    r = client.get("/api/v1/skills/paper-digest/recent_tasks")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert {i["id"] for i in items} == {"t1", "t2"}
