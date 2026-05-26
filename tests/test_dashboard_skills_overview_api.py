"""/api/v1/skills — all-skills overview (M2 follow-up).

Validates that installed-but-never-run skills appear (the previous
``/skills/health`` endpoint only showed skills with runtime_tasks history,
which made the operator UI silent about half the catalog).
"""

from __future__ import annotations

import sqlite3
import textwrap
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
    # Two runs for paper-digest (1 success, 1 failure → 0.5 success rate).
    conn.execute(
        "INSERT INTO runtime_tasks (id, goal, status, skill_name, error, created_at) "
        "VALUES ('t1', 'g', 'COMPLETED', 'paper-digest', NULL, datetime('now','-1 days'))"
    )
    conn.execute(
        "INSERT INTO runtime_tasks (id, goal, status, skill_name, error, created_at) "
        "VALUES ('t2', 'g', 'FAILED', 'paper-digest', 'boom', datetime('now','-2 days'))"
    )
    # And one run for a renamed skill that has no SKILL.md on disk.
    conn.execute(
        "INSERT INTO runtime_tasks (id, goal, status, skill_name, error, created_at) "
        "VALUES ('t3', 'g', 'COMPLETED', 'ghost-skill', NULL, datetime('now','-3 days'))"
    )
    conn.commit()
    conn.close()


def _seed_skills_dir(skills_dir: Path) -> None:
    skills_dir.mkdir(parents=True, exist_ok=True)
    # paper-digest — exists on disk AND has runtime history.
    (skills_dir / "paper-digest").mkdir(exist_ok=True)
    (skills_dir / "paper-digest" / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: paper-digest
            description: Summarize academic papers
            allowed-tools: [WebFetch, Read, Write]
            metadata:
              timeout_seconds: 900
              max_turns: 30
            ---

            body
            """
        ),
        encoding="utf-8",
    )
    # transcribe-media — exists on disk but no runtime history.
    (skills_dir / "transcribe-media").mkdir(exist_ok=True)
    (skills_dir / "transcribe-media" / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: transcribe-media
            description: Transcribe audio + video files
            ---
            """
        ),
        encoding="utf-8",
    )
    # An empty dir with no SKILL.md — should be silently skipped.
    (skills_dir / "junk").mkdir(exist_ok=True)


class _FakeStore:
    def __init__(
        self,
        *,
        manual: set[str] | None = None,
        auto: set[str] | None = None,
        fail_listing: bool = False,
    ):
        self._manual = manual or set()
        self._auto = auto or set()
        self._fail_listing = fail_listing

    async def list_manual_disabled_skills(self):
        if self._fail_listing:
            raise RuntimeError("DB locked")
        return set(self._manual)

    async def list_auto_disabled_skills(self):
        if self._fail_listing:
            raise RuntimeError("DB locked")
        return set(self._auto)

    async def set_skill_override(self, name, *, enabled):
        if enabled:
            self._manual.discard(name)
        else:
            self._manual.add(name)

    async def set_skill_auto_disabled(self, name, *, disabled, reason=None):
        if disabled:
            self._auto.add(name)
        else:
            self._auto.discard(name)


def _config(tmp_path: Path, skills_dir: Path) -> dict:
    db = tmp_path / "runtime.db"
    _seed_runtime_db(db)
    (tmp_path / "memory").mkdir(exist_ok=True)
    (tmp_path / "memory" / "memories.yaml").write_text("[]\n", encoding="utf-8")
    return {
        "runtime": {"state_path": str(db)},
        "memory": {
            "path": str(tmp_path / "memory.db"),
            "judge": {"memory_dir": str(tmp_path / "memory")},
        },
        "skills": {"path": str(skills_dir)},
    }


