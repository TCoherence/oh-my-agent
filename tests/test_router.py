import pytest

from oh_my_agent.gateway.router import OpenAICompatibleRouter, normalize_intent


def test_normalize_intent_passes_through_canonical_names():
    """All three v2 canonical intents must round-trip identical."""
    for canonical in ("reply", "artifact", "repo_update"):
        assert normalize_intent(canonical) == canonical


def test_normalize_intent_maps_legacy_aliases_to_canonical():
    """Legacy router-output names (both v1 canonical and pre-v1 aliases)
    from older models / fixtures must resolve to the v2 canonical set so
    the dispatcher only needs three arms."""
    cases = {
        # v1 canonical → v2 canonical
        "chat_reply": "reply",
        "invoke_skill": "artifact",
        "oneoff_artifact": "artifact",
        "propose_repo_change": "repo_update",
        "update_skill": "repo_update",
        # Pre-v1 legacy → v2 canonical
        "reply_once": "reply",
        "invoke_existing_skill": "artifact",
        "propose_artifact_task": "artifact",
        "propose_repo_task": "repo_update",
        "propose_task": "repo_update",
        "create_skill": "repo_update",
        "repair_skill": "repo_update",
    }
    for legacy, expected in cases.items():
        assert normalize_intent(legacy) == expected, f"{legacy!r} did not normalize"


def test_normalize_intent_is_case_and_whitespace_insensitive():
    """Robustness against router models that emit ``CREATE_SKILL`` /
    leading whitespace / etc."""
    assert normalize_intent("  CREATE_SKILL ") == "repo_update"
    assert normalize_intent("Reply_Once") == "reply"
    assert normalize_intent("INVOKE_SKILL") == "artifact"
    assert normalize_intent("Repo_Update") == "repo_update"


def test_normalize_intent_unknown_falls_back_to_reply():
    """Unknown / empty / garbage inputs fall back to ``reply`` — the
    safe default that keeps the runtime in chat mode rather than
    spawning a task."""
    for bad in ("", "   ", "garbage", "destroy_database", "1234"):
        assert normalize_intent(bad) == "reply"


@pytest.mark.asyncio
async def test_router_parses_valid_json_decision(monkeypatch):
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
        timeout_seconds=3,
        confidence_threshold=0.55,
    )

    def _fake_post(_payload):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"decision":"propose_repo_change","confidence":0.88,'
                            '"goal":"fix tests and docs","risk_hints":["multi-step"],'
                            '"task_type":"repo_change","completion_mode":"merge"}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("please fix and run tests")
    assert out is not None
    # Legacy ``propose_repo_change`` is normalized to v2 ``repo_update``.
    assert out.decision == "repo_update"
    assert out.confidence == 0.88
    assert out.goal == "fix tests and docs"
    assert out.risk_hints == ["multi-step"]
    assert out.skill_name is None
    assert out.task_type == "repo_change"
    assert out.completion_mode == "merge"
    # ``force_draft`` defaults False when missing from router payload.
    assert out.force_draft is False


@pytest.mark.asyncio
async def test_router_extracts_json_from_wrapped_text(monkeypatch):
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
    )

    def _fake_post(_payload):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            "Here is result:\\n"
                            '{"decision":"chat_reply","confidence":0.74,"goal":"","risk_hints":[]}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("hi")
    assert out is not None
    # Legacy ``chat_reply`` is normalized to v2 ``reply``.
    assert out.decision == "reply"
    assert out.confidence == 0.74
    assert out.skill_name is None


@pytest.mark.asyncio
async def test_router_retries_once_then_succeeds(monkeypatch):
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
        timeout_seconds=1,
        max_retries=1,
    )
    calls = {"n": 0}

    def _fake_post(_payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("timeout")
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"decision":"propose_repo_change","confidence":0.66,"goal":"do x",'
                            '"risk_hints":[],"task_type":"repo_change","completion_mode":"merge"}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("please do x")
    assert calls["n"] == 2
    assert out is not None
    # Legacy ``propose_repo_change`` is normalized to v2 ``repo_update``.
    assert out.decision == "repo_update"
    assert out.skill_name is None


@pytest.mark.asyncio
async def test_router_parses_create_skill_decision(monkeypatch):
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
    )

    def _fake_post(_payload):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"decision":"create_skill","confidence":0.91,'
                            '"goal":"Create a reusable weather skill",'
                            '"skill_name":"weather",'
                            '"risk_hints":["reusable"]}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("create a skill for checking weather")
    assert out is not None
    # Pre-v1 ``create_skill`` and v1 ``update_skill`` both normalize to
    # v2 ``repo_update``. Distinguishing create vs repair happens
    # downstream via skill_name lookup against the registered skill list.
    assert out.decision == "repo_update"
    assert out.skill_name == "weather"


