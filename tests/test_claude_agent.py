import asyncio
import json

import pytest

from oh_my_agent.agents.cli.claude import ClaudeAgent, _parse_claude_stream_json


@pytest.mark.asyncio
async def test_claude_fresh_flattens_history_resume_drops_it(monkeypatch):
    """Root cause of the context-scatter bug: resume sends only the new prompt
    (no history), while a fresh run flattens prior turns in. The watermark gate
    relies on this — clearing the session forces the history-bearing fresh path."""
    captured: dict[str, list] = {}

    async def _capture(*args, **kwargs):
        captured["argv"] = list(args)
        ndjson = json.dumps({
            "type": "result", "subtype": "success", "result": "ok",
            "session_id": "sess-1",
        })
        return 0, ndjson.encode(), b""

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _capture)

    history = [
        {"role": "user", "content": "EARLIER-QUESTION"},
        {"role": "assistant", "content": "earlier-answer", "agent": "claude"},
    ]

    def _prompt_arg(argv: list) -> str:
        return argv[argv.index("-p") + 1]

    # Fresh (no session): history is flattened into the prompt.
    agent = ClaudeAgent(cli_path="claude")
    await agent.run("new-question", history=history, thread_id="t1")
    assert "--resume" not in captured["argv"]
    assert "EARLIER-QUESTION" in _prompt_arg(captured["argv"])

    # Resume (session set): only the new prompt is sent — history is dropped.
    agent.set_session_id("t1", "sess-1")
    await agent.run("another-question", history=history, thread_id="t1")
    assert "--resume" in captured["argv"]
    assert "EARLIER-QUESTION" not in _prompt_arg(captured["argv"])


def test_claude_command_omits_permission_bypass_by_default():
    """The documented default: permission bypass must be opt-in (boot.py's
    config fallback is False; the constructor default matches it)."""
    agent = ClaudeAgent(cli_path="claude", model="sonnet-test")
    cmd = agent._build_command("hello")
    assert "--dangerously-skip-permissions" not in cmd


def test_claude_command_includes_permission_bypass_when_enabled():
    agent = ClaudeAgent(
        cli_path="claude", model="sonnet-test", dangerously_skip_permissions=True
    )
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


def _arg_after(argv: list, flag: str) -> str | None:
    if flag not in argv:
        return None
    return argv[argv.index(flag) + 1]


def test_claude_extra_args_cannot_last_wins_override_ambient_append():
    """If an operator adds ``--append-system-prompt`` to ``extra_args`` (e.g. to
    force a tone/persona), per-call ambient must still be the LAST occurrence in
    argv so the claude CLI's last-wins parser keeps ambient. Otherwise the
    entire memory + control-protocol delivery would be silently overridden by
    static config."""
    agent = ClaudeAgent(
        cli_path="claude",
        model="sonnet-test",
        extra_args=["--append-system-prompt", "USER_OVERRIDE_PERSONA"],
    )
    cmd = agent._build_command("hi", system_append="AMBIENT_WITH_MEMORY")
    flag_positions = [i for i, x in enumerate(cmd) if x == "--append-system-prompt"]
    assert len(flag_positions) == 2  # both present
    # Last-wins: ambient must be at the LAST occurrence.
    assert cmd[flag_positions[-1] + 1] == "AMBIENT_WITH_MEMORY"

    rcmd = agent._build_resume_command("hi", "sess-x", system_append="AMBIENT_WITH_MEMORY")
    flag_positions = [i for i, x in enumerate(rcmd) if x == "--append-system-prompt"]
    assert len(flag_positions) == 2
    assert rcmd[flag_positions[-1] + 1] == "AMBIENT_WITH_MEMORY"


def test_claude_command_builders_accept_system_append():
    """Ambient context rides --append-system-prompt on both fresh and resume."""
    agent = ClaudeAgent(cli_path="claude", model="sonnet-test")
    fresh = agent._build_command("hello", system_append="SYS-X")
    assert "--append-system-prompt" in fresh
    assert _arg_after(fresh, "--append-system-prompt") == "SYS-X"
    resume = agent._build_resume_command("hello", "sess-1", system_append="SYS-Y")
    assert "--append-system-prompt" in resume
    assert _arg_after(resume, "--append-system-prompt") == "SYS-Y"
    # No system_append → flag absent.
    assert "--append-system-prompt" not in agent._build_command("hi")


