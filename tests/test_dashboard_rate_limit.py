"""Dashboard rate-limit tests — RateLimiter unit + middleware integration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from oh_my_agent.dashboard.rate_limit import RateLimiter, build_rate_limiter

# ── Unit: client IP resolution (XFF + trusted proxies) ────────────── #


def test_client_ip_direct_when_peer_not_trusted():
    rl = RateLimiter(trusted_proxies={"10.0.0.1"})
    # Direct peer is not a trusted proxy → ignore XFF (spoofable)
    ip = rl.client_ip(direct_ip="203.0.113.9", xff_header="1.2.3.4")
    assert ip == "203.0.113.9"


def test_client_ip_uses_xff_when_peer_is_trusted_proxy():
    rl = RateLimiter(trusted_proxies={"10.0.0.1"})
    ip = rl.client_ip(direct_ip="10.0.0.1", xff_header="203.0.113.9")
    assert ip == "203.0.113.9"


def test_client_ip_walks_chained_proxies():
    rl = RateLimiter(trusted_proxies={"10.0.0.1", "10.0.0.2"})
    # chain: realclient, proxy2, proxy1(direct). Walk right-to-left, skip trusted.
    ip = rl.client_ip(direct_ip="10.0.0.1", xff_header="203.0.113.9, 10.0.0.2")
    assert ip == "203.0.113.9"


def test_client_ip_all_trusted_falls_back_to_direct():
    rl = RateLimiter(trusted_proxies={"10.0.0.1"})
    ip = rl.client_ip(direct_ip="10.0.0.1", xff_header="10.0.0.1")
    assert ip == "10.0.0.1"


# ── Unit: global token bucket ─────────────────────────────────────── #


def test_global_bucket_blocks_after_burst():
    rl = RateLimiter(global_rps=0.0001, burst=3)  # ~no refill
    assert rl.allow_global()
    assert rl.allow_global()
    assert rl.allow_global()
    assert not rl.allow_global()  # burst exhausted


def test_global_disabled_when_rps_zero():
    rl = RateLimiter(global_rps=0, burst=1)
    for _ in range(100):
        assert rl.allow_global()  # disabled → always allow


# ── Unit: per-IP auth-failure lockout ─────────────────────────────── #


def test_auth_failure_locks_after_threshold():
    rl = RateLimiter(auth_fail_max=3, auth_fail_window_seconds=300, auth_fail_cooldown_seconds=900)
    ip = "203.0.113.9"
    assert not rl.is_locked(ip)
    assert not rl.record_auth_failure(ip)  # 1
    assert not rl.record_auth_failure(ip)  # 2
    assert rl.record_auth_failure(ip)      # 3 → locks
    assert rl.is_locked(ip)
    # A different IP is unaffected
    assert not rl.is_locked("198.51.100.1")


def test_lock_expires_after_cooldown(monkeypatch):
    import oh_my_agent.dashboard.rate_limit as rlmod

    t = {"now": 1000.0}
    monkeypatch.setattr(rlmod.time, "monotonic", lambda: t["now"])
    rl = RateLimiter(auth_fail_max=1, auth_fail_window_seconds=300, auth_fail_cooldown_seconds=60)
    ip = "203.0.113.9"
    assert rl.record_auth_failure(ip)
    assert rl.is_locked(ip)
    t["now"] += 61  # past cooldown
    assert not rl.is_locked(ip)


def test_failures_age_out_of_window(monkeypatch):
    import oh_my_agent.dashboard.rate_limit as rlmod

    t = {"now": 1000.0}
    monkeypatch.setattr(rlmod.time, "monotonic", lambda: t["now"])
    rl = RateLimiter(auth_fail_max=3, auth_fail_window_seconds=100, auth_fail_cooldown_seconds=900)
    ip = "203.0.113.9"
    rl.record_auth_failure(ip)  # t=1000
    t["now"] += 60
    rl.record_auth_failure(ip)  # t=1060
    t["now"] += 60              # t=1120; first failure (1000) now >100s old → ages out
    assert not rl.record_auth_failure(ip)  # only 2 in window → no lock
    assert not rl.is_locked(ip)


def test_build_rate_limiter_disabled():
    assert build_rate_limiter({"enabled": False}) is None
    assert build_rate_limiter({}) is not None  # default enabled


def test_eviction_keeps_tracked_ips_bounded(monkeypatch):
    """Codex catch: eviction must keep _failures <= cap even as new IPs
    arrive (was off-by-one, leaving cap+1)."""
    import oh_my_agent.dashboard.rate_limit as rlmod

    monkeypatch.setattr(rlmod, "_MAX_TRACKED_IPS", 5)
    rl = RateLimiter(auth_fail_max=99, auth_fail_window_seconds=9999)
    for i in range(20):
        rl.record_auth_failure(f"10.0.0.{i}")
    assert len(rl._failures) <= 5
    # The most recently touched IP is retained
    assert "10.0.0.19" in rl._failures


# ── Integration: middleware via TestClient ────────────────────────── #

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from oh_my_agent.dashboard.app import create_app  # noqa: E402


def _config(tmp_path: Path, rate_limit: dict | None = None) -> dict:
    db = tmp_path / "runtime.db"
    sqlite3.connect(db).close()
    (tmp_path / "memory").mkdir(exist_ok=True)
    (tmp_path / "memory" / "memories.yaml").write_text("[]\n", encoding="utf-8")
    cfg = {
        "runtime": {"state_path": str(db)},
        "memory": {"path": str(tmp_path / "memory.db"), "judge": {"memory_dir": str(tmp_path / "memory")}},
    }
    if rate_limit is not None:
        cfg["dashboard"] = {"rate_limit": rate_limit}
    return cfg


def test_middleware_locks_out_after_repeated_401(tmp_path: Path):
    cfg = _config(tmp_path, rate_limit={
        "enabled": True, "global_rps": 1000, "burst": 1000,
        "auth_fail_max": 3, "auth_fail_window_seconds": 300, "auth_fail_cooldown_seconds": 900,
    })
    app = create_app(cfg, auth_token="secret")  # auth on → wrong token = 401
    client = TestClient(app)
    # 3 wrong-token requests → 401 each, then locked
    for _ in range(3):
        r = client.get("/api/v1/skills/health", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
    # 4th request (even with CORRECT token) is now locked out → 429
    r = client.get("/api/v1/skills/health", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 429


def test_middleware_healthz_exempt_from_rate_limit(tmp_path: Path):
    cfg = _config(tmp_path, rate_limit={"enabled": True, "global_rps": 0.0001, "burst": 1})
    app = create_app(cfg)
    client = TestClient(app)
    # Exhaust the global bucket on a normal route
    client.get("/api/v1/sessions")
    # healthz still 200 (exempt)
    for _ in range(5):
        assert client.get("/healthz").status_code == 200


def test_middleware_global_cap_returns_429(tmp_path: Path):
    cfg = _config(tmp_path, rate_limit={"enabled": True, "global_rps": 0.0001, "burst": 2})
    app = create_app(cfg)
    client = TestClient(app)
    codes = [client.get("/api/v1/healthz").status_code for _ in range(2)]
    # /api/v1/healthz is exempt (in _AUTH_PUBLIC_PATHS) — never 429
    assert all(c == 200 for c in codes)
    # a non-exempt route hits the cap
    seen = [client.get("/api/v1/sessions").status_code for _ in range(4)]
    assert 429 in seen


def test_middleware_disabled_no_limiting(tmp_path: Path):
    cfg = _config(tmp_path, rate_limit={"enabled": False})
    app = create_app(cfg)
    client = TestClient(app)
    # No limiter attached
    assert not hasattr(app.state, "rate_limiter")
    for _ in range(50):
        assert client.get("/api/v1/sessions").status_code in (200, 503)
