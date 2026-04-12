"""Structured logging setup for oh-my-agent.

Provides ``KeyValueFormatter`` (structured ``key=value`` log lines) and
``setup_logging()`` to configure the root logger from the application
config.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_DEFAULT_LEVEL = "INFO"
_DEFAULT_RETENTION_DAYS = 7


class KeyValueFormatter(logging.Formatter):
    """Emit log records as structured ``key=value`` lines.

    Output format::

        2026-04-10T10:20:11.123Z level=INFO logger=oh_my_agent.gateway.manager msg=agent running
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"  # truncate microseconds → milliseconds

        msg = record.getMessage()
        # Keep each log record on a single line
        msg = msg.replace("\n", "\\n")

        parts = [
            ts,
            f"level={record.levelname}",
            f"logger={record.name}",
            f"msg={msg}",
        ]
        line = " ".join(parts)

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line += " exc=" + record.exc_text.replace("\n", "\\n")
        return line


def setup_logging(
    config: dict[str, Any] | None = None,
    *,
    runtime_root: Path | None = None,
) -> None:
    """Configure the root logger based on the ``logging`` config block.

    Parameters
    ----------
    config:
        Full application config dict.  Only the ``logging`` section is
        read.  Pass ``None`` (or omit) to use sensible defaults.
    runtime_root:
        Override for the runtime directory.  The file handler writes to
        ``<runtime_root>/logs/service.log``.  Defaults to
        ``~/.oh-my-agent/runtime``.
    """
    logging_cfg: dict = (config or {}).get("logging", {}) or {}

    # ── level ───────────────────────────────────────────────────────
    level_name = str(logging_cfg.get("level", _DEFAULT_LEVEL)).upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO

    # ── retention ───────────────────────────────────────────────────
    try:
        retention_days = int(logging_cfg.get("service_retention_days", _DEFAULT_RETENTION_DAYS))
    except (TypeError, ValueError):
        retention_days = _DEFAULT_RETENTION_DAYS

    # ── root logger ─────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    formatter = KeyValueFormatter()

    # Console handler — always present
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Rotating file handler — one file per day
    runtime_root = runtime_root or Path("~/.oh-my-agent/runtime").expanduser().resolve()
    log_dir = runtime_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / "service.log",
        when="midnight",
        backupCount=retention_days,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Startup cleanup: delete rotated log files older than retention period.
    # TimedRotatingFileHandler only rotates while running — if the process
    # restarts before midnight, stale files accumulate.  Clean them eagerly.
    _cleanup_old_logs(log_dir, "service.log", retention_days)


# ── Startup log cleanup ──────────────────────────────────────────────── #

# Matches the date suffix that TimedRotatingFileHandler appends,
# e.g.  service.log.2026-04-10
_DATE_SUFFIX_RE = re.compile(r"\.(\d{4}-\d{2}-\d{2})$")


def _cleanup_old_logs(
    log_dir: Path,
    base_name: str,
    retention_days: int,
) -> None:
    """Remove rotated log files older than *retention_days*."""
    cutoff = datetime.now(tz=timezone.utc).date() - timedelta(days=retention_days)
    removed: list[str] = []

    for path in sorted(log_dir.glob(f"{base_name}.*")):
        m = _DATE_SUFFIX_RE.search(path.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            path.unlink(missing_ok=True)
            removed.append(path.name)

    if removed:
        logger = logging.getLogger(__name__)
        logger.info(
            "Startup log cleanup: removed %d old log file(s): %s",
            len(removed),
            ", ".join(removed),
        )
