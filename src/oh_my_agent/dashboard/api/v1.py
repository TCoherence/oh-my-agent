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

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from oh_my_agent import paths
from oh_my_agent.dashboard import data_sessions
from oh_my_agent.trace import trace_reader


def build_router(config: dict) -> APIRouter:
    """Return an APIRouter bound to the given top-level oh-my-agent config.

    Path resolution is done per-request via :mod:`oh_my_agent.paths` so
    config-time path overrides (e.g. ``memory.db_path``) are respected.
    """

    router = APIRouter()

    def _memory_db_path() -> Path:
        return paths.memory_db_path(config)

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

    return router
