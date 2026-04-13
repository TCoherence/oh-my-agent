import asyncio
import json

import pytest

from oh_my_agent.agents.cli.claude import ClaudeAgent


def test_claude_command_includes_permission_bypass_by_default():
    agent = ClaudeAgent(cli_path="claude", model="sonnet-test")
    cmd = agent._build_command("hello")
    assert "--dangerously-skip-permissions" in cmd


def test_claude_command_can_disable_permission_bypass():
    agent = ClaudeAgent(
        cli_path="claude",
        model="sonnet-test",
        dangerously_skip_permissions=False,
        permission_mode="default",
    )
    cmd = agent._build_command("hello")
    assert "--dangerously-skip-permissions" not in cmd
    assert "--permission-mode" in cmd
    assert "default" in cmd


def test_claude_command_supports_extra_args():
    agent = ClaudeAgent(
        cli_path="claude",
        model="sonnet-test",
        extra_args=["--verbose"],
    )
    cmd = agent._build_command("hello")
    assert "--verbose" in cmd


@pytest.mark.asyncio
async def test_claude_timeout_returns_partial_excerpt(tmp_path, monkeypatch):
    log_path = tmp_path / "claude.log"
    log_path.write_text("a" * 2500, encoding="utf-8")

    async def _timeout(*args, **kwargs):
        raise asyncio.TimeoutError

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _timeout)

    agent = ClaudeAgent(cli_path="claude", model="sonnet-test", timeout=5)
    response = await agent.run("hello", log_path=log_path)

    assert response.error_kind == "timeout"
    assert response.terminal_reason == "timeout"
    assert response.partial_text == ("a" * 2000)


@pytest.mark.asyncio
async def test_claude_error_max_turns_returns_partial(monkeypatch):
    payload = {
        "type": "result",
        "subtype": "error_max_turns",
        "result": "partial answer",
        "terminal_reason": "max_turns",
    }

    async def _fail(*args, **kwargs):
        return 1, json.dumps(payload).encode(), b""

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _fail)

    agent = ClaudeAgent(cli_path="claude", model="sonnet-test")
    response = await agent.run("hello")

    assert response.error_kind == "max_turns"
    assert response.terminal_reason == "max_turns"
    assert response.partial_text == "partial answer"
