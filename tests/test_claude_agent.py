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
