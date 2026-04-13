from __future__ import annotations

import asyncio
import sqlite3
import subprocess

USER_MSG_TIMEOUT = "The agent timed out. Try again or use a simpler prompt."
USER_MSG_MAX_TURNS = "The agent reached its max turn budget. Narrow the task or raise max_turns."
USER_MSG_AGENT_CRASH = "The agent encountered an error. The issue has been logged."
USER_MSG_STORE_FAILURE = "A storage error occurred. Your message was received but may not be persisted."
USER_MSG_INTERNAL = "An internal error occurred. Details have been logged for debugging."


def user_safe_message(exc: Exception, *, context: str = "") -> str:
    """Map an exception to a concise user-facing message."""
    del context
    if isinstance(exc, asyncio.TimeoutError):
        return USER_MSG_TIMEOUT
    if isinstance(exc, subprocess.CalledProcessError):
        return USER_MSG_AGENT_CRASH
    if isinstance(exc, sqlite3.Error):
        return USER_MSG_STORE_FAILURE
    return USER_MSG_INTERNAL


def user_safe_agent_error(error_kind: str | None) -> str:
    """Map an agent response error kind to a user-facing message."""
    if error_kind == "timeout":
        return USER_MSG_TIMEOUT
    if error_kind == "max_turns":
        return USER_MSG_MAX_TURNS
    return USER_MSG_AGENT_CRASH
