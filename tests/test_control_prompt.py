from oh_my_agent.agents.control_prompt import (
    CONTROL_PROMPT,
    build_system_preamble,
    inject_control_protocol,
    prepend_ambient,
)


def test_inject_control_protocol_prepends_instructions_once():
    prompt = inject_control_protocol("hello")
    assert CONTROL_PROMPT in prompt
    assert prompt.endswith("hello")

    prompt_again = inject_control_protocol(prompt)
    assert prompt_again.count(CONTROL_PROMPT) == 1


def test_prepend_ambient_prepends_memory_block():
    out = prepend_ambient("ask this", "[Remembered context]\n- M")
    assert out == "[Remembered context]\n- M\n\nask this"


def test_prepend_ambient_none_or_blank_returns_prompt_unchanged():
    assert prepend_ambient("ask", None) == "ask"
    assert prepend_ambient("ask", "   ") == "ask"


def test_build_system_preamble_control_only_when_no_ambient():
    assert build_system_preamble(None) == CONTROL_PROMPT
    assert build_system_preamble("  ") == CONTROL_PROMPT


def test_build_system_preamble_appends_ambient_after_control():
    out = build_system_preamble("[Remembered context]\n- M")
    assert out.startswith(CONTROL_PROMPT)
    assert "[Remembered context]" in out
    assert "- M" in out
