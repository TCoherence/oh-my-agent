from oh_my_agent.agents.cli.codex import CodexCLIAgent


def test_codex_command_includes_skip_git_repo_check_by_default():
    agent = CodexCLIAgent(cli_path="codex", model="gpt-test")
    cmd = agent._build_command("hello")
    assert "--skip-git-repo-check" in cmd


def test_codex_command_can_disable_skip_git_repo_check():
    agent = CodexCLIAgent(cli_path="codex", model="gpt-test", skip_git_repo_check=False)
    cmd = agent._build_command("hello")
    assert "--skip-git-repo-check" not in cmd


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
