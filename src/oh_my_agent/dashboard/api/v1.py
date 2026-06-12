"""v1 read-only JSON API for the dashboard frontend.

Mounted under ``/api/v1/`` by :func:`oh_my_agent.dashboard.app.create_app`.
All endpoints are GET-only — write surfaces (post message, approve task,
edit memory) are out of scope for the read-only session viewer MVP and
will live under ``/api/v2/`` or a separate ``write`` router once the
read path proves out.

Auth: inherits the parent app's bearer-token middleware. ``/api/v1/healthz``
is whitelisted in :mod:`oh_my_agent.dashboard.app` so liveness probes
don't need the token.

Data flow:
- session list / history → :mod:`oh_my_agent.dashboard.data_sessions`
  (read-only SQLite ``mode=ro`` connection on memory.db)
- tool trace → :mod:`oh_my_agent.trace.trace_reader` (per-day JSONL scan
  with ``thread_id`` filter; strictly day-bounded by required ``date``
  query param)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from oh_my_agent import paths
from oh_my_agent.dashboard import data, data_sessions
from oh_my_agent.trace import trace_reader

# The only ``weeks`` values the dashboard offers / supports. Kept in sync
# with the WINDOWS presets in dashboard-web/src/app/trends/index.tsx.
_TREND_WEEK_PRESETS = (1, 2, 4, 12)


def build_router(config: dict) -> APIRouter:
    """Return an APIRouter bound to the given top-level oh-my-agent config.

    Path resolution is done per-request via :mod:`oh_my_agent.paths` so
    config-time path overrides (e.g. ``memory.db_path``) are respected.
    """

    router = APIRouter()

    def _memory_db_path() -> Path:
        return paths.memory_db_path(config)

    def _runtime_db_path() -> Path:
        # runtime_tasks / usage_events live in the runtime state DB,
        # NOT memory.db (which only holds conversation `turns`).
        return paths.runtime_state_path(config)

    def _trace_dir() -> Path | None:
        """Resolve the experiment.tool_trace path from config.

        Returns ``None`` when tool_trace is disabled / unconfigured —
        callers translate that into an empty trace response so the
        frontend can render "no tool calls".
        """

        exp = config.get("experiment", {}) or {}
        trace_cfg = exp.get("tool_trace", {}) or {}
        if not trace_cfg.get("enabled", False):
            return None
        trace_path = trace_cfg.get("path")
        if trace_path:
            return Path(str(trace_path)).expanduser().resolve()
        # Fall back to the runtime-root convention used by boot.py
        # ("traces/" sibling of memory.db) when no explicit path is set.
        return paths.runtime_root(config) / "traces"

    @router.get("/healthz")
    def healthz() -> JSONResponse:
        # Whitelisted in dashboard/app.py auth middleware so probes
        # don't need the token. Symmetric with the top-level /healthz.
        return JSONResponse({"status": "ok"})

    @router.get("/sessions")
    def list_sessions(
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = Query(default=None),
    ) -> dict[str, Any]:
        result = data_sessions.fetch_session_list(
            _memory_db_path(),
            limit=limit,
            cursor=cursor,
        )
        if "error" in result:
            # 503 (not 500) — the most common error here is a missing
            # memory.db on first boot. Tells the operator "service is
            # up but DB isn't there yet" rather than "code crashed".
            raise HTTPException(status_code=503, detail=result["error"])
        return result

    @router.get("/sessions/search")
    def search_sessions(
        q: str = Query(..., min_length=1, description="Full-text query over turns"),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        # Distinct path from /sessions/{platform}/{channel_id}/{thread_id}/...
        # (one segment vs four) so there's no route ambiguity.
        result = data_sessions.search_sessions(
            _memory_db_path(),
            query=q,
            limit=limit,
        )
        if "error" in result:
            raise HTTPException(status_code=503, detail=result["error"])
        return result

    @router.get("/trends")
    def get_trends(
        weeks: int = Query(
            default=4,
            description=(
                "Trailing window; one of 1/2/4/12 weeks "
                "(daily buckets = weeks * 7, UTC calendar days)"
            ),
        ),
    ) -> dict[str, Any]:
        # Allowlist, not a 1..12 range: the contract is exactly the four
        # presets the UI offers. An in-range-but-unsupported value (e.g.
        # weeks=3) is a client bug, so reject it loudly rather than serve
        # a window nothing was designed around.
        if weeks not in _TREND_WEEK_PRESETS:
            raise HTTPException(
                status_code=400,
                detail=f"weeks must be one of {list(_TREND_WEEK_PRESETS)}",
            )
        # usage_events/runtime_tasks ← runtime.db; turns ← memory.db.
        # fetch_trends is fully resilient (degrades per-signal), so there
        # is no error branch to translate into a 503.
        result = data.fetch_trends(
            _runtime_db_path(), _memory_db_path(), days=weeks * 7
        )
        result["weeks"] = weeks
        return result

    @router.get("/sessions/{platform}/{channel_id}/{thread_id}/history")
    def get_history(
        platform: str,
        channel_id: str,
        thread_id: str,
        limit: int = Query(default=200, ge=1, le=500),
        before_id: int | None = Query(default=None, ge=1),
    ) -> list[dict[str, Any]]:
        result = data_sessions.fetch_session_history(
            _memory_db_path(),
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
            limit=limit,
            before_id=before_id,
        )
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=503, detail=result["error"])
        return result  # type: ignore[return-value]

    @router.get("/sessions/{platform}/{channel_id}/{thread_id}/trace")
    def get_trace(
        platform: str,
        channel_id: str,
        thread_id: str,
        date: str = Query(..., description="YYYY-MM-DD; required, no scan-all fallback"),
        limit: int = Query(default=500, ge=1, le=2000),
    ) -> dict[str, Any]:
        # platform / channel_id are unused for trace lookup today (the
        # writer keys lines by thread_id alone), but they're in the
        # URL for path-shape symmetry with /history. Future schema
        # changes can start using them without breaking URLs.
        del platform, channel_id

        trace_dir = _trace_dir()
        if trace_dir is None:
            # experiment.tool_trace disabled. Return empty trace so the
            # frontend can show "tool trace disabled" rather than 404.
            return {
                "items": [],
                "date": date,
                "thread_id": thread_id,
                "enabled": False,
            }

        result = trace_reader.read_thread_trace(
            trace_dir,
            thread_id=thread_id,
            date=date,
            limit=limit,
        )
        if "error" in result:
            # Validation errors (bad date format, empty thread_id) → 400.
            # IO errors → 503. Cheap heuristic on the error message.
            msg = result["error"]
            if msg.startswith("invalid date") or msg == "thread_id is required":
                raise HTTPException(status_code=400, detail=msg)
            raise HTTPException(status_code=503, detail=msg)
        result["enabled"] = True
        return result

    # ------------------------------------------------------------------
    # M2 PR3 — Automation control (write surface, colocated mode only)
    # ------------------------------------------------------------------

    def _require_colocated(request: Request):
        """Pull the live DashboardContext; 503 unless co-located."""
        ctx = getattr(request.app.state, "oma", None)
        if ctx is None or ctx.mode != "colocated":
            raise HTTPException(
                status_code=503,
                detail="automation control requires the co-located dashboard "
                "(runs inside the bot process)",
            )
        return ctx

    def _require_write(request: Request):
        """Colocated + bearer-header auth for mutating routes (M2 PR1 deps)."""
        from oh_my_agent.dashboard.app import require_write_auth

        return require_write_auth(request)

    # ------------------------------------------------------------------
    # M2 PR4 — Skill health + manual enable/disable
    # ------------------------------------------------------------------

    @router.get("/skills/health")
    async def skills_health(request: Request) -> dict[str, Any]:
        # Legacy: only lists skills that have actually run as tasks. New
        # frontend code should use ``/skills`` (below) which also surfaces
        # installed-but-never-run skills. Kept for back-compat with any
        # external callers or tests.
        disabled: set[str] = set()
        ctx = getattr(request.app.state, "oma", None)
        if ctx is not None and ctx.mode == "colocated" and ctx.store is not None:
            try:
                auto = await ctx.store.list_auto_disabled_skills()
                manual = await ctx.store.list_manual_disabled_skills()
                disabled = auto | manual
            except Exception:
                disabled = set()
        # to_thread: this handler must be async (it awaits the store), but
        # fetch_skill_health does real SQLite + YAML IO. In colocated mode
        # this coroutine runs on the bot's event loop and the SPA polls it
        # every 5s — offload so the loop never blocks on disk.
        items = await asyncio.to_thread(
            data.fetch_skill_health,
            _runtime_db_path(),
            memories_yaml=paths.judge_memories_yaml_path(config),
            disabled_skills=disabled,
        )
        return {"items": items}

    @router.get("/skills")
    async def skills_overview(request: Request) -> dict[str, Any]:
        """All-skills overview: installed skills (read from disk) merged
        with runtime stats, disabled-kind split, and frontmatter snippets.

        Works in both readonly and colocated mode — readonly skips the
        live disabled-skill set (no store handle), so the manual/auto
        labels only appear in colocated mode.
        """
        ctx = getattr(request.app.state, "oma", None)
        disabled_manual: set[str] = set()
        disabled_auto: set[str] = set()
        disabled_lookup_error: str | None = None
        if ctx is not None and ctx.mode == "colocated" and ctx.store is not None:
            try:
                disabled_manual = await ctx.store.list_manual_disabled_skills()
                disabled_auto = await ctx.store.list_auto_disabled_skills()
            except Exception as exc:  # noqa: BLE001 — surface as a warning
                # Don't silently lie about operational state — render every
                # row as if it were active when we couldn't actually check
                # would make the UI claim health it doesn't have (Codex
                # review #3). Empty the sets so we don't half-attribute,
                # and bubble up a banner so the operator knows the labels
                # are unreliable until the store recovers.
                disabled_manual = set()
                disabled_auto = set()
                disabled_lookup_error = (
                    f"disabled-skill state unavailable: {exc}; "
                    "manual/auto labels in this view may be stale"
                )
        project_root = getattr(ctx, "project_root", None) if ctx is not None else None
        sdir = paths.skills_dir(config, project_root=project_root)
        # Same loop-safety rationale as skills_health above: disk walk of
        # skills_dir + SQLite + YAML must not run inline on the event loop.
        result = await asyncio.to_thread(
            data.fetch_skills_overview,
            _runtime_db_path(),
            skills_dir=sdir,
            memories_yaml=paths.judge_memories_yaml_path(config),
            disabled_manual=disabled_manual,
            disabled_auto=disabled_auto,
        )
        if disabled_lookup_error:
            result.setdefault("warnings", []).append(disabled_lookup_error)
        return result

    @router.get("/skills/{name}/recent_tasks")
    def skill_recent_tasks(
        name: str,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        rows = data.fetch_skill_recent_tasks(_runtime_db_path(), skill=name, limit=limit)
        return {"skill": name, "items": rows}

    @router.post("/skills/{name}/enable")
    async def skill_enable(name: str, request: Request) -> dict[str, Any]:
        return await _set_skill_enabled(request, name, enabled=True)

    @router.post("/skills/{name}/disable")
    async def skill_disable(name: str, request: Request) -> dict[str, Any]:
        return await _set_skill_enabled(request, name, enabled=False)

    async def _set_skill_enabled(request: Request, name: str, *, enabled: bool) -> dict[str, Any]:
        _require_write(request)
        ctx = _require_colocated(request)
        if ctx.store is None:
            raise HTTPException(status_code=503, detail="store not available")
        # The gateway treats a skill as blocked if it appears in EITHER the
        # manual override set OR the auto-disabled set (manager.py
        # _is_skill_auto_disabled uses OR). So when the operator asks to
        # "enable", clearing the manual override alone leaves the gateway
        # still blocking on the auto-disable bit — the dashboard would lie
        # ("enabled") while routing keeps refusing. Match /skill_enable's
        # semantics: enable = clear BOTH; disable = manual override only
        # (don't fight auto-disable, that's the bot's call).
        await ctx.store.set_skill_override(name, enabled=enabled)
        if enabled and hasattr(ctx.store, "set_skill_auto_disabled"):
            try:
                await ctx.store.set_skill_auto_disabled(name, disabled=False)
            except Exception:
                # Best-effort: if auto-disable clear fails the manual
                # override still landed; the gateway refresh below will
                # surface the residual auto-disable on the next poll.
                pass
        # Best-effort: nudge the gateway to refresh its disabled-skill cache
        # so the change takes effect without a restart.
        gw = getattr(ctx, "gateway", None)
        if gw is not None and hasattr(gw, "refresh_disabled_skills"):
            try:
                await gw.refresh_disabled_skills()
            except Exception:
                pass
        return {"skill": name, "enabled": enabled}

    @router.get("/automations")
    def list_automations(request: Request) -> dict[str, Any]:
        """Two modes:

        - **live**: colocated dashboard with a running scheduler. Returns
          records *and* computed ``next_run_at`` per job.
        - **static**: standalone dashboard (no scheduler handle). Reads
          ``automations.storage_dir`` YAMLs directly so the operator can
          still see what's scheduled. ``next_run_at`` is null because
          firing-state lives only in the scheduler. Write surfaces
          (``/fire``, PATCH) still 503 in this mode — that's enforced by
          ``_require_colocated`` on those routes.
        """
        ctx = getattr(request.app.state, "oma", None)
        scheduler = getattr(ctx, "scheduler", None) if ctx is not None else None
        if scheduler is None:
            project_root = getattr(ctx, "project_root", None) if ctx is not None else None
            return data.fetch_automations_static(
                paths.automations_storage_dir(config, project_root=project_root)
            )

        records = scheduler.list_automations()
        next_runs = scheduler.compute_all_next_run_at()
        items = []
        for rec in records:
            next_at = next_runs.get(rec.name)
            items.append(
                {
                    "name": rec.name,
                    "enabled": rec.enabled,
                    "schedule_kind": rec.schedule_kind,
                    "cron": rec.cron,
                    "interval_seconds": rec.interval_seconds,
                    "agent": rec.agent,
                    "skill_name": rec.skill_name,
                    "platform": rec.platform,
                    "channel_id": rec.channel_id,
                    "next_run_at": next_at.isoformat() if next_at else None,
                }
            )
        return {"items": items, "warnings": [], "mode": "live"}

    @router.post("/automations/{name}/fire")
    async def fire_automation(name: str, request: Request) -> dict[str, Any]:
        _require_write(request)
        ctx = _require_colocated(request)
        scheduler = ctx.scheduler
        if scheduler is None:
            raise HTTPException(status_code=503, detail="scheduler not available")
        result = await scheduler.fire_job_now(name)
        if result == "not_found":
            raise HTTPException(status_code=404, detail=f"automation {name!r} not found")
        if result == "scheduler_down":
            raise HTTPException(status_code=503, detail="scheduler not running")
        if result == "already_firing":
            # Codex M2 PR3: 409 Conflict — the job is mid-run, the manual
            # fire was refused (not a success).
            raise HTTPException(
                status_code=409,
                detail=f"automation {name!r} is already firing",
            )
        return {"name": name, "result": result}

    @router.patch("/automations/{name}")
    async def patch_automation(
        name: str,
        request: Request,
        body: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        _require_write(request)
        ctx = _require_colocated(request)
        scheduler = ctx.scheduler
        if scheduler is None:
            raise HTTPException(status_code=503, detail="scheduler not available")
        allowed = {"enabled", "cron", "interval_seconds"}
        # Codex M2 PR3 fix: reject ANY disallowed key explicitly. Previously
        # disallowed keys were silently filtered out, so a body like
        # {"enabled": false, "prompt": "x"} succeeded — bypassing the
        # whitelist contract entirely.
        bad = set(body) - allowed
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"disallowed keys {sorted(bad)} (allowed: {sorted(allowed)})",
            )
        updates = {k: v for k, v in body.items() if k in allowed}
        if not updates:
            raise HTTPException(
                status_code=400,
                detail=f"no patchable keys in body (allowed: {sorted(allowed)})",
            )
        try:
            rec = await scheduler.patch_automation(name, updates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "name": rec.name,
            "enabled": rec.enabled,
            "cron": rec.cron,
            "interval_seconds": rec.interval_seconds,
        }

    return router