@pytest.mark.asyncio
async def test_router_parses_artifact_task_decision(monkeypatch):
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
    )

    def _fake_post(_payload):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"decision":"oneoff_artifact","confidence":0.83,'
                            '"goal":"Generate a daily news markdown report",'
                            '"risk_hints":["multi_step"],'
                            '"task_type":"artifact","completion_mode":"reply"}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("生成一份今日新闻速读并整理成 markdown")
    assert out is not None
    # Legacy ``oneoff_artifact`` normalizes to v2 ``artifact``.
    assert out.decision == "artifact"
    assert out.task_type == "artifact"
    assert out.completion_mode == "reply"


@pytest.mark.asyncio
async def test_router_prompt_carries_disambiguation_and_examples(monkeypatch):
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
    )
    captured: dict = {}

    def _spy_post(payload):
        captured["payload"] = payload
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"decision":"chat_reply","confidence":0.9,"goal":"","risk_hints":[]}'
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _spy_post)
    await router.route("hi")
    messages = captured["payload"]["messages"]
    system_content = messages[0]["content"]
    assert "DISAMBIGUATION RULES" in system_content
    assert "EXAMPLES:" in system_content
    assert "Jensen Huang" in system_content
    # v2 canonical intents named in the prompt.
    assert "artifact" in system_content
    assert "repo_update" in system_content
    assert "reply" in system_content
    # ``force_draft`` keyword documented in the schema description.
    assert "force_draft" in system_content


@pytest.mark.asyncio
async def test_router_extra_body_cannot_override_reserved_keys(monkeypatch):
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="safe-model",
        extra_body={
            "model": "evil-model",
            "messages": [{"role": "user", "content": "hijack"}],
            "max_tokens": 999999,
            "temperature": 2,
            "reasoning": {"effort": "medium"},
        },
    )
    captured: dict = {}

    def _spy_post(payload):
        captured["payload"] = payload
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"decision":"chat_reply","confidence":0.9,"goal":"","risk_hints":[]}'
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _spy_post)
    await router.route("hi")
    payload = captured["payload"]
    assert payload["model"] == "safe-model"
    # Default max_tokens (4096) wins — extra_body cannot inflate it to 999999.
    assert payload["max_tokens"] == 4096
    assert payload["temperature"] == 0
    # Our user message must survive, not the injected hijack.
    user_msg = payload["messages"][-1]
    assert user_msg["role"] == "user"
    assert "hi" in user_msg["content"]
    # Non-reserved pass-through key survives.
    assert payload.get("reasoning") == {"effort": "medium"}


@pytest.mark.asyncio
async def test_router_parses_repair_skill_decision(monkeypatch):
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
    )

    def _fake_post(_payload):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"decision":"repair_skill","confidence":0.89,'
                            "\"goal\":\"Update existing skill 'top-5-daily-news' based on recent feedback\","
                            '"skill_name":"top-5-daily-news",'
                            '"risk_hints":["quality_feedback"],'
                            '"task_type":"skill_change","completion_mode":"merge"}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route(
        "这个 skill 不太对，帮我修一下",
        context="Recent thread context:\n- user: /top-5-daily-news",
    )
    assert out is not None
    # Legacy ``repair_skill`` from older router models normalizes to v2
    # ``repo_update``. Repair-vs-create is decided downstream by checking
    # ``skill_name`` against registered skills.
    assert out.decision == "repo_update"
    assert out.skill_name == "top-5-daily-news"
    assert out.task_type == "skill_change"
    assert out.completion_mode == "merge"


# ── DeepSeek V4 / reasoning-model adaptation regression tests ─────────── #
#
# These exercise the parsing layers added when DeepSeek V4 flash output
# started occasionally getting truncated mid-JSON (reasoning tokens eat
# the budget; pretty-printed output with multi-element ``risk_hints``
# crosses the old 400-token ceiling). Without recovery, the router would
# log ``Router returned non-JSON content`` and fall back to heuristics.


