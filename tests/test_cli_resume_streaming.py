"""Pin that the ``--resume`` path also goes through streaming when
``on_partial`` is passed.

Regression for the MVP oversight where only the *first* turn of a Discord
thread got live edits — every subsequent turn (Claude ``--resume``,
Codex ``exec resume``, Gemini ``--resume``) silently fell back to block mode
because ``run()`` branched on ``session_id`` before checking ``on_partial``.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest

from oh_my_agent.agents.cli import base as cli_base
from oh_my_agent.agents.cli.claude import ClaudeAgent
from oh_my_agent.agents.cli.codex import CodexCLIAgent
from oh_my_agent.agents.cli.gemini import GeminiCLIAgent


def _install_fake_stream(monkeypatch, ndjson_lines: list[str]):
    """Replace ``_stream_cli_lines`` with a generator that yields the given
    stdout lines. Returns the ``captured_cmd`` list that the fake writes to."""
    captured_cmd: list[list[str]] = []

    async def _fake_stream(*cmd, cwd, env, timeout, cancel=None, log_path=None) -> AsyncIterator[tuple[str, str]]:
        captured_cmd.append(list(cmd))
        for line in ndjson_lines:
            yield ("stdout", line)

    monkeypatch.setattr(cli_base, "_stream_cli_lines", _fake_stream)
    return captured_cmd


@pytest.mark.asyncio
async def test_claude_resume_runs_through_streaming_when_on_partial(monkeypatch):
    ndjson = [
        '{"type":"system","subtype":"init","session_id":"sess_existing","model":"sonnet","tools":[]}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"resumed reply part 1 "}]}}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"part 2"}]}}',
        '{"type":"result","subtype":"success","usage":{"input_tokens":10,"output_tokens":5}}',
    ]
    captured = _install_fake_stream(monkeypatch, ndjson)

    agent = ClaudeAgent(cli_path="claude")
    agent.set_session_id("t1", "sess_existing")

    partials: list[str] = []

    async def _on_partial(text: str) -> None:
        partials.append(text)

    resp = await agent.run("follow-up prompt", thread_id="t1", on_partial=_on_partial)

    # argv was the resume command, not fresh `-p`.
    assert captured, "expected one streamed invocation"
    cmd = captured[0]
    assert "--resume" in cmd
    assert "sess_existing" in cmd
    assert "stream-json" in cmd

    # on_partial was called at least once with accumulated text.
    assert partials, "on_partial was never called on resume path"
    assert "resumed reply part 1" in partials[-1]
    assert "part 2" in partials[-1]

    # Final response collapses the partials.
    assert "resumed reply part 1" in resp.text
    assert "part 2" in resp.text
    assert resp.error is None


@pytest.mark.asyncio
async def test_codex_resume_runs_through_streaming_when_on_partial(monkeypatch):
    jsonl = [
        '{"type":"thread.started","thread_id":"thr_existing"}',
        '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"resumed codex reply"}}',
        '{"type":"turn.completed","usage":{"input_tokens":20,"output_tokens":8}}',
    ]
    captured = _install_fake_stream(monkeypatch, jsonl)

    agent = CodexCLIAgent(cli_path="codex")
    agent.set_session_id("t1", "thr_existing")

    partials: list[str] = []

    async def _on_partial(text: str) -> None:
        partials.append(text)

    resp = await agent.run("follow-up", thread_id="t1", on_partial=_on_partial)

    assert captured
    cmd = captured[0]
    # Codex resume form: `codex exec resume <id> ...`
    assert "exec" in cmd
    assert "resume" in cmd
    assert "thr_existing" in cmd

    assert partials, "on_partial should have been called during resume streaming"
    assert "resumed codex reply" in partials[-1]
    assert "resumed codex reply" in resp.text
    assert resp.error is None


@pytest.mark.asyncio
async def test_gemini_resume_runs_through_streaming_when_on_partial(monkeypatch):
    # Real Gemini `--output-format json` emits a single JSON object once the
    # response is ready. The streaming path must extract `response` out of
    # that, not forward the raw JSON line to the user.
    import json as _json

    payload = _json.dumps(
        {
            "response": "resumed gemini reply",
            "session_id": "gsess_new",
            "stats": {
                "models": {
                    "gemini-3-flash-preview": {
                        "tokens": {"prompt": 12, "candidates": 7, "cached": 0},
                    }
                }
            },
        }
    )
    captured = _install_fake_stream(monkeypatch, [payload])

    agent = GeminiCLIAgent(cli_path="gemini")
    agent.set_session_id("t1", "gsess_existing")

    partials: list[str] = []

    async def _on_partial(text: str) -> None:
        partials.append(text)

    resp = await agent.run("hi again", thread_id="t1", on_partial=_on_partial)

    assert captured
    cmd = captured[0]
    assert "--resume" in cmd
    assert "gsess_existing" in cmd

    assert partials, "on_partial should fire at least once during streaming"
    # Must be the extracted response text, not the raw JSON literal.
    assert partials[-1] == "resumed gemini reply"
    assert "{" not in partials[-1], "partial must not leak raw JSON to users"

    assert resp.text == "resumed gemini reply"
    assert resp.error is None
    # session_id from the emitted init event is captured + stored.
    assert agent.get_session_id("t1") == "gsess_new"
    # Usage is surfaced via the stats → usage translator.
    assert resp.usage == {
        "input_tokens": 12,
        "output_tokens": 7,
        "cache_read_input_tokens": 0,
    }


@pytest.mark.asyncio
async def test_gemini_plaintext_stream_lines_still_forwarded(monkeypatch):
    """Non-JSON lines (future plaintext stream mode or stray output) must still
    reach the user as plain TextEvents, not be silently dropped."""
    captured = _install_fake_stream(
        monkeypatch,
        ["partial line one", "partial line two"],
    )

    agent = GeminiCLIAgent(cli_path="gemini")
    agent.set_session_id("t1", "gsess_existing")

    partials: list[str] = []

    async def _on_partial(text: str) -> None:
        partials.append(text)

    resp = await agent.run("hi again", thread_id="t1", on_partial=_on_partial)

    assert captured
    assert partials
    assert "partial line one" in partials[-1]
    assert "partial line two" in partials[-1]
    assert resp.error is None


@pytest.mark.asyncio
async def test_claude_streaming_fires_on_tool_use_for_each_tool_use_event(monkeypatch):
    ndjson = [
        '{"type":"system","subtype":"init","session_id":"sess_new","model":"sonnet","tools":[]}',
        '{"type":"assistant","message":{"content":['
        '{"type":"tool_use","id":"tu_1","name":"Read","input":{"file_path":"/x"}}'
        "]}}",
        '{"type":"user","message":{"content":['
        '{"type":"tool_result","tool_use_id":"tu_1","content":"..."}'
        "]}}",
        '{"type":"assistant","message":{"content":['
        '{"type":"tool_use","id":"tu_2","name":"Bash","input":{"command":"ls"}}'
        "]}}",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
        '{"type":"result","subtype":"success"}',
    ]
    _install_fake_stream(monkeypatch, ndjson)

    agent = ClaudeAgent(cli_path="claude")

    tool_names: list[str] = []

    async def _on_tool_use(name: str) -> None:
        tool_names.append(name)

    async def _on_partial(text: str) -> None:
        pass

    resp = await agent.run(
        "please look up something",
        thread_id="t1",
        on_partial=_on_partial,
        on_tool_use=_on_tool_use,
    )

    assert tool_names == ["Read", "Bash"]
    assert resp.error is None
    assert resp.text.strip() == "ok"


@pytest.mark.asyncio
async def test_on_tool_use_alone_activates_streaming_path(monkeypatch):
    """``on_tool_use`` without ``on_partial`` must still take the streaming path."""
    ndjson = [
        '{"type":"system","subtype":"init","session_id":"sess_x","model":"sonnet","tools":[]}',
        '{"type":"assistant","message":{"content":['
        '{"type":"tool_use","id":"tu_a","name":"Glob","input":{"pattern":"*.py"}}'
        "]}}",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"all done"}]}}',
        '{"type":"result","subtype":"success"}',
    ]
    captured = _install_fake_stream(monkeypatch, ndjson)

    agent = ClaudeAgent(cli_path="claude")

    tool_names: list[str] = []

    async def _on_tool_use(name: str) -> None:
        tool_names.append(name)

    resp = await agent.run(
        "scan",
        thread_id="t1",
        on_tool_use=_on_tool_use,
    )

    assert captured, "streaming path was not taken"
    assert "stream-json" in captured[0]
    assert tool_names == ["Glob"]
    assert resp.text.strip() == "all done"


@pytest.mark.asyncio
async def test_codex_streaming_fires_on_tool_use_for_command_execution(monkeypatch):
    jsonl = [
        '{"type":"thread.started","thread_id":"thr_x"}',
        '{"type":"item.started","item":{"id":"c1","type":"command_execution","command":"ls"}}',
        '{"type":"item.completed","item":{"id":"c1","type":"command_execution","aggregated_output":"a","exit_code":0}}',
        '{"type":"item.completed","item":{"id":"m1","type":"agent_message","text":"done"}}',
        '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}',
    ]
    _install_fake_stream(monkeypatch, jsonl)

    agent = CodexCLIAgent(cli_path="codex")
    tool_names: list[str] = []

    async def _on_tool_use(name: str) -> None:
        tool_names.append(name)

    resp = await agent.run("run something", thread_id="t1", on_tool_use=_on_tool_use)

    # Command execution surfaces as a "Bash" tool-use event in Codex mapping.
    assert tool_names == ["Bash"]
    assert resp.text.strip() == "done"


@pytest.mark.asyncio
async def test_claude_resume_with_image_stays_in_block_mode(monkeypatch, tmp_path):
    """Images + resume still fall back to block mode — streaming + argv
    augmentation is not yet wired together. Pins the intentional skip."""
    # Block-mode path uses _stream_cli_process, not _stream_cli_lines. If the
    # streaming path were accidentally taken, our fake _stream_cli_lines would
    # record the cmd; if block path is taken, the list stays empty.
    captured = _install_fake_stream(monkeypatch, ["ignored"])

    image = tmp_path / "img.png"
    image.write_bytes(b"\x89PNG\r\n")

    async def _fake_proc(*cmd, cwd, env, timeout, log_path=None):
        # Return rc=0, empty stdout, empty stderr — we only care the block
        # branch was taken.
        return 0, b'{"type":"result","subtype":"success"}\n', b""

    monkeypatch.setattr(cli_base, "_stream_cli_process", _fake_proc)

    agent = ClaudeAgent(cli_path="claude")
    agent.set_session_id("t1", "sess_existing")

    async def _on_partial(text: str) -> None:
        pass

    await agent.run(
        "describe this",
        thread_id="t1",
        on_partial=_on_partial,
        image_paths=[image],
    )

    # Streaming path should NOT have been taken (image present).
    assert captured == [], "resume+image should stay in block mode"
