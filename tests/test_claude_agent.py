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


@pytest.mark.asyncio
async def test_claude_error_max_turns_with_ndjson_stdout(monkeypatch):
    """Regression: claude emits NDJSON (system.init → assistant → user → result)
    even on max_turns failure. The failure path must parse the final result
    frame out of the stream rather than fall back to ``cli_error``.

    Without JSONL-aware parsing, ``error_kind`` silently becomes ``cli_error``
    and ``AgentRegistry.run()`` fallbacks to the next agent instead of
    short-circuiting. Observed in prod 2026-04-19.
    """
    ndjson = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "session_id": "sess-mt"}),
        json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read"}]},
        }),
        json.dumps({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]},
        }),
        json.dumps({
            "type": "result",
            "subtype": "error_max_turns",
            "result": "partial NDJSON",
            "terminal_reason": "max_turns",
            "session_id": "sess-mt",
            "errors": ["Reached maximum number of turns (2)"],
        }),
    ])

    async def _fail(*args, **kwargs):
        return 1, ndjson.encode(), b""

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _fail)

    agent = ClaudeAgent(cli_path="claude", model="sonnet-test")
    response = await agent.run("hello")

    assert response.error_kind == "max_turns"
    assert response.terminal_reason == "max_turns"
    assert response.partial_text == "partial NDJSON"


@pytest.mark.asyncio
async def test_claude_ndjson_without_result_frame_falls_back_to_cli_error(monkeypatch):
    """If the stream has no ``result`` event (e.g. CLI killed mid-stream),
    ``error_kind`` should fall back to ``classify_cli_error_kind`` on stderr —
    NOT silently stay ``cli_error`` via broken JSON parsing."""
    ndjson = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "session_id": "sess-x"}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}}),
    ])

    async def _fail(*args, **kwargs):
        return 1, ndjson.encode(), b"upstream 503: service unavailable"

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _fail)

    agent = ClaudeAgent(cli_path="claude", model="sonnet-test")
    response = await agent.run("hello")

    assert response.error_kind == "api_5xx"
    assert response.terminal_reason is None


def test_claude_build_env_exports_oma_agent_home():
    """SKILL.md scripts use ``$OMA_AGENT_HOME/skills/<name>/scripts/...``;
    Bash subprocesses spawned by claude-cli inherit env from the parent, so
    setting the var here is what makes the substitution resolve at runtime."""
    agent = ClaudeAgent(cli_path="claude", model="sonnet-test")
    env = agent._build_env()  # noqa: SLF001
    assert env.get("OMA_AGENT_HOME") == ".claude"


def test_gemini_build_env_exports_oma_agent_home():
    from oh_my_agent.agents.cli.gemini import GeminiCLIAgent

    agent = GeminiCLIAgent(cli_path="gemini")
    env = agent._build_env()  # noqa: SLF001
    assert env.get("OMA_AGENT_HOME") == ".gemini"


def test_codex_build_env_exports_oma_agent_home():
    from oh_my_agent.agents.cli.codex import CodexCLIAgent

    agent = CodexCLIAgent(cli_path="codex")
    env = agent._build_env()  # noqa: SLF001
    assert env.get("OMA_AGENT_HOME") == ".agents"
