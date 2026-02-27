import json
import os
from email.message import Message

import pytest
import urllib.request

from oh_my_agent.config import load_config
from oh_my_agent.gateway.router import OpenAICompatibleRouter


class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body
        self.headers = Message()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_router_real_http_with_config_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "integration-test-key")
    captured: dict[str, object] = {}

    def _fake_urlopen(req: urllib.request.Request, timeout: int):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["auth"] = req.headers.get("Authorization")
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        content = json.dumps(
            {
                "decision": "propose_task",
                "confidence": 0.92,
                "goal": "create docs smoke file and run tests",
                "risk_hints": ["multi_step", "run_tests"],
            },
            ensure_ascii=False,
        )
        body = {
            "id": "chatcmpl-local",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        }
        return _FakeHTTPResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        (
            "router:\n"
            "  enabled: true\n"
            "  provider: openai_compatible\n"
            "  base_url: \"https://router.example.test/v1\"\n"
            "  api_key_env: DEEPSEEK_API_KEY\n"
            "  model: deepseek-chat\n"
            "  timeout_seconds: 5\n"
            "  max_retries: 0\n"
            "  confidence_threshold: 0.55\n"
        ),
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)
    rcfg = cfg["router"]
    router = OpenAICompatibleRouter(
        base_url=str(rcfg["base_url"]),
        api_key=str(os.environ[str(rcfg["api_key_env"])]),
        model=str(rcfg["model"]),
        timeout_seconds=int(rcfg["timeout_seconds"]),
        max_retries=int(rcfg["max_retries"]),
        confidence_threshold=float(rcfg["confidence_threshold"]),
    )

    decision = await router.route("请在 docs 下新增文件并跑测试")
    assert decision is not None
    assert decision.decision == "propose_task"
    assert decision.confidence == pytest.approx(0.92)
    assert "run tests" in decision.goal

    assert captured["url"] == "https://router.example.test/v1/chat/completions"
    assert captured["timeout"] == 5
    assert captured["payload"] is not None
    assert captured["auth"] == "Bearer integration-test-key"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "deepseek-chat"
    user_msg = payload["messages"][1]["content"]
    assert "docs" in user_msg
