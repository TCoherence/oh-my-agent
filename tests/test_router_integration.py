import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from oh_my_agent.config import load_config
from oh_my_agent.gateway.router import OpenAICompatibleRouter


class _FakeRouterServer(ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass):
        super().__init__(server_address, RequestHandlerClass)
        self.last_payload = None
        self.last_auth = None


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        payload = json.loads(raw)
        self.server.last_payload = payload  # type: ignore[attr-defined]
        self.server.last_auth = self.headers.get("Authorization")  # type: ignore[attr-defined]

        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return

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
        out = json.dumps(body).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, _format, *_args):
        # keep test output clean
        return


@pytest.fixture
def local_router_server():
    srv = _FakeRouterServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        thread.join(timeout=2)
        srv.server_close()


@pytest.mark.asyncio
async def test_router_real_http_with_config_and_env(tmp_path, monkeypatch, local_router_server):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "integration-test-key")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        (
            "router:\n"
            "  enabled: true\n"
            "  provider: openai_compatible\n"
            f"  base_url: \"http://127.0.0.1:{local_router_server.server_port}/v1\"\n"
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

    assert local_router_server.last_payload is not None
    assert local_router_server.last_auth == "Bearer integration-test-key"
    assert local_router_server.last_payload["model"] == "deepseek-chat"
    user_msg = local_router_server.last_payload["messages"][1]["content"]
    assert "docs" in user_msg
