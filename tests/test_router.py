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
                            '{"decision":"propose_task","confidence":0.88,'
                            '"goal":"fix tests and docs","risk_hints":["multi-step"]}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("please fix and run tests")
    assert out is not None
    assert out.decision == "propose_task"
    assert out.confidence == 0.88
    assert out.goal == "fix tests and docs"
    assert out.risk_hints == ["multi-step"]


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
                            '{"decision":"reply_once","confidence":0.74,"goal":"","risk_hints":[]}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(router, "_post_json", _fake_post)
    out = await router.route("hi")
    assert out is not None
    assert out.decision == "reply_once"
    assert out.confidence == 0.74
