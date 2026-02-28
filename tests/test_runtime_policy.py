from oh_my_agent.runtime.policy import (
    build_skill_prompt,
    build_runtime_prompt,
    evaluate_strict_risk,
    extract_skill_name,
    is_skill_intent,
    is_long_task_intent,
    parse_task_state,
)


def test_long_task_intent_detects_coding_requests():
    assert is_long_task_intent("Please fix this bug and run tests")
    assert is_long_task_intent("帮我修复这个问题并跑测试")
    assert not is_long_task_intent("hello")


def test_skill_intent_detects_skill_requests():
    assert is_skill_intent("create a skill for weather")
    assert is_skill_intent("请帮我创建skill")
    assert not is_skill_intent("hello")


def test_strict_risk_flags_sensitive_and_budget_overrides():
    r1 = evaluate_strict_risk("fix bug", max_steps=8, max_minutes=20)
    assert r1.require_approval is False

    r2 = evaluate_strict_risk("fix bug with pip install deps", max_steps=8, max_minutes=20)
    assert r2.require_approval is True
    assert "contains_sensitive_keywords" in r2.reasons

    r3 = evaluate_strict_risk("fix bug", max_steps=12, max_minutes=20)
    assert r3.require_approval is True
    assert "steps_over_8" in r3.reasons


def test_parse_task_state_and_block_reason():
    text = "Work done\nTASK_STATE: DONE"
    assert parse_task_state(text) == ("DONE", None)

    blocked = "Need credentials\nTASK_STATE: BLOCKED\nBLOCK_REASON: missing API key"
    assert parse_task_state(blocked) == ("BLOCKED", "missing API key")

    fallback = "No marker output"
    assert parse_task_state(fallback) == ("CONTINUE", None)


def test_build_runtime_prompt_includes_loop_context():
    prompt = build_runtime_prompt(
        goal="Fix flaky test",
        original_request="Please fix flaky test in parser.py and keep output stable",
        step_no=2,
        max_steps=8,
        prior_failure="assert x == y",
        resume_instruction="focus on parser module",
    )
    assert "Normalized goal: Fix flaky test" in prompt
    assert "Original user request:" in prompt
    assert "Please fix flaky test in parser.py" in prompt
    assert "Current step: 2/8" in prompt
    assert "assert x == y" in prompt
    assert "focus on parser module" in prompt
    assert "User approval/merge happens outside your loop" in prompt
    assert "authoritative test command" in prompt


def test_extract_skill_name_prefers_explicit_names_and_detects_updates():
    name, is_update = extract_skill_name("create a skill named `weather-checker`", {"weather-checker"})
    assert name == "weather-checker"
    assert is_update is True

    name2, is_update2 = extract_skill_name("make a skill for pdf rotation automation", None)
    assert name2 == "make-a-skill-for"
    assert is_update2 is False


def test_build_skill_prompt_includes_skill_context():
    prompt = build_skill_prompt(
        skill_name="weather-checker",
        goal="Create a skill for weather checking",
        original_request="Please create a reusable skill for checking the weather",
        step_no=1,
        max_steps=6,
        prior_failure="Missing frontmatter",
        resume_instruction="Keep it minimal",
    )
    assert "Skill name: weather-checker" in prompt
    assert "skills/weather-checker/" in prompt
    assert "quick_validate.py skills/weather-checker" in prompt
    assert "Missing frontmatter" in prompt
    assert "Keep it minimal" in prompt
