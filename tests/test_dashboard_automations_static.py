"""/api/v1/automations — standalone fallback to YAML reading.

Without this, the standalone dashboard 503s on every automations poll,
so the operator can't see *what's scheduled* without spinning up the
bot. The colocated path (with scheduler) keeps its existing contract.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from oh_my_agent.dashboard.app import create_app  # noqa: E402


def _seed_automations_dir(automations_dir: Path) -> None:
    automations_dir.mkdir(parents=True, exist_ok=True)
    (automations_dir / "daily-finance.yaml").write_text(
        textwrap.dedent(
            """\
            name: daily-finance
            enabled: true
            platform: discord
            channel_id: "123"
            agent: claude
            skill_name: market-briefing-finance
            cron: "0 9 * * *"
            prompt: Run the daily finance brief.
            """
        ),
        encoding="utf-8",
    )
    (automations_dir / "interval-job.yaml").write_text(
        textwrap.dedent(
            """\
            name: interval-job
            enabled: false
            platform: discord
            channel_id: "456"
            agent: gemini
            interval_seconds: 600
            prompt: poll thing
            """
        ),
        encoding="utf-8",
    )
    # .bak siblings the scheduler leaves behind — must be ignored.
    (automations_dir / "daily-finance.yaml.bak").write_text(
        "name: daily-finance-stale\nenabled: true\ncron: 0 9 * * *\nplatform: x\nchannel_id: x\n",
        encoding="utf-8",
    )
    (automations_dir / "old.bak.yaml").write_text(
        "name: oldbak\nenabled: true\ncron: 0 9 * * *\nplatform: x\nchannel_id: x\n",
        encoding="utf-8",
    )
    # Malformed YAML — must yield a warning, not 500.
    (automations_dir / "broken.yaml").write_text(
        ":\n:not-valid:yaml:\n", encoding="utf-8"
    )
    # Root-not-a-mapping — also a soft warning.
    (automations_dir / "scalar.yaml").write_text("just a string\n", encoding="utf-8")


def _config(tmp_path: Path, automations_dir: Path) -> dict:
    return {
        "automations": {"storage_dir": str(automations_dir)},
        "runtime": {"state_path": str(tmp_path / "runtime.db")},
        "memory": {"path": str(tmp_path / "memory.db")},
    }


def test_standalone_automations_lists_from_yaml(tmp_path: Path):
    automations_dir = tmp_path / "automations"
    _seed_automations_dir(automations_dir)
    app = create_app(_config(tmp_path, automations_dir))  # readonly/standalone
    client = TestClient(app)

    r = client.get("/api/v1/automations")
    assert r.status_code == 200
    payload = r.json()
    assert payload["mode"] == "static"
    by_name = {item["name"]: item for item in payload["items"]}

    # Stale .bak files must be filtered out — they aren't live schedules.
    assert "daily-finance-stale" not in by_name
    assert "oldbak" not in by_name

    finance = by_name["daily-finance"]
    assert finance["enabled"] is True
    assert finance["schedule_kind"] == "cron"
    assert finance["cron"] == "0 9 * * *"
    assert finance["agent"] == "claude"
    assert finance["skill_name"] == "market-briefing-finance"
    assert finance["next_run_at"] is None  # static cannot compute

    interval = by_name["interval-job"]
    assert interval["enabled"] is False
    assert interval["schedule_kind"] == "interval"
    assert interval["interval_seconds"] == 600


def test_standalone_automations_soft_fails_malformed(tmp_path: Path):
    automations_dir = tmp_path / "automations"
    _seed_automations_dir(automations_dir)
    app = create_app(_config(tmp_path, automations_dir))
    client = TestClient(app)

    payload = client.get("/api/v1/automations").json()
    # Two malformed files → at least two warnings, healthy items still
    # exposed.
    assert len(payload["warnings"]) >= 2
    names = {item["name"] for item in payload["items"]}
    assert "daily-finance" in names
    assert "interval-job" in names


def test_standalone_automations_missing_dir_warns(tmp_path: Path):
    app = create_app(_config(tmp_path, tmp_path / "does-not-exist"))
    client = TestClient(app)
    payload = client.get("/api/v1/automations").json()
    assert payload["mode"] == "static"
    assert payload["items"] == []
    assert any("automations directory" in w for w in payload["warnings"])


def test_standalone_write_endpoints_still_503(tmp_path: Path):
    """The read fallback must NOT loosen the write contract — fire/patch
    keep their 503s because they need a real scheduler."""
    automations_dir = tmp_path / "automations"
    _seed_automations_dir(automations_dir)
    app = create_app(_config(tmp_path, automations_dir))
    client = TestClient(app)

    r = client.post("/api/v1/automations/daily-finance/fire")
    assert r.status_code == 503
    r = client.patch(
        "/api/v1/automations/daily-finance", json={"enabled": False}
    )
    assert r.status_code == 503


class _FakeScheduler:
    """Bare-minimum scheduler stand-in for the live-mode assertion."""

    def list_automations(self):
        from oh_my_agent.automation.scheduler import AutomationRecord

        return [
            AutomationRecord(
                name="live-job",
                platform="discord",
                channel_id="999",
                prompt="x",
                enabled=True,
                cron="0 12 * * *",
                agent="claude",
                skill_name=None,
            ),
        ]

    def compute_all_next_run_at(self):
        from datetime import datetime, timezone
        return {"live-job": datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)}


def test_standalone_accepts_yml_extension(tmp_path: Path):
    """Codex review #2: scheduler globs both *.yaml AND *.yml. Static
    mode must too, otherwise standalone silently hides .yml jobs that
    the live bot is actually running."""
    automations_dir = tmp_path / "automations"
    automations_dir.mkdir(parents=True)
    (automations_dir / "yml-form.yml").write_text(
        textwrap.dedent(
            """\
            name: yml-form
            enabled: true
            platform: discord
            channel_id: "1"
            agent: claude
            cron: "*/15 * * * *"
            prompt: ping
            """
        ),
        encoding="utf-8",
    )
    app = create_app(_config(tmp_path, automations_dir))
    client = TestClient(app)
    names = {item["name"] for item in client.get("/api/v1/automations").json()["items"]}
    assert "yml-form" in names


def test_standalone_invalid_interval_warns_not_500(tmp_path: Path):
    """Codex review #5b: scheduler silently soft-skips non-int interval
    values; static mode must match (no 500)."""
    automations_dir = tmp_path / "automations"
    automations_dir.mkdir(parents=True)
    (automations_dir / "bad-interval.yaml").write_text(
        textwrap.dedent(
            """\
            name: bad-interval
            enabled: true
            platform: discord
            channel_id: "1"
            agent: claude
            interval_seconds: never
            prompt: x
            """
        ),
        encoding="utf-8",
    )
    app = create_app(_config(tmp_path, automations_dir))
    client = TestClient(app)
    r = client.get("/api/v1/automations")
    assert r.status_code == 200
    payload = r.json()
    by_name = {item["name"]: item for item in payload["items"]}
    assert by_name["bad-interval"]["interval_seconds"] is None
    assert any("interval_seconds=" in w for w in payload["warnings"])


def test_standalone_relative_storage_dir_uses_project_root(tmp_path: Path):
    """Codex review #5a: relative ``automations.storage_dir`` must resolve
    against project_root (config.yaml's parent), matching the scheduler.
    Without this, standalone scans a different directory than the bot."""
    # Pretend project_root = tmp_path / "project"; relative config value
    # "automations" should resolve to tmp_path / "project" / "automations".
    project_root = tmp_path / "project"
    project_root.mkdir()
    automations_dir = project_root / "automations"
    _seed_automations_dir(automations_dir)
    cfg = {
        "automations": {"storage_dir": "automations"},  # relative!
        "runtime": {"state_path": str(tmp_path / "runtime.db")},
        "memory": {"path": str(tmp_path / "memory.db")},
    }
    app = create_app(cfg, project_root=project_root)
    client = TestClient(app)
    payload = client.get("/api/v1/automations").json()
    names = {item["name"] for item in payload["items"]}
    # Both seeded jobs must be visible — proves the resolution found
    # the right directory.
    assert "daily-finance" in names
    assert "interval-job" in names


def test_live_mode_still_works(tmp_path: Path):
    """Colocated path must keep its existing live shape: items with
    next_run_at + mode='live'."""
    app = create_app(
        _config(tmp_path, tmp_path / "automations"),
        mode="colocated",
        scheduler=_FakeScheduler(),
    )
    client = TestClient(app)
    payload = client.get("/api/v1/automations").json()
    assert payload["mode"] == "live"
    assert payload["warnings"] == []
    assert payload["items"][0]["name"] == "live-job"
    assert payload["items"][0]["next_run_at"] == "2026-06-01T12:00:00+00:00"
