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
    # Gemini stream fallback parses each stdout line as a TextEvent.
    lines = ["resumed gemini line A", "resumed gemini line B"]
    captured = _install_fake_stream(monkeypatch, lines)

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

    assert partials
    assert "resumed gemini line A" in partials[-1]
    assert "resumed gemini line B" in partials[-1]
    assert resp.error is None


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
