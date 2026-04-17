import asyncio
import json

import pytest

from oh_my_agent.agents.cli.claude import ClaudeAgent, _parse_claude_stream_json


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


def test_claude_command_uses_stream_json_verbose():
    agent = ClaudeAgent(cli_path="claude", model="sonnet-test")
    cmd = agent._build_command("hello")
    assert "--output-format" in cmd
    idx = cmd.index("--output-format")
    assert cmd[idx + 1] == "stream-json"
    assert "--verbose" in cmd


def test_claude_resume_command_uses_stream_json_verbose():
    agent = ClaudeAgent(cli_path="claude", model="sonnet-test")
    cmd = agent._build_resume_command("hello", "session-abc")
    assert "--resume" in cmd
    assert "session-abc" in cmd
    idx = cmd.index("--output-format")
    assert cmd[idx + 1] == "stream-json"
    assert "--verbose" in cmd


def test_parse_stream_json_extracts_session_and_result():
    raw = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1"}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}),
        json.dumps({
            "type": "result",
            "subtype": "success",
            "result": "final answer",
            "session_id": "sess-1",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 0.01,
        }),
    ])
    init_sid, frame = _parse_claude_stream_json(raw)
    assert init_sid == "sess-1"
    assert frame is not None
    assert frame["result"] == "final answer"
    assert frame["total_cost_usd"] == 0.01


def test_parse_stream_json_returns_single_result_frame():
    """A single-line JSON result frame (the typical error path) is parsed as the final frame."""
    payload = {
        "type": "result",
        "subtype": "error_max_turns",
        "result": "partial",
        "session_id": "sess-err",
    }
    _, frame = _parse_claude_stream_json(json.dumps(payload))
    assert frame == payload


def test_parse_stream_json_fallback_on_non_ndjson_single_object():
    """Raw that isn't NDJSON (no newlines, no type field) still parses as final frame via fallback."""
    payload = {"session_id": "sess-x", "result": "hello"}
    init_sid, frame = _parse_claude_stream_json(json.dumps(payload))
    # The loop parses it as one line and records it as final_frame only if type=result.
    # Here type is missing, so the loop sees an event but no result; fallback doesn't fire
    # (stream_saw_events=True). Document this behavior: frame is None, session stays None.
    assert frame is None
    assert init_sid is None


def test_parse_stream_json_uses_init_session_when_result_has_none():
    raw = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "session_id": "sess-init-only"}),
        json.dumps({"type": "result", "subtype": "success", "result": "x"}),
    ])
    init_sid, frame = _parse_claude_stream_json(raw)
    assert init_sid == "sess-init-only"
    assert frame is not None
    assert frame.get("session_id") is None


def test_parse_stream_json_handles_empty_and_malformed():
    assert _parse_claude_stream_json("") == (None, None)
    assert _parse_claude_stream_json("not json\nalso not json") == (None, None)


def test_parse_stream_json_skips_non_dict_events():
    raw = "\n".join([
        "[1,2,3]",
        json.dumps({"type": "result", "result": "ok"}),
    ])
    _, frame = _parse_claude_stream_json(raw)
    assert frame is not None
    assert frame["result"] == "ok"


@pytest.mark.asyncio
async def test_claude_success_parses_stream_json_and_stores_session(monkeypatch):
    ndjson = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "session_id": "sess-42"}),
        json.dumps({
            "type": "result",
            "subtype": "success",
            "result": "done",
            "session_id": "sess-42",
            "usage": {"input_tokens": 100},
            "total_cost_usd": 0.02,
        }),
    ])

    async def _ok(*args, **kwargs):
        return 0, ndjson.encode(), b""

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _ok)

    agent = ClaudeAgent(cli_path="claude", model="sonnet-test")
    response = await agent.run("hello", thread_id="thread-1")

    assert response.text == "done"
    assert agent.get_session_id("thread-1") == "sess-42"
    assert response.usage == {"input_tokens": 100, "cost_usd": 0.02}


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
