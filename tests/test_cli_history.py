from oh_my_agent.agents.cli.base import _build_prompt_with_history


def test_no_history_returns_prompt_unchanged():
    result = _build_prompt_with_history("hello", None)
    assert result == "hello"


def test_empty_history_returns_prompt_unchanged():
    result = _build_prompt_with_history("hello", [])
    assert result == "hello"


def test_history_is_prepended():
    history = [{"role": "user", "content": "hi", "author": "alice"}]
    result = _build_prompt_with_history("follow up", history)
    assert "Previous conversation:" in result
    assert "[alice] hi" in result
    assert "Current message:" in result
    assert result.endswith("follow up")


def test_assistant_turn_uses_agent_label():
    history = [{"role": "assistant", "content": "hello back", "agent": "claude"}]
    result = _build_prompt_with_history("ok", history)
    assert "[claude] hello back" in result


def test_assistant_turn_falls_back_to_assistant_label():
    history = [{"role": "assistant", "content": "hi"}]
    result = _build_prompt_with_history("ok", history)
    assert "[assistant] hi" in result


def test_multi_turn_preserves_order():
    history = [
        {"role": "user", "content": "q1", "author": "alice"},
        {"role": "assistant", "content": "a1", "agent": "claude"},
        {"role": "user", "content": "q2", "author": "alice"},
    ]
    result = _build_prompt_with_history("q3", history)
    pos_q1 = result.index("q1")
    pos_a1 = result.index("a1")
    pos_q2 = result.index("q2")
    pos_current = result.index("q3")
    assert pos_q1 < pos_a1 < pos_q2 < pos_current