@pytest.mark.asyncio
async def test_router_recovers_truncated_json_pretty_printed(monkeypatch):
    """Real DeepSeek V4 flash failure mode: output cut off after a partial
    string value. Recovery should still surface the leading complete pairs."""
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="deepseek-v4-flash",
    )

    truncated = (
        '{\n'
        '  "decision": "chat_reply",\n'
        '  "confidence": 0.95,\n'
        '  "goal": "",\n'
        '  "risk_hints": ["lo'  # cut off mid-string, no closing quote/bracket/brace
    )

    def _fake_post(_payload):
        return {"choices": [{"message": {"content": truncated}}]}

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("飞书文档怎么接入比较方便？")
    assert out is not None, "truncated JSON must still produce a decision"
    # decision/confidence are what actually drive routing — those must
    # round-trip exactly even from truncated input. Legacy ``chat_reply``
    # normalizes to v2 ``reply``.
    assert out.decision == "reply"
    assert out.confidence == 0.95
    # ``risk_hints`` got cut mid-string. Strategy A closes the dangling
    # string and the open brackets, so we get the truncated literal back
    # ("lo") rather than dropping the whole list. That's strictly better
    # than the pre-fix behavior of dropping the entire response.
    assert out.risk_hints == ["lo"]


@pytest.mark.asyncio
async def test_router_recovers_truncated_json_after_complete_pair(monkeypatch):
    """When the model dies right after a comma, the safe-prefix strategy
    picks up the complete pairs to the left."""
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="deepseek-v4-flash",
    )

    truncated = (
        '{"decision":"oneoff_artifact","confidence":0.8,'
        '"goal":"daily news brief","task_type":"artifact",'
        '"completion_mode":"reply","risk_hints":["mu'  # killed mid risk_hints
    )

    def _fake_post(_payload):
        return {"choices": [{"message": {"content": truncated}}]}

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("生成一份今日新闻速读")
    assert out is not None
    # Legacy ``oneoff_artifact`` normalizes to v2 ``artifact``.
    assert out.decision == "artifact"
    assert out.task_type == "artifact"
    assert out.completion_mode == "reply"
    assert out.goal == "daily news brief"


@pytest.mark.asyncio
async def test_router_strips_markdown_code_fences(monkeypatch):
    """Some models still wrap JSON in ```json … ``` even with json mode on."""
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
    )

    fenced = (
        "```json\n"
        '{"decision":"chat_reply","confidence":0.9,"goal":"","risk_hints":[]}\n'
        "```"
    )

    def _fake_post(_payload):
        return {"choices": [{"message": {"content": fenced}}]}

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("hi")
    assert out is not None
    # Legacy ``chat_reply`` normalizes to v2 ``reply``.
    assert out.decision == "reply"


@pytest.mark.asyncio
async def test_router_defaults_to_json_response_format(monkeypatch):
    """JSON mode is on by default — DeepSeek and OpenAI-compatible APIs
    accept ``response_format: {"type":"json_object"}`` and emit far less
    prose wrapping when it is set."""
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
    )
    captured: dict = {}

    def _spy_post(payload):
        captured["payload"] = payload
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"decision":"chat_reply","confidence":0.9,"goal":"","risk_hints":[]}'
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _spy_post)
    await router.route("hi")
    assert captured["payload"].get("response_format") == {"type": "json_object"}


@pytest.mark.asyncio
async def test_router_extra_body_can_override_response_format(monkeypatch):
    """Operators on endpoints that reject ``json_object`` (or want a custom
    schema) must be able to override via ``extra_body`` — ``response_format``
    is intentionally NOT in the reserved-key list."""
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
        extra_body={"response_format": {"type": "text"}},
    )
    captured: dict = {}

    def _spy_post(payload):
        captured["payload"] = payload
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"decision":"chat_reply","confidence":0.9,"goal":"","risk_hints":[]}'
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _spy_post)
    await router.route("hi")
    assert captured["payload"].get("response_format") == {"type": "text"}


@pytest.mark.asyncio
async def test_router_max_tokens_constructor_param_propagates(monkeypatch):
    """Operators can dial up the budget for very chatty reasoning models."""
    router = OpenAICompatibleRouter(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
        max_tokens=4096,
    )
    captured: dict = {}

    def _spy_post(payload):
        captured["payload"] = payload
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"decision":"chat_reply","confidence":0.9,"goal":"","risk_hints":[]}'
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _spy_post)
    await router.route("hi")
    assert captured["payload"]["max_tokens"] == 4096
