from oh_my_agent.agents.control_prompt import CONTROL_PROMPT, inject_control_protocol


def test_inject_control_protocol_prepends_instructions_once():
    prompt = inject_control_protocol("hello")
    assert CONTROL_PROMPT in prompt
    assert prompt.endswith("hello")

    prompt_again = inject_control_protocol(prompt)
    assert prompt_again.count(CONTROL_PROMPT) == 1