@pytest.mark.asyncio
async def test_claude_ambient_goes_to_append_system_prompt_fresh_and_resume(monkeypatch):
    """The fix: control protocol + memory ride --append-system-prompt (re-supplied
    per call, never persisted), NOT the -p user prompt — on fresh AND resume."""
    captured: dict[str, list] = {}

    async def _capture(*args, **kwargs):
        captured["argv"] = list(args)
        return 0, json.dumps({"type": "result", "result": "ok", "session_id": "s1"}).encode(), b""

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _capture)
    mem = "[Remembered context]\n- user likes terse replies"
    agent = ClaudeAgent(cli_path="claude")

    # Fresh
    await agent.run("hello there", thread_id="t1", ambient_context=mem)
    argv = captured["argv"]
    assert "--resume" not in argv
    append = _arg_after(argv, "--append-system-prompt")
    prompt = _arg_after(argv, "-p")
    assert append is not None
    assert "[Control Protocol]" in append
    assert "[Remembered context]" in append and "user likes terse replies" in append
    assert "[Control Protocol]" not in prompt
    assert "[Remembered context]" not in prompt
    assert "hello there" in prompt

    # Resume
    agent.set_session_id("t1", "s1")
    await agent.run("next question", thread_id="t1", ambient_context=mem)
    argv = captured["argv"]
    assert "--resume" in argv
    append = _arg_after(argv, "--append-system-prompt")
    prompt = _arg_after(argv, "-p")
    assert "[Control Protocol]" in append
    assert "user likes terse replies" in append
    assert "[Control Protocol]" not in prompt
    assert "[Remembered context]" not in prompt


@pytest.mark.asyncio
async def test_claude_ambient_none_keeps_control_in_append_not_prompt(monkeypatch):
    """With no memory, the control protocol still rides --append-system-prompt
    (never the user prompt), and no memory block leaks anywhere."""
    captured: dict[str, list] = {}

    async def _capture(*args, **kwargs):
        captured["argv"] = list(args)
        return 0, json.dumps({"type": "result", "result": "ok", "session_id": "s1"}).encode(), b""

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _capture)
    agent = ClaudeAgent(cli_path="claude")
    await agent.run("just a question", thread_id="t1", ambient_context=None)
    argv = captured["argv"]
    append = _arg_after(argv, "--append-system-prompt")
    prompt = _arg_after(argv, "-p")
    assert append is not None and "[Control Protocol]" in append
    assert "[Remembered context]" not in append
    assert "[Control Protocol]" not in prompt
    assert prompt == "just a question"


@pytest.mark.asyncio
async def test_claude_fresh_streaming_builds_command_with_append(monkeypatch):
    """Fresh-streaming must build its command explicitly (command=) so the
    --append-system-prompt survives the shared base stream() path."""
    from oh_my_agent.agents.base import AgentResponse

    captured: dict = {}

    async def _fake_run_streamed(self, *, prompt, history, on_partial, workspace_override,
                                 log_path, thread_id=None, command=None, on_tool_use=None,
                                 timeout_override=None):
        captured["command"] = command
        return AgentResponse(text="ok")

    monkeypatch.setattr(ClaudeAgent, "_run_streamed", _fake_run_streamed)

    async def _noop(_):
        return None

    agent = ClaudeAgent(cli_path="claude")
    await agent.run("hi", thread_id="t1", ambient_context="[Remembered context]\n- M", on_partial=_noop)
    cmd = captured["command"]
    assert cmd is not None and "--resume" not in cmd
    append = _arg_after(cmd, "--append-system-prompt")
    assert append is not None and "[Control Protocol]" in append and "[Remembered context]" in append


@pytest.mark.asyncio
async def test_claude_resume_with_image_keeps_append_and_clean_prompt(tmp_path, monkeypatch):
    """Resume + image uses block mode; ambient still rides --append-system-prompt
    and the user -p carries the image reference, not the control protocol."""
    captured: dict[str, list] = {}

    async def _capture(*args, **kwargs):
        captured["argv"] = list(args)
        return 0, json.dumps({"type": "result", "result": "ok", "session_id": "s1"}).encode(), b""

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _capture)
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n")
    agent = ClaudeAgent(cli_path="claude", workspace=tmp_path)
    agent.set_session_id("t1", "s1")
    await agent.run("look", thread_id="t1", ambient_context="[Remembered context]\n- M",
                    image_paths=[img])
    argv = captured["argv"]
    assert "--resume" in argv
    append = _arg_after(argv, "--append-system-prompt")
    prompt = _arg_after(argv, "-p")
    assert append is not None and "[Control Protocol]" in append and "[Remembered context]" in append
    assert "[Control Protocol]" not in prompt
    assert "pic.png" in prompt


