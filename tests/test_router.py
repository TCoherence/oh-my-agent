import pytest

from oh_my_agent.gateway.router import OpenAICompatibleRouter


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
    assert out.decision == "propose_repo_change"
    assert out.confidence == 0.88
    assert out.goal == "fix tests and docs"
    assert out.risk_hints == ["multi-step"]
    assert out.skill_name is None
    assert out.task_type == "repo_change"
    assert out.completion_mode == "merge"


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
    assert out.decision == "chat_reply"
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
    assert out.decision == "propose_repo_change"
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
    # Legacy router models still emit "create_skill"; the parser
    # normalizes that to the canonical ``update_skill`` intent so the
    # dispatcher only needs one match arm. Distinguishing create vs
    # repair happens downstream via the registered skill list.
    assert out.decision == "update_skill"
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
    assert out.decision == "oneoff_artifact"
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
    assert "oneoff_artifact" in system_content
    assert "update_skill" in system_content


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
    assert payload["max_tokens"] == 400
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
    # Legacy ``repair_skill`` from older router models is normalized to
    # the canonical ``update_skill`` intent. Repair-vs-create is decided
    # downstream by checking ``skill_name`` against registered skills.
    assert out.decision == "update_skill"
    assert out.skill_name == "top-5-daily-news"
    assert out.task_type == "skill_change"
    assert out.completion_mode == "merge"
