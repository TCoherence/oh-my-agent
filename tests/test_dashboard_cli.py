"""End-to-end smoke test for the ``oma-dashboard`` CLI entry point.

Spawns uvicorn in a background thread on port 0 (OS-assigned), hits
``/healthz`` over HTTP, and shuts down. Exercises the full ``main()``
codepath: arg parsing → config load → app construction → server start.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

import uvicorn  # noqa: E402

from oh_my_agent.dashboard.app import create_app  # noqa: E402


def _seed_minimal_config(tmp_path: Path) -> str:
    """Write a minimal config.yaml + minimal runtime tree, return config path."""

    tasks_dir = tmp_path / "tasks"
    logs_dir = tmp_path / "logs"
    memory_dir = tmp_path / "memory"
    tasks_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)
    memory_dir.mkdir(parents=True)
    (logs_dir / "service.log").write_text("", encoding="utf-8")
    (memory_dir / "memories.yaml").write_text("entries: []\n", encoding="utf-8")

    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        f"""
runtime:
  worktree_root: {tasks_dir}
  state_path: {tmp_path / "runtime.db"}
  reports_dir: ""
memory:
  path: {tmp_path / "memory.db"}
  judge:
    memory_dir: {memory_dir}
skills:
  telemetry_path: {tmp_path / "skills.db"}
""",
        encoding="utf-8",
    )
    return str(config_yaml)


def _start_uvicorn(app, port_holder: list[int], started: threading.Event) -> uvicorn.Server:
    """Start uvicorn on port 0, recording the assigned port back."""

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)

    # Patch up: uvicorn picks the actual port after socket bind. We sniff it
    # from the server's started servers list by polling the started flag.
    def watcher() -> None:
        # Wait until the server has bound; uvicorn sets `server.started = True`
        # once startup completes.
        for _ in range(100):
            if server.started and server.servers:
                break
            time.sleep(0.05)
        if server.servers:
            sock = server.servers[0].sockets[0]
            port_holder.append(sock.getsockname()[1])
            started.set()

    threading.Thread(target=watcher, daemon=True).start()

    # uvicorn.Server.run() is sync — call from caller's thread.
    return server


def test_oma_dashboard_serves_healthz(tmp_path: Path) -> None:
    """Boot the FastAPI app via uvicorn on a free port, hit /healthz, shut down.

    This exercises the same codepath as ``oma-dashboard`` (cli.main calls
    ``uvicorn.run(app, ...)``) without going through subprocess / argparse.
    """

    config_path = _seed_minimal_config(tmp_path)

    # Load config the same way cli.main does.
    from oh_my_agent.config import load_config

    config = load_config(config_path)
    app = create_app(config)

    port_holder: list[int] = []
    started = threading.Event()
    server = _start_uvicorn(app, port_holder, started)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    try:
        assert started.wait(timeout=10.0), "uvicorn never bound a port"
        port = port_holder[0]

        # Now hit /healthz with stdlib urllib
        url = f"http://127.0.0.1:{port}/healthz"
        with urllib.request.urlopen(url, timeout=5.0) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert '"status":"ok"' in body or '"status": "ok"' in body

        # Hit / for a full render too
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5.0) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "oh-my-agent dashboard" in body
    finally:
        server.should_exit = True
        server_thread.join(timeout=5.0)


def test_oma_dashboard_cli_rejects_missing_config(tmp_path: Path, capsys) -> None:
    """``main()`` returns 2 when the config file is absent."""

    from oh_my_agent.dashboard.cli import main

    rc = main(["--config", str(tmp_path / "does-not-exist.yaml")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "config not found" in captured.err


# ---------------------------------------------------------------------------
# _resolve_refresh_seconds — CLI flag > env var > default precedence
# ---------------------------------------------------------------------------


def test_resolve_refresh_seconds_default_is_300(monkeypatch) -> None:
    from oh_my_agent.dashboard.cli import _resolve_refresh_seconds

    monkeypatch.delenv("OMA_DASHBOARD_REFRESH_SECONDS", raising=False)
    assert _resolve_refresh_seconds(None) == 300


def test_resolve_refresh_seconds_cli_flag_wins(monkeypatch) -> None:
    from oh_my_agent.dashboard.cli import _resolve_refresh_seconds

    monkeypatch.setenv("OMA_DASHBOARD_REFRESH_SECONDS", "60")
    assert _resolve_refresh_seconds(120) == 120  # CLI flag > env


def test_resolve_refresh_seconds_env_var_used_when_no_flag(monkeypatch) -> None:
    from oh_my_agent.dashboard.cli import _resolve_refresh_seconds

    monkeypatch.setenv("OMA_DASHBOARD_REFRESH_SECONDS", "600")
    assert _resolve_refresh_seconds(None) == 600


def test_resolve_refresh_seconds_invalid_env_falls_to_default(monkeypatch) -> None:
    from oh_my_agent.dashboard.cli import _resolve_refresh_seconds

    monkeypatch.setenv("OMA_DASHBOARD_REFRESH_SECONDS", "not-an-int")
    assert _resolve_refresh_seconds(None) == 300


def test_resolve_refresh_seconds_zero_is_kept(monkeypatch) -> None:
    """0 means 'disable auto-refresh' — must NOT fall back to default."""

    from oh_my_agent.dashboard.cli import _resolve_refresh_seconds

    monkeypatch.delenv("OMA_DASHBOARD_REFRESH_SECONDS", raising=False)
    assert _resolve_refresh_seconds(0) == 0
    monkeypatch.setenv("OMA_DASHBOARD_REFRESH_SECONDS", "0")
    assert _resolve_refresh_seconds(None) == 0


def test_resolve_refresh_seconds_negative_clamped_to_zero(monkeypatch) -> None:
    from oh_my_agent.dashboard.cli import _resolve_refresh_seconds

    monkeypatch.delenv("OMA_DASHBOARD_REFRESH_SECONDS", raising=False)
    assert _resolve_refresh_seconds(-1) == 0
    monkeypatch.setenv("OMA_DASHBOARD_REFRESH_SECONDS", "-5")
    assert _resolve_refresh_seconds(None) == 0