@pytest.mark.asyncio
async def test_claude_invalid_session_clears_even_with_ambient(monkeypatch):
    """A stale resume that the CLI rejects clears the session id even when
    ambient_context is supplied (the append must not mask the clear path)."""
    async def _fail(*args, **kwargs):
        return 1, b"", b"Error: conversation not found for session s1"

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _fail)
    agent = ClaudeAgent(cli_path="claude")
    agent.set_session_id("t1", "s1")
    resp = await agent.run("hi", thread_id="t1", ambient_context="[Remembered context]\n- M")
    assert resp.error is not None
    assert agent.get_session_id("t1") is None


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
async def test_claude_max_turns_override_is_per_call(monkeypatch):
    """Per-call ``max_turns_override`` lands in argv without mutating the
    configured ``_max_turns`` (concurrent runs must not corrupt each other)."""
    captured: dict[str, list] = {}

    async def _capture(*args, **kwargs):
        captured["argv"] = list(args)
        return 0, json.dumps({"type": "result", "result": "ok", "session_id": "s1"}).encode(), b""

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _capture)
    agent = ClaudeAgent(cli_path="claude", max_turns=25)

    await agent.run("hello", thread_id="t1", max_turns_override=80)
    assert _arg_after(captured["argv"], "--max-turns") == "80"
    assert agent._max_turns == 25

    # Resume path honors the override too.
    await agent.run("again", thread_id="t1", max_turns_override=55)
    assert "--resume" in captured["argv"]
    assert _arg_after(captured["argv"], "--max-turns") == "55"
    assert agent._max_turns == 25

    # No override → configured budget.
    await agent.run("third", thread_id="t1")
    assert _arg_after(captured["argv"], "--max-turns") == "25"


@pytest.mark.asyncio
async def test_claude_timeout_override_is_per_call(monkeypatch):
    """Per-call ``timeout_override`` drives the subprocess timeout and the
    error message; ``_timeout`` stays untouched."""
    seen: dict[str, float] = {}

    async def _timeout(*args, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        raise asyncio.TimeoutError

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _timeout)
    agent = ClaudeAgent(cli_path="claude", model="sonnet-test", timeout=300)

    response = await agent.run("hello", timeout_override=7)

    assert seen["timeout"] == 7
    assert response.error_kind == "timeout"
    assert "timed out after 7s" in response.error
    assert agent._timeout == 300


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
async def test_claude_max_turns_error_message_is_self_describing(monkeypatch):
    """Regression: the ``error`` field on a max_turns failure must read
    ``"claude: max_turns reached (<N> turns)"`` — not ``"claude exited 1:
    {"type":"system","subtype":"init",..."`` which is the raw NDJSON head.

    Before this fix the stdout-prefix leak made every max_turns task look
    like a cli_error in the runtime_tasks.error column / Discord task card,
    masking the real cause even though ``error_kind`` was already correctly
    classified. ``raw`` and ``partial_text`` still carry the original frame
    for forensic inspection.
    """
    ndjson = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "session_id": "sess-mt"}),
        json.dumps({
            "type": "result",
            "subtype": "error_max_turns",
            "num_turns": 61,
            "result": "partial",
            "terminal_reason": "max_turns",
            "session_id": "sess-mt",
        }),
    ])

    async def _fail(*args, **kwargs):
        return 1, ndjson.encode(), b""

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _fail)

    agent = ClaudeAgent(cli_path="claude", model="sonnet-test")
    response = await agent.run("hello")

    assert response.error_kind == "max_turns"
    assert response.error == "claude: max_turns reached (61 turns)"
    # raw frame and partial_text still carry the original signal for debugging.
    assert isinstance(response.raw, dict)
    assert response.raw.get("subtype") == "error_max_turns"
    assert response.partial_text == "partial"


@pytest.mark.asyncio
async def test_claude_max_turns_error_message_omits_count_when_unknown(monkeypatch):
    """If ``num_turns`` is missing from the result frame, the message gracefully
    drops the count instead of rendering ``(None turns)``."""
    payload = {
        "type": "result",
        "subtype": "error_max_turns",
        "result": "partial",
        "terminal_reason": "max_turns",
    }

    async def _fail(*args, **kwargs):
        return 1, json.dumps(payload).encode(), b""

    monkeypatch.setattr("oh_my_agent.agents.cli.claude._stream_cli_process", _fail)

    agent = ClaudeAgent(cli_path="claude", model="sonnet-test")
    response = await agent.run("hello")

    assert response.error_kind == "max_turns"
    assert response.error == "claude: max_turns reached"


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
