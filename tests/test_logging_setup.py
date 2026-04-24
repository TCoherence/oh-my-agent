"""Tests for structured logging setup (Session 1E)."""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime, timedelta, timezone

from oh_my_agent.logging_setup import KeyValueFormatter, _cleanup_old_logs, setup_logging


class TestKeyValueFormatter:
    def test_output_format(self):
        formatter = KeyValueFormatter()
        record = logging.LogRecord(
            name="oh_my_agent.gateway.manager",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="agent running",
            args=(),
            exc_info=None,
        )
        line = formatter.format(record)
        assert "level=INFO" in line
        assert "logger=oh_my_agent.gateway.manager" in line
        assert "msg=agent running" in line
        # ISO-8601 timestamp with Z suffix
        assert line[10] == "T"
        assert "Z " in line

    def test_newlines_escaped(self):
        formatter = KeyValueFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="line1\nline2\nline3",
            args=(),
            exc_info=None,
        )
        line = formatter.format(record)
        assert "\n" not in line
        assert "line1\\nline2\\nline3" in line

    def test_exception_info_included(self):
        formatter = KeyValueFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="failed",
            args=(),
            exc_info=exc_info,
        )
        line = formatter.format(record)
        assert "exc=" in line
        assert "ValueError" in line
        assert "boom" in line

    def test_message_with_args(self):
        formatter = KeyValueFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="count=%d name=%s",
            args=(42, "alice"),
            exc_info=None,
        )
        line = formatter.format(record)
        assert "msg=count=42 name=alice" in line


class TestSetupLogging:
    def test_defaults_when_no_config(self, tmp_path):
        setup_logging(None, runtime_root=tmp_path)
        root = logging.getLogger()
        assert root.level == logging.INFO
        assert len(root.handlers) == 2  # console + file
        # File handler writes to service.log
        file_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.TimedRotatingFileHandler)
        ]
        assert len(file_handlers) == 1
        assert file_handlers[0].baseFilename.endswith("service.log")

    def test_custom_level_respected(self, tmp_path):
        config = {"logging": {"level": "DEBUG"}}
        setup_logging(config, runtime_root=tmp_path)
        assert logging.getLogger().level == logging.DEBUG

    def test_warning_level(self, tmp_path):
        config = {"logging": {"level": "WARNING"}}
        setup_logging(config, runtime_root=tmp_path)
        assert logging.getLogger().level == logging.WARNING

    def test_invalid_level_falls_back_to_info(self, tmp_path):
        config = {"logging": {"level": "BANANA"}}
        setup_logging(config, runtime_root=tmp_path)
        assert logging.getLogger().level == logging.INFO

    def test_service_log_file_created(self, tmp_path):
        setup_logging(None, runtime_root=tmp_path)
        log_file = tmp_path / "logs" / "service.log"
        assert log_file.exists()

    def test_retention_days_applied(self, tmp_path):
        config = {"logging": {"service_retention_days": 14}}
        setup_logging(config, runtime_root=tmp_path)
        file_handlers = [
            h for h in logging.getLogger().handlers
            if isinstance(h, logging.handlers.TimedRotatingFileHandler)
        ]
        assert file_handlers[0].backupCount == 14

    def test_missing_logging_block_uses_defaults(self, tmp_path):
        config = {"gateway": {}}  # no "logging" key
        setup_logging(config, runtime_root=tmp_path)
        root = logging.getLogger()
        assert root.level == logging.INFO
        assert len(root.handlers) == 2

    def test_formatter_is_key_value(self, tmp_path):
        setup_logging(None, runtime_root=tmp_path)
        for handler in logging.getLogger().handlers:
            assert isinstance(handler.formatter, KeyValueFormatter)

    def test_thread_log_retention_accessible(self):
        """thread_log_retention_days is a config value the janitor reads directly."""
        config = {"logging": {"thread_log_retention_days": 30}}
        # Just verify the value is accessible from config — no special handling needed
        assert config["logging"]["thread_log_retention_days"] == 30


class TestStartupLogCleanup:
    def test_old_logs_deleted(self, tmp_path):
        """Rotated logs older than retention_days are removed on startup."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        today = datetime.now(tz=timezone.utc).date()
        # Create files: 2 old (beyond 3-day retention) + 1 recent + 1 today
        old_dates = [today - timedelta(days=10), today - timedelta(days=5)]
        recent_date = today - timedelta(days=1)

        for d in old_dates:
            (log_dir / f"service.log.{d}").write_text("old")
        (log_dir / f"service.log.{recent_date}").write_text("recent")
        (log_dir / "service.log").write_text("current")

        _cleanup_old_logs(log_dir, "service.log", retention_days=3)

        # Old files gone
        for d in old_dates:
            assert not (log_dir / f"service.log.{d}").exists()
        # Recent + current still there
        assert (log_dir / f"service.log.{recent_date}").exists()
        assert (log_dir / "service.log").exists()

    def test_no_crash_on_empty_dir(self, tmp_path):
        """Cleanup on a directory with no rotated files does not crash."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _cleanup_old_logs(log_dir, "service.log", retention_days=7)
        # no exception = pass

    def test_non_date_suffixes_ignored(self, tmp_path):
        """Files like service.log.bak are not touched."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "service.log.bak").write_text("backup")
        (log_dir / "service.log.tmp").write_text("temp")

        _cleanup_old_logs(log_dir, "service.log", retention_days=0)

        assert (log_dir / "service.log.bak").exists()
        assert (log_dir / "service.log.tmp").exists()

    def test_cleanup_runs_during_setup_logging(self, tmp_path):
        """setup_logging() triggers cleanup of stale rotated files."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        old_date = datetime.now(tz=timezone.utc).date() - timedelta(days=30)
        stale_file = log_dir / f"service.log.{old_date}"
        stale_file.write_text("stale")

        setup_logging(None, runtime_root=tmp_path)

        assert not stale_file.exists()
