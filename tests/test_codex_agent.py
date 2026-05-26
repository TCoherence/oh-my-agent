import pytest

from oh_my_agent.agents.cli.codex import CodexCLIAgent


def _joined(argv) -> str:
    return " ".join(str(a) for a in argv)


_CODEX_OK = b'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}'


@pytest.mark.asyncio
async def test_codex_folds_ambient_into_prompt_fresh_and_resume(monkeypatch):
    """Codex has no system channel: memory + control protocol stay folded into
    the prompt on every turn (behavior-preserving relocation)."""
    captured: dict = {}

    async def _capture(*args, **kwargs):
        captured["argv"] = list(args)
        return 0, _CODEX_OK, b""

    monkeypatch.setattr("oh_my_agent.agents.cli.codex._stream_cli_process", _capture)
    mem = "[Remembered context]\n- user likes terse"
    agent = CodexCLIAgent(cli_path="codex", model="gpt-test")

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
async def test_codex_ambient_none_keeps_control_no_memory(monkeypatch):
    captured: dict = {}

    async def _capture(*args, **kwargs):
        captured["argv"] = list(args)
        return 0, _CODEX_OK, b""

    monkeypatch.setattr("oh_my_agent.agents.cli.codex._stream_cli_process", _capture)
    agent = CodexCLIAgent(cli_path="codex", model="gpt-test")
    await agent.run("just ask", thread_id="t1", ambient_context=None)
    joined = _joined(captured["argv"])
    assert "[Control Protocol]" in joined
    assert "[Remembered context]" not in joined
    assert "just ask" in joined


def test_codex_command_includes_skip_git_repo_check_by_default():
    agent = CodexCLIAgent(cli_path="codex", model="gpt-test")
    cmd = agent._build_command("hello")
    assert "--skip-git-repo-check" in cmd


def test_codex_command_can_disable_skip_git_repo_check():
    agent = CodexCLIAgent(cli_path="codex", model="gpt-test", skip_git_repo_check=False)
    cmd = agent._build_command("hello")
    assert "--skip-git-repo-check" not in cmd


def test_codex_command_supports_custom_sandbox_mode():
    agent = CodexCLIAgent(
        cli_path="codex",
        model="gpt-test",
        sandbox_mode="danger-full-access",
    )
    cmd = agent._build_command("hello")
    assert "--sandbox" in cmd
    assert "danger-full-access" in cmd


def test_codex_command_supports_full_bypass_flag():
    agent = CodexCLIAgent(
        cli_path="codex",
        model="gpt-test",
        dangerously_bypass_approvals_and_sandbox=True,
    )
    cmd = agent._build_command("hello")
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--sandbox" not in cmd


def test_codex_command_supports_extra_args():
    agent = CodexCLIAgent(
        cli_path="codex",
        model="gpt-test",
        extra_args=["--search"],
    )
    cmd = agent._build_command("hello")
    assert "--search" in cmd


def test_codex_parse_output_handles_item_completed_agent_message():
    agent = CodexCLIAgent(cli_path="codex", model="gpt-test")
    raw = "\n".join([
        '{"type":"thread.started","thread_id":"abc"}',
        '{"type":"turn.started"}',
        '{"type":"item.completed","item":{"id":"item_0","type":"reasoning","text":"Thinking..."}}',
        '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"Final answer"}}',
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":3,"output_tokens":2}}',
    ])
    resp = agent._parse_output(raw)
    assert resp.text == "Final answer"
    assert resp.usage == {
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_read_input_tokens": 3,
    }


def test_codex_parse_output_handles_item_content_blocks():
    agent = CodexCLIAgent(cli_path="codex", model="gpt-test")
    raw = "\n".join([
        '{"type":"item.completed","item":{"type":"assistant_message","content":[{"type":"output_text","text":"Hello"},{"type":"output_text","text":"world"}]}}',
        '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":2}}',
    ])
    resp = agent._parse_output(raw)
    assert resp.text == "Hello world"
