"""Tests for GeminiCLIAgent session resume."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from oh_my_agent.agents.cli.gemini import GeminiCLIAgent


def _agent() -> GeminiCLIAgent:
    return GeminiCLIAgent(cli_path="gemini", model="gemini-test")


# ---------------------------------------------------------------------------
# Session ID management
# ---------------------------------------------------------------------------

def test_gemini_session_id_initially_none():
    agent = _agent()
    assert agent.get_session_id("t1") is None


def test_gemini_set_and_get_session_id():
    agent = _agent()
    agent.set_session_id("t1", "sess-abc")
    assert agent.get_session_id("t1") == "sess-abc"


def test_gemini_clear_session():
    agent = _agent()
    agent.set_session_id("t1", "sess-abc")
    agent.clear_session("t1")
    assert agent.get_session_id("t1") is None


def test_gemini_clear_session_missing_is_noop():
    agent = _agent()
    agent.clear_session("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------

def test_gemini_fresh_command_has_output_format_json():
    agent = _agent()
    cmd = agent._build_command("hello")
    assert "--output-format" in cmd
    assert "json" in cmd


def test_gemini_resume_command_includes_resume_flag():
    agent = _agent()
    cmd = agent._build_resume_command("hello", "sess-xyz")
    assert "--resume" in cmd
    assert "sess-xyz" in cmd


def test_gemini_resume_command_has_output_format_json():
    agent = _agent()
    cmd = agent._build_resume_command("hello", "sess-xyz")
    assert "--output-format" in cmd
    assert "json" in cmd


def test_gemini_resume_command_includes_yolo():
    agent = _agent()
    cmd = agent._build_resume_command("hello", "sess-xyz")
    assert "--yolo" in cmd


# ---------------------------------------------------------------------------
# Output parsing: _parse_output
# ---------------------------------------------------------------------------

def test_gemini_parse_output_extracts_response_and_session_id():
    agent = _agent()
    data = {
        "session_id": "sess-123",
        "response": "Hello world",
        "stats": {"models": {"gemini-flash": {"tokens": {"prompt": 10, "candidates": 5, "cached": 0}}}},
    }
    resp = agent._parse_output(json.dumps(data))
    assert resp.text == "Hello world"
    assert resp.usage["input_tokens"] == 10
    assert resp.usage["output_tokens"] == 5


def test_gemini_parse_output_falls_back_on_invalid_json():
    agent = _agent()
    resp = agent._parse_output("plain text response")
    assert resp.text == "plain text response"


def test_gemini_parse_output_falls_back_when_no_response_field():
    agent = _agent()
    resp = agent._parse_output(json.dumps({"session_id": "x", "other": "value"}))
    # no "response" key → falls back to raw
    assert "session_id" in resp.text or resp.text != ""


@pytest.mark.asyncio
async def test_gemini_generic_resume_error_keeps_session_id():
    agent = _agent()
    agent.set_session_id("t1", "sess-abc")

    with patch(
        "oh_my_agent.agents.cli.gemini._stream_cli_process",
        new=AsyncMock(return_value=(1, b"", b"rate limited, retry later")),
    ):
        resp = await agent.run("hello", thread_id="t1")

    assert resp.error is not None
    assert agent.get_session_id("t1") == "sess-abc"


@pytest.mark.asyncio
async def test_gemini_invalid_resume_error_clears_session_id():
    agent = _agent()
    agent.set_session_id("t1", "sess-abc")

    with patch(
        "oh_my_agent.agents.cli.gemini._stream_cli_process",
        new=AsyncMock(return_value=(1, b"", b"invalid session identifier")),
    ):
        resp = await agent.run("hello", thread_id="t1")

    assert resp.error is not None
    assert agent.get_session_id("t1") is None
