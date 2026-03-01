"""Tests for CodexCLIAgent session resume."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from oh_my_agent.agents.cli.codex import CodexCLIAgent


def _agent() -> CodexCLIAgent:
    return CodexCLIAgent(cli_path="codex", model="o4-mini-test")


# ---------------------------------------------------------------------------
# Session ID management
# ---------------------------------------------------------------------------

def test_codex_session_id_initially_none():
    agent = _agent()
    assert agent.get_session_id("t1") is None


def test_codex_set_and_get_session_id():
    agent = _agent()
    agent.set_session_id("t1", "sess-abc")
    assert agent.get_session_id("t1") == "sess-abc"


def test_codex_clear_session():
    agent = _agent()
    agent.set_session_id("t1", "sess-abc")
    agent.clear_session("t1")
    assert agent.get_session_id("t1") is None


def test_codex_clear_session_missing_is_noop():
    agent = _agent()
    agent.clear_session("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------

def test_codex_fresh_command_uses_exec():
    agent = _agent()
    cmd = agent._build_command("hello")
    assert "exec" in cmd
    assert "resume" not in cmd


def test_codex_resume_command_uses_exec_resume():
    agent = _agent()
    cmd = agent._build_resume_command("hello", "sess-xyz")
    assert "exec" in cmd
    assert "resume" in cmd
    assert "sess-xyz" in cmd


def test_codex_resume_command_has_json_flag():
    agent = _agent()
    cmd = agent._build_resume_command("hello", "sess-xyz")
    assert "--json" in cmd


def test_codex_resume_command_has_full_auto():
    agent = _agent()
    cmd = agent._build_resume_command("hello", "sess-xyz")
    assert "--full-auto" in cmd


def test_codex_resume_command_includes_prompt():
    agent = _agent()
    cmd = agent._build_resume_command("my prompt", "sess-xyz")
    assert "my prompt" in cmd


def test_codex_resume_command_includes_skip_git_repo_check_by_default():
    agent = _agent()
    cmd = agent._build_resume_command("hello", "sess-xyz")
    assert "--skip-git-repo-check" in cmd


def test_codex_resume_command_omits_skip_git_repo_check_when_disabled():
    agent = CodexCLIAgent(cli_path="codex", model="o4-mini-test", skip_git_repo_check=False)
    cmd = agent._build_resume_command("hello", "sess-xyz")
    assert "--skip-git-repo-check" not in cmd


# ---------------------------------------------------------------------------
# Session ID capture from JSONL
# ---------------------------------------------------------------------------

def test_codex_parse_output_session_id_not_captured_by_parse_output():
    """_parse_output does not capture session_id (done in run()); text is extracted normally."""
    import json
    agent = _agent()
    raw = "\n".join([
        '{"type":"thread.started","thread_id":"019ca7d1-1b1d-7e73-8718-84114cec1905"}',
        '{"type":"turn.started"}',
        '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"Hi!"}}',
        '{"type":"turn.completed","usage":{"input_tokens":5,"cached_input_tokens":0,"output_tokens":3}}',
    ])
    resp = agent._parse_output(raw)
    assert resp.text == "Hi!"
    # session_id not stored by _parse_output
    assert agent.get_session_id("t1") is None


@pytest.mark.asyncio
async def test_codex_generic_resume_error_keeps_session_id():
    agent = _agent()
    agent.set_session_id("t1", "sess-abc")

    with patch(
        "oh_my_agent.agents.cli.codex._stream_cli_process",
        new=AsyncMock(return_value=(1, b"", b"rate limited, retry later")),
    ):
        resp = await agent.run("hello", thread_id="t1")

    assert resp.error is not None
    assert agent.get_session_id("t1") == "sess-abc"


@pytest.mark.asyncio
async def test_codex_invalid_resume_error_clears_session_id():
    agent = _agent()
    agent.set_session_id("t1", "sess-abc")

    with patch(
        "oh_my_agent.agents.cli.codex._stream_cli_process",
        new=AsyncMock(return_value=(1, b"", b"session not found")),
    ):
        resp = await agent.run("hello", thread_id="t1")

    assert resp.error is not None
    assert agent.get_session_id("t1") is None
