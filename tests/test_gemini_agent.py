"""Tests for GeminiCLIAgent session resume."""
from __future__ import annotations

import asyncio
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


def test_gemini_command_can_disable_yolo():
    agent = GeminiCLIAgent(cli_path="gemini", model="gemini-test", yolo=False)
    cmd = agent._build_command("hello")
    assert "--yolo" not in cmd


def test_gemini_command_supports_extra_args():
    agent = GeminiCLIAgent(cli_path="gemini", model="gemini-test", extra_args=["--debug"])
    cmd = agent._build_command("hello")
    assert "--debug" in cmd


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


def _joined(argv) -> str:
    return " ".join(str(a) for a in argv)


_GEMINI_OK = b'{"response": "ok", "session_id": "s1"}'


@pytest.mark.asyncio
async def test_gemini_folds_ambient_into_prompt_fresh_and_resume(monkeypatch):
    """Gemini has no system channel: memory + control protocol stay folded into
    the prompt on every turn (behavior-preserving relocation)."""
    captured: dict = {}

    async def _capture(*args, **kwargs):
        captured["argv"] = list(args)
        return 0, _GEMINI_OK, b""

    monkeypatch.setattr("oh_my_agent.agents.cli.gemini._stream_cli_process", _capture)
    mem = "[Remembered context]\n- user likes terse"
    agent = _agent()

    await agent.run("do X", thread_id="t1", ambient_context=mem)
    joined = _joined(captured["argv"])
    assert "[Control Protocol]" in joined
    assert "[Remembered context]" in joined
    assert "do X" in joined

    agent.set_session_id("t1", "sess-1")
    await agent.run("do Y", thread_id="t1", ambient_context=mem)
    joined = _joined(captured["argv"])
    assert "[Control Protocol]" in joined
    assert "user likes terse" in joined
    assert "do Y" in joined


@pytest.mark.asyncio
async def test_gemini_ambient_none_keeps_control_no_memory(monkeypatch):
    captured: dict = {}

    async def _capture(*args, **kwargs):
        captured["argv"] = list(args)
        return 0, _GEMINI_OK, b""

    monkeypatch.setattr("oh_my_agent.agents.cli.gemini._stream_cli_process", _capture)
    agent = _agent()
    await agent.run("just ask", thread_id="t1", ambient_context=None)
    joined = _joined(captured["argv"])
    assert "[Control Protocol]" in joined
    assert "[Remembered context]" not in joined
    assert "just ask" in joined


@pytest.mark.asyncio
async def test_gemini_timeout_returns_partial_excerpt_and_terminal_reason(tmp_path, monkeypatch):
    """Gemini's timeout path must populate partial_text + terminal_reason the
    same way base/claude block mode does (previously a bare timeout response)."""
    log_path = tmp_path / "gemini.log"
    log_path.write_text("c" * 2500, encoding="utf-8")

    async def _timeout(*args, **kwargs):
        raise asyncio.TimeoutError

    monkeypatch.setattr("oh_my_agent.agents.cli.gemini._stream_cli_process", _timeout)

    agent = GeminiCLIAgent(cli_path="gemini", model="gemini-test", timeout=5)
    response = await agent.run("hello", log_path=log_path)

    assert response.error_kind == "timeout"
    assert response.terminal_reason == "timeout"
    assert response.partial_text == ("c" * 2000)


@pytest.mark.asyncio
async def test_gemini_timeout_override_is_per_call(monkeypatch):
    seen: dict[str, float] = {}

    async def _timeout(*args, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        raise asyncio.TimeoutError

    monkeypatch.setattr("oh_my_agent.agents.cli.gemini._stream_cli_process", _timeout)

    agent = GeminiCLIAgent(cli_path="gemini", model="gemini-test", timeout=300)
    response = await agent.run("hello", timeout_override=11)

    assert seen["timeout"] == 11
    assert "timed out after 11s" in response.error
    assert agent._timeout == 300


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
