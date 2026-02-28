import json
import os
import urllib.request

import pytest

from oh_my_agent.config import load_config
from oh_my_agent.gateway.router import OpenAICompatibleRouter


class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


@pytest.mark.asyncio
async def test_router_real_http_with_config_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "integration-test-key")
    seen: dict[str, object] = {}

    def _fake_urlopen(req: urllib.request.Request, timeout: int = 0):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["timeout"] = timeout
        seen["payload"] = json.loads(req.data.decode("utf-8"))
        content = json.dumps(
            {
                "decision": "propose_repo_task",
                "confidence": 0.92,
                "goal": "create docs smoke file and run tests",
                "risk_hints": ["multi_step", "run_tests"],
                "skill_name": "",
                "task_type": "repo_change",
                "completion_mode": "merge",
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
    assert decision.decision == "propose_repo_task"
    assert decision.confidence == pytest.approx(0.92)
    assert "run tests" in decision.goal
    assert decision.task_type == "repo_change"
    assert decision.completion_mode == "merge"

    assert seen["url"] == "https://router.example.test/v1/chat/completions"
    assert seen["auth"] == "Bearer integration-test-key"
    assert seen["timeout"] == 5
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "deepseek-chat"
    user_msg = payload["messages"][1]["content"]
    assert "docs" in user_msg
