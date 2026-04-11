from __future__ import annotations

import asyncio
import sqlite3
import subprocess

from oh_my_agent.utils.errors import (
    USER_MSG_AGENT_CRASH,
    USER_MSG_INTERNAL,
    USER_MSG_STORE_FAILURE,
    USER_MSG_TIMEOUT,
    user_safe_agent_error,
    user_safe_message,
)


def test_user_safe_message_maps_timeout() -> None:
    assert user_safe_message(asyncio.TimeoutError()) == USER_MSG_TIMEOUT


def test_user_safe_message_maps_agent_crash() -> None:
    exc = subprocess.CalledProcessError(returncode=1, cmd=["claude"])
    assert user_safe_message(exc) == USER_MSG_AGENT_CRASH


def test_user_safe_message_maps_storage_failure() -> None:
    assert user_safe_message(sqlite3.OperationalError("db locked")) == USER_MSG_STORE_FAILURE


def test_user_safe_message_maps_generic_exception() -> None:
    assert user_safe_message(RuntimeError("boom")) == USER_MSG_INTERNAL


def test_user_safe_agent_error_maps_timeout() -> None:
    assert user_safe_agent_error("timeout") == USER_MSG_TIMEOUT


def test_user_safe_agent_error_maps_non_timeout_to_agent_crash() -> None:
    assert user_safe_agent_error("cli_error") == USER_MSG_AGENT_CRASH
    assert user_safe_agent_error(None) == USER_MSG_AGENT_CRASH
