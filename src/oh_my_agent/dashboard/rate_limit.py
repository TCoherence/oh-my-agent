"""Dashboard rate limiting — defense-in-depth for public exposure.

Two independent limiters, both in-process (single dashboard process):

1. **Global token bucket** — caps total requests/sec across all clients.
   Blunt DoS protection; the SPA's 2-5s polling sits far under any sane
   cap, so legit use is never throttled.

2. **Per-IP auth-failure lockout** — counts 401 responses per client IP in
   a rolling window; once a threshold is crossed the IP is locked out
   (429) for a cooldown. Directly counters token brute-force.

Client IP resolution is XFF-aware but **only trusts X-Forwarded-For when
the direct peer is a configured trusted proxy** — otherwise XFF is
attacker-controlled and would let anyone spoof their IP to dodge the
per-IP limiter. Walks the XFF chain right-to-left, returning the first
hop that isn't a trusted proxy (the real client behind a known proxy
chain).

MAC-based limiting is intentionally absent: MAC is a link-layer (L2)
identifier that never reaches an HTTP server past the first router hop.

Operational note for ``trusted_proxies``: the trusted proxy MUST itself
sanitize/append X-Forwarded-For (e.g. nginx ``$proxy_add_x_forwarded_for``,
which appends the real peer rather than passing client-supplied XFF raw).
If the proxy forwards attacker-controlled XFF verbatim, the rightmost-non-
trusted walk can be fooled. Also keep ``trusted_proxies`` tight — listing
too broad a range lets those peers spoof their client IP.

Single-process only: state lives in this process's memory. Behind multiple
dashboard workers it becomes per-worker (weaker, not unsafe) — use an
external limiter / proxy / WAF for multi-worker or public-scale.
"""

from __future__ import annotations

import logging
import time
from collections import deque

logger = logging.getLogger(__name__)

# Cap on tracked IPs so a distributed attack can't grow the dicts
# unbounded. When exceeded we drop the oldest-seen IP state. 10k distinct
# attacker IPs is already well past where a single-process dashboard is
# the right tool, but this keeps memory bounded regardless.
_MAX_TRACKED_IPS = 10_000


class RateLimiter:
    """In-process global + per-IP rate limiter. Not thread-safe by design —
    intended for a single asyncio event loop (the dashboard process).
    All public methods are synchronous and do no awaits, so coroutine
    interleaving can't tear a check+mutate apart.
    """

    def __init__(
        self,
        *,
        global_rps: float = 20.0,
        burst: float = 40.0,
        auth_fail_max: int = 10,
        auth_fail_window_seconds: float = 300.0,
        auth_fail_cooldown_seconds: float = 900.0,
        trusted_proxies: set[str] | None = None,
    ) -> None:
        self._global_rps = max(0.0, float(global_rps))
        self._burst = max(1.0, float(burst))
        self._tokens = self._burst
        self._last_refill = time.monotonic()
        self._auth_fail_max = max(1, int(auth_fail_max))
        self._auth_fail_window = max(1.0, float(auth_fail_window_seconds))
        self._auth_fail_cooldown = max(1.0, float(auth_fail_cooldown_seconds))
        self._trusted = set(trusted_proxies or set())
        # ip -> deque[failure_monotonic_ts]
        self._failures: dict[str, deque[float]] = {}
        # ip -> locked_until_monotonic_ts
        self._locked_until: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Client IP resolution
    # ------------------------------------------------------------------

    def client_ip(self, *, direct_ip: str, xff_header: str | None) -> str:
        """Resolve the real client IP.

        ``direct_ip`` is the TCP peer (``request.client.host``). When it is a
        trusted proxy, walk ``X-Forwarded-For`` right-to-left and return the
        first hop that isn't itself a trusted proxy. Otherwise XFF is
        untrusted (spoofable) → use the direct peer.
        """
        direct = direct_ip or "unknown"
        if direct not in self._trusted:
            return direct
        if not xff_header:
            return direct
        parts = [p.strip() for p in xff_header.split(",") if p.strip()]
        for ip in reversed(parts):
            if ip not in self._trusted:
                return ip
        return direct

    # ------------------------------------------------------------------
    # Global token bucket
    # ------------------------------------------------------------------

    def allow_global(self) -> bool:
        """Consume one global token. False when the bucket is empty.

        ``global_rps <= 0`` disables the global limiter (always allow).
        """
        if self._global_rps <= 0:
            return True
        now = time.monotonic()
        self._tokens = min(
            self._burst, self._tokens + (now - self._last_refill) * self._global_rps
        )
        self._last_refill = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    # ------------------------------------------------------------------
    # Per-IP auth-failure lockout
    # ------------------------------------------------------------------

    def is_locked(self, ip: str) -> bool:
        """True if this IP is currently in lockout. Clears expired locks."""
        until = self._locked_until.get(ip)
        if until is None:
            return False
        if time.monotonic() >= until:
            del self._locked_until[ip]
            self._failures.pop(ip, None)
            return False
        return True

    def record_auth_failure(self, ip: str) -> bool:
        """Record one auth failure for ``ip``. Returns True if it just locked.

        Failures age out by the rolling window; reaching ``auth_fail_max``
        within the window locks the IP for ``auth_fail_cooldown``.
        """
        now = time.monotonic()
        dq = self._failures.setdefault(ip, deque())
        dq.append(now)
        while dq and now - dq[0] > self._auth_fail_window:
            dq.popleft()
        # Evict AFTER insert (Codex catch: pre-insert check left a 10_001th
        # entry). The just-touched IP has the newest last-failure ts so the
        # least-recently-failed eviction never drops it.
        self._evict_if_needed()
        if len(dq) >= self._auth_fail_max:
            self._locked_until[ip] = now + self._auth_fail_cooldown
            logger.warning(
                "dashboard rate-limit: IP %s locked out for %.0fs after %d "
                "auth failures in %.0fs",
                ip,
                self._auth_fail_cooldown,
                len(dq),
                self._auth_fail_window,
            )
            return True
        return False

    def _evict_if_needed(self) -> None:
        # Drop least-recently-failed IPs until back within the cap.
        while len(self._failures) > _MAX_TRACKED_IPS:
            oldest_ip = min(
                self._failures,
                key=lambda k: self._failures[k][-1] if self._failures[k] else 0.0,
            )
            self._failures.pop(oldest_ip, None)
            self._locked_until.pop(oldest_ip, None)


def build_rate_limiter(cfg: dict | None) -> RateLimiter | None:
    """Construct a RateLimiter from the ``dashboard.rate_limit`` config block.

    Returns None when disabled. Defaults are generous so a loopback / SPA
    polling workload is never throttled; the per-IP lockout only bites
    repeated auth failures.
    """
    cfg = cfg or {}
    if not cfg.get("enabled", True):
        return None
    trusted = cfg.get("trusted_proxies") or []
    return RateLimiter(
        global_rps=float(cfg.get("global_rps", 20.0)),
        burst=float(cfg.get("burst", 40.0)),
        auth_fail_max=int(cfg.get("auth_fail_max", 10)),
        auth_fail_window_seconds=float(cfg.get("auth_fail_window_seconds", 300.0)),
        auth_fail_cooldown_seconds=float(cfg.get("auth_fail_cooldown_seconds", 900.0)),
        trusted_proxies={str(p) for p in trusted},
    )
