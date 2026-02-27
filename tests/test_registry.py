import pytest
from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.agents.registry import AgentRegistry


class _OKAgent(BaseAgent):
    def __init__(self, name: str, response: str = "ok"):
        self._name = name
        self._response = response
        self.calls: list[tuple] = []

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt, history=None):
        self.calls.append((prompt, history))
        return AgentResponse(text=self._response)


class _FailAgent(BaseAgent):
    def __init__(self, name: str, error: str = "fail"):
        self._name = name
        self._error = error
        self.calls: list[tuple] = []

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt, history=None):
        self.calls.append((prompt, history))
        return AgentResponse(text="", error=self._error)


class _WorkspaceAgent(BaseAgent):
    def __init__(self, name: str):
        self._name = name
        self.calls: list[tuple] = []

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt, history=None, *, thread_id=None, workspace_override=None):
        self.calls.append((prompt, history, thread_id, workspace_override))
        return AgentResponse(text="ok")


def test_registry_requires_at_least_one_agent():
    with pytest.raises(ValueError):
        AgentRegistry([])


@pytest.mark.asyncio
async def test_returns_first_successful_agent():
    a = _OKAgent("a", "result-a")
    b = _OKAgent("b", "result-b")
    registry = AgentRegistry([a, b])
    agent, resp = await registry.run("hello")
    assert agent is a
    assert resp.text == "result-a"
    assert len(a.calls) == 1
    assert len(b.calls) == 0  # never tried


@pytest.mark.asyncio
async def test_falls_back_to_second_agent_on_first_failure():
    fail = _FailAgent("fail")
    ok = _OKAgent("ok", "success")
    registry = AgentRegistry([fail, ok])
    agent, resp = await registry.run("hello")
    assert agent is ok
    assert resp.text == "success"
    assert len(fail.calls) == 1
    assert len(ok.calls) == 1


@pytest.mark.asyncio
async def test_returns_last_error_when_all_fail():
    a = _FailAgent("a", "error-a")
    b = _FailAgent("b", "error-b")
    registry = AgentRegistry([a, b])
    agent, resp = await registry.run("hello")
    assert agent is b
    assert resp.error == "error-b"


@pytest.mark.asyncio
async def test_history_passed_through_to_agent():
    a = _OKAgent("a")
    registry = AgentRegistry([a])
    history = [{"role": "user", "content": "prev"}]
    await registry.run("current", history)
    assert a.calls[0][1] == history


@pytest.mark.asyncio
async def test_single_agent_success():
    a = _OKAgent("solo", "42")
    registry = AgentRegistry([a])
    agent, resp = await registry.run("q")
    assert agent is a
    assert resp.text == "42"
    assert resp.error is None


@pytest.mark.asyncio
async def test_workspace_override_passed_when_agent_supports_it(tmp_path):
    agent = _WorkspaceAgent("ws")
    registry = AgentRegistry([agent])
    workspace = tmp_path / "thread-1"
    await registry.run("q", thread_id="thread-1", workspace_override=workspace)
    assert len(agent.calls) == 1
    assert agent.calls[0][2] == "thread-1"
    assert agent.calls[0][3] == workspace