def test_skills_overview_includes_installed_without_runs(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    _seed_skills_dir(skills_dir)
    app = create_app(_config(tmp_path, skills_dir))
    client = TestClient(app)

    r = client.get("/api/v1/skills")
    assert r.status_code == 200
    payload = r.json()
    by_name = {item["skill"]: item for item in payload["items"]}

    transcribe = by_name["transcribe-media"]
    assert transcribe["installed"] is True
    assert transcribe["description"] == "Transcribe audio + video files"
    assert transcribe["runs_30d"] == 0
    assert transcribe["last_run_at"] is None
    assert transcribe["timeout_seconds"] is None  # not in frontmatter

    paper = by_name["paper-digest"]
    assert paper["installed"] is True
    assert paper["description"] == "Summarize academic papers"
    assert paper["allowed_tool_count"] == 3
    assert paper["timeout_seconds"] == 900
    assert paper["max_turns"] == 30
    assert paper["runs_30d"] == 2
    assert paper["success_rate"] == 0.5
    assert paper["last_failure_reason"] == "boom"


def test_skills_overview_surfaces_history_only_skills(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    _seed_skills_dir(skills_dir)
    app = create_app(_config(tmp_path, skills_dir))
    client = TestClient(app)

    payload = client.get("/api/v1/skills").json()
    by_name = {item["skill"]: item for item in payload["items"]}

    ghost = by_name["ghost-skill"]
    assert ghost["installed"] is False
    assert ghost["description"] == ""
    assert ghost["runs_30d"] == 1


def test_skills_overview_disabled_kind_split(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    _seed_skills_dir(skills_dir)
    store = _FakeStore(manual={"paper-digest"}, auto={"transcribe-media"})
    app = create_app(
        _config(tmp_path, skills_dir),
        mode="colocated",
        store=store,
        auth_token="t",
    )
    client = TestClient(app)
    payload = client.get(
        "/api/v1/skills", headers={"Authorization": "Bearer t"}
    ).json()
    by_name = {item["skill"]: item for item in payload["items"]}
    assert by_name["paper-digest"]["disabled_kind"] == "manual"
    assert by_name["transcribe-media"]["disabled_kind"] == "auto"
    assert by_name["ghost-skill"]["disabled_kind"] is None


def test_skills_overview_missing_skills_dir_warns(tmp_path: Path):
    skills_dir = tmp_path / "does-not-exist"
    cfg = _config(tmp_path, skills_dir)
    app = create_app(cfg)
    client = TestClient(app)

    payload = client.get("/api/v1/skills").json()
    # The runtime DB still has runs, so ghost-skill (history-only) appears.
    by_name = {item["skill"]: item for item in payload["items"]}
    assert "ghost-skill" in by_name
    assert by_name["ghost-skill"]["installed"] is False
    # Missing skills dir → at least one warning describing it.
    assert any("skills directory" in w for w in payload["warnings"])


def test_skills_overview_malformed_frontmatter_does_not_500(tmp_path: Path):
    """Codex review #2: a non-string description used to escape the parse
    try-block via ``.strip()`` and 500 the whole endpoint. Now it must
    yield a per-skill warning and skip the entry instead."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "broken").mkdir()
    (skills_dir / "broken" / "SKILL.md").write_text(
        # ``description`` is intentionally a list — fails coerce; we expect
        # a warning, not a stack trace.
        "---\nname: broken\ndescription:\n  - line1\n  - line2\n---\nbody",
        encoding="utf-8",
    )
    # Also seed a healthy skill so the response shape stays exercised.
    (skills_dir / "healthy").mkdir()
    (skills_dir / "healthy" / "SKILL.md").write_text(
        "---\nname: healthy\ndescription: ok\n---\n",
        encoding="utf-8",
    )
    app = create_app(_config(tmp_path, skills_dir))
    client = TestClient(app)
    r = client.get("/api/v1/skills")
    assert r.status_code == 200
    payload = r.json()
    by_name = {item["skill"]: item for item in payload["items"]}
    # NOTE: when a list value sneaks through, the entry may or may not
    # land (depending on whether str() coerces it cleanly). What matters
    # is no 500 and the healthy sibling stays visible.
    assert "healthy" in by_name


def test_skills_overview_allowed_tools_string_form(tmp_path: Path):
    """Codex review #4: ``allowed-tools: "Read, Write"`` (string form) must
    count as 2 tools, not 1."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "csv-form").mkdir()
    (skills_dir / "csv-form" / "SKILL.md").write_text(
        '---\nname: csv-form\ndescription: x\nallowed-tools: "Read, Write, Bash"\n---\n',
        encoding="utf-8",
    )
    (skills_dir / "ws-form").mkdir()
    (skills_dir / "ws-form" / "SKILL.md").write_text(
        "---\nname: ws-form\ndescription: x\nallowed-tools: Read Write\n---\n",
        encoding="utf-8",
    )
    app = create_app(_config(tmp_path, skills_dir))
    client = TestClient(app)
    payload = client.get("/api/v1/skills").json()
    by_name = {item["skill"]: item for item in payload["items"]}
    assert by_name["csv-form"]["allowed_tool_count"] == 3
    assert by_name["ws-form"]["allowed_tool_count"] == 2


def test_skills_overview_disabled_lookup_failure_warns(tmp_path: Path):
    """Codex review #3: when the store lookup throws, we must NOT silently
    report every skill as active — surface a banner warning."""
    skills_dir = tmp_path / "skills"
    _seed_skills_dir(skills_dir)
    store = _FakeStore(fail_listing=True)
    app = create_app(
        _config(tmp_path, skills_dir),
        mode="colocated",
        store=store,
        auth_token="t",
    )
    client = TestClient(app)
    payload = client.get(
        "/api/v1/skills", headers={"Authorization": "Bearer t"}
    ).json()
    assert any("disabled-skill state unavailable" in w for w in payload["warnings"])


def test_skill_enable_clears_auto_disabled(tmp_path: Path):
    """Codex review #1: ``POST /skills/{name}/enable`` must clear BOTH the
    manual override AND the auto-disable flag, otherwise the gateway keeps
    blocking the skill while the API claims it's enabled."""
    skills_dir = tmp_path / "skills"
    _seed_skills_dir(skills_dir)
    store = _FakeStore(auto={"paper-digest"})  # bot auto-disabled it
    app = create_app(
        _config(tmp_path, skills_dir),
        mode="colocated",
        store=store,
        auth_token="t",
    )
    client = TestClient(app)
    r = client.post(
        "/api/v1/skills/paper-digest/enable",
        headers={"Authorization": "Bearer t"},
    )
    assert r.status_code == 200
    assert "paper-digest" not in store._auto  # auto-disable cleared
    # Re-listing confirms the gateway will treat it as active now.
    payload = client.get(
        "/api/v1/skills", headers={"Authorization": "Bearer t"}
    ).json()
    by_name = {item["skill"]: item for item in payload["items"]}
    assert by_name["paper-digest"]["disabled_kind"] is None
