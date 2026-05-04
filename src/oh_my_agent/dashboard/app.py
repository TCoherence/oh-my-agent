"""FastAPI app exposing the dashboard.

Two routes:

- ``GET /`` — full HTML dashboard (Jinja2-rendered)
- ``GET /healthz`` — JSON health check (always 200 if process is up)

The ``data.py`` layer is invoked synchronously per request — query each
SQLite table on demand. SQLite reads with ``mode=ro`` URI are concurrent-safe
under WAL, so this stays correct while the bot writes.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, PackageLoader, select_autoescape

from oh_my_agent import paths

from . import data as dashboard_data


def create_app(config: dict, *, refresh_seconds: int = 300) -> FastAPI:
    """Build a FastAPI app bound to the given top-level oh-my-agent config.

    Args:
        config: top-level oh-my-agent config dict.
        refresh_seconds: page auto-refresh interval (default 300 = 5 min).
            Set to 0 to omit the meta-refresh tag entirely.

    The app keeps a reference to the config dict and resolves all paths fresh
    on each request (cheap — just dict lookups + string ops). No caching.
    """

    refresh_seconds = max(0, int(refresh_seconds))

    app = FastAPI(title="oh-my-agent dashboard", docs_url=None, redoc_url=None)
    env = Environment(
        loader=PackageLoader("oh_my_agent.dashboard", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["fmt_bytes"] = _fmt_bytes
    env.filters["fmt_relative"] = _fmt_relative
    env.filters["fmt_pct"] = _fmt_pct
    env.filters["fmt_uptime"] = _fmt_uptime
    env.filters["short_day"] = _short_day
    env.globals["chart"] = _chart_svg
    env.globals["sparkline"] = _sparkline_svg  # legacy alias, kept for any caller

    template = env.get_template("dashboard.html")

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/")
    def index() -> HTMLResponse:
        ctx = _build_context(config)
        ctx["refresh_seconds"] = refresh_seconds
        return HTMLResponse(template.render(**ctx))

    return app


def _build_context(config: dict) -> dict:
    """Resolve all data sections + path metadata for the template."""

    db_path = paths.runtime_state_path(config)
    service_log = paths.runtime_service_log_path(config)
    oma_log = paths.runtime_oma_log_path(config)
    memories_yaml = paths.judge_memories_yaml_path(config)

    runtime_root = paths.runtime_root(config)
    disk_targets = [
        runtime_root / "logs",
        paths.runtime_worktree_root(config),
        db_path,
        paths.memory_db_path(config),
        memories_yaml,
    ]
    reports_dir = paths.runtime_reports_dir(config)
    if reports_dir is not None:
        disk_targets.append(reports_dir)

    return {
        "now_utc": datetime.now(timezone.utc).isoformat(),
        "automation": dashboard_data.fetch_automation_health(db_path),
        "task": dashboard_data.fetch_task_health(db_path),
        "cost": dashboard_data.fetch_cost_usage(db_path),
        "memory": dashboard_data.fetch_memory_summary(memories_yaml),
        "skill": dashboard_data.fetch_skill_stats(db_path),
        "log": dashboard_data.fetch_log_health([service_log, oma_log]),
        "disk": dashboard_data.fetch_disk_usage(disk_targets),
        "uptime": dashboard_data.fetch_bot_uptime(service_log),
        "paths_block": {
            "runtime_root": str(runtime_root),
            "service_log": str(service_log),
            "oma_log": str(oma_log),
            "memories_yaml": str(memories_yaml),
        },
    }


# ---------------------------------------------------------------------------
# Jinja2 filters / globals
# ---------------------------------------------------------------------------


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024 / 1024 / 1024:.2f} GB"


def _fmt_relative(ts_str: str | None) -> str:
    """Render an ISO timestamp as relative-from-now ("5m ago" / "2h ago")."""

    if not ts_str:
        return "never"
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return ts_str
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.0f}%"


def _fmt_uptime(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    return f"{days}d {hours}h"


def _short_day(s: str | None) -> str:
    """Shorten ``YYYY-MM-DD`` to ``MM-DD``; pass through anything else."""

    if s and len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[5:10]
    return s or ""


def _format_y_axis_label(value: float) -> str:
    """Compact dollar formatter for chart y-axis ticks.

    The cost values we plot range from ~$0.0001 (chat exchanges) to ~$10
    (heavy automation days), so we need adaptive precision.
    """

    if value == 0:
        return "$0"
    abs_val = abs(value)
    if abs_val >= 1000:
        return f"${value / 1000:.1f}k"
    if abs_val >= 10:
        return f"${value:.0f}"
    if abs_val >= 1:
        return f"${value:.2f}"
    if abs_val >= 0.01:
        return f"${value:.3f}"
    return f"${value:.4f}"


def _chart_svg(
    values: list[float],
    labels: list[str] | None = None,
    width: int = 480,
    height: int = 140,
) -> str:
    """Render a labeled line chart with x/y axes, ticks, and grid.

    More substantial than ``_sparkline_svg`` — designed to live as a block
    element with breathing room. Fills the y-axis with min/mid/max ticks
    and the x-axis with one tick per data point. Empty / single-value
    inputs render a labeled-empty placeholder.
    """

    if not values:
        return f'<svg width="{width}" height="{height}"></svg>'

    left, top, right, bottom = 52, 10, 12, 26
    plot_w = width - left - right
    plot_h = height - top - bottom

    vmin = min(values)
    vmax = max(values)
    if vmax == vmin:
        # Pad a tiny range so a flat series still renders on the polyline,
        # not on the axis itself. Use 10% of the value when non-zero, or
        # a small absolute pad ($0.01) when everything is zero — Codex
        # round-1 caught that the previous `max(1.0, ...)` pushed the
        # all-zero case to a misleading $0/$0.50/$1.00 y-axis.
        if vmin == 0:
            vmax = 0.01
        else:
            vmax = vmin + abs(vmin) * 0.1
    span = vmax - vmin

    n = len(values)

    def x_for(i: int) -> float:
        if n == 1:
            return left + plot_w / 2
        return left + (i / (n - 1)) * plot_w

    def y_for(v: float) -> float:
        return top + plot_h - ((v - vmin) / span) * plot_h

    parts: list[str] = []
    parts.append(
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        'xmlns="http://www.w3.org/2000/svg" style="font: 10px ui-monospace, '
        'SFMono-Regular, Menlo, Consolas, monospace;">'
    )

    # Horizontal grid + y-axis ticks at min/mid/max.
    y_ticks = [
        (vmin, top + plot_h),
        ((vmin + vmax) / 2, top + plot_h / 2),
        (vmax, top),
    ]
    for _val, y in y_ticks:
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
            'stroke="#3a3a3a" stroke-width="0.5" stroke-dasharray="2,3"/>'
        )
    for val, y in y_ticks:
        parts.append(
            f'<line x1="{left - 3}" y1="{y:.1f}" x2="{left}" y2="{y:.1f}" '
            'stroke="#888" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{left - 5}" y="{y + 3:.1f}" text-anchor="end" '
            f'fill="#888">{_format_y_axis_label(val)}</text>'
        )

    # Axis lines.
    parts.append(
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" '
        'stroke="#888" stroke-width="0.5"/>'
    )
    parts.append(
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" '
        f'y2="{top + plot_h}" stroke="#888" stroke-width="0.5"/>'
    )

    # X-axis ticks + labels (one per data point, if labels provided).
    if labels and len(labels) == n:
        for i, lab in enumerate(labels):
            x = x_for(i)
            parts.append(
                f'<line x1="{x:.1f}" y1="{top + plot_h}" x2="{x:.1f}" '
                f'y2="{top + plot_h + 3}" stroke="#888" stroke-width="0.5"/>'
            )
            # Escape: chart() is a Jinja global, future callers may pass
            # user-controlled strings. Cheap defense regardless of who calls.
            safe_lab = html.escape(str(lab), quote=False)
            parts.append(
                f'<text x="{x:.1f}" y="{top + plot_h + 14:.1f}" '
                f'text-anchor="middle" fill="#888">{safe_lab}</text>'
            )

    # Data: polyline + dot markers.
    if n >= 2:
        points = " ".join(f"{x_for(i):.1f},{y_for(v):.1f}" for i, v in enumerate(values))
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="currentColor" '
            'stroke-width="1.5"/>'
        )
    for i, v in enumerate(values):
        parts.append(
            f'<circle cx="{x_for(i):.1f}" cy="{y_for(v):.1f}" r="2.5" '
            'fill="currentColor"/>'
        )

    parts.append("</svg>")
    return "".join(parts)


def _sparkline_svg(values: list[float], width: int = 120, height: int = 24) -> str:
    """Render a list of numbers as an inline SVG polyline.

    Legacy minimal renderer. Kept as a Jinja global for any future caller
    that wants tiny inline trends; current dashboard uses ``chart`` instead
    (axes + tick labels). Empty / single-value lists render an empty SVG.
    """

    if not values or len(values) < 2:
        return f'<svg width="{width}" height="{height}"></svg>'
    vmin = min(values)
    vmax = max(values)
    span = vmax - vmin or 1.0
    points: list[str] = []
    n = len(values)
    for i, v in enumerate(values):
        x = (i / (n - 1)) * (width - 2) + 1
        y = height - 2 - ((v - vmin) / span) * (height - 4)
        points.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        'xmlns="http://www.w3.org/2000/svg" style="vertical-align: middle;">'
        f'<polyline points="{" ".join(points)}" fill="none" stroke="currentColor" '
        'stroke-width="1.5"/></svg>'
    )


__all__ = ["create_app"]
