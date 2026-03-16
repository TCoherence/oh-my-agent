from __future__ import annotations

from contextlib import contextmanager
import logging
from pathlib import Path
import re

from oh_my_agent.agents.base import AgentResponse, BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Ordered list of agents with automatic fallback on error."""

    def __init__(self, agents: list[BaseAgent]) -> None:
        if not agents:
            raise ValueError("AgentRegistry requires at least one agent")
        self._agents = agents

    @property
    def agents(self) -> list[BaseAgent]:
        return list(self._agents)

    def get_agent(self, name: str) -> BaseAgent | None:
        """Return the agent with the given name, or None if not found."""
        return next((a for a in self._agents if a.name == name), None)

    @staticmethod
    def _agent_log_path(log_path: Path | None, agent_name: str) -> Path | None:
        if log_path is None:
            return None
        safe_agent = re.sub(r"[^A-Za-z0-9_.-]+", "-", agent_name).strip("-") or "agent"
        suffix = log_path.suffix or ".log"
        return log_path.with_name(f"{log_path.stem}-{safe_agent}{suffix}")

    @staticmethod
    @contextmanager
    def _temporary_timeout(agent: BaseAgent, timeout_override_seconds: int | None):
        if timeout_override_seconds is None or not hasattr(agent, "_timeout"):
            yield
            return
        try:
            override = int(timeout_override_seconds)
        except (TypeError, ValueError):
            yield
            return
        if override <= 0:
            yield
            return
        original = getattr(agent, "_timeout")
        setattr(agent, "_timeout", override)
        try:
            yield
        finally:
            setattr(agent, "_timeout", original)

    async def _run_single_agent(
        self,
        agent: BaseAgent,
        prompt: str,
        history: list[dict] | None,
        *,
        thread_id: str | None,
        workspace_override,
        log_path,
        image_paths: list[Path] | None,
        timeout_override_seconds: int | None,
    ) -> AgentResponse:
        import inspect

        sig = inspect.signature(agent.run)
        kwargs = {}
        if "thread_id" in sig.parameters:
            kwargs["thread_id"] = thread_id
        if "workspace_override" in sig.parameters:
            kwargs["workspace_override"] = workspace_override
        if "log_path" in sig.parameters:
            kwargs["log_path"] = self._agent_log_path(log_path, agent.name)
        if "image_paths" in sig.parameters:
            kwargs["image_paths"] = image_paths
        with self._temporary_timeout(agent, timeout_override_seconds):
            return await agent.run(prompt, history, **kwargs)

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        force_agent: str | None = None,
        workspace_override=None,
        log_path=None,
        image_paths: list[Path] | None = None,
        run_label: str | None = None,
        timeout_override_seconds: int | None = None,
    ) -> tuple[BaseAgent, AgentResponse]:
        """Try each agent in order. Return the first successful (agent, response) pair.

        If all agents fail, returns the last agent and its error response.

        *thread_id* is forwarded to agents that accept it (e.g. for session resume).
        *force_agent* bypasses fallback and runs only the named agent.
        """
        label_suffix = f" [{run_label}]" if run_label else ""

        if force_agent is not None:
            agent = self.get_agent(force_agent)
            if agent is None:
                names = [a.name for a in self._agents]
                return self._agents[0], AgentResponse(
                    text="",
                    error=f"Agent '{force_agent}' not found. Available: {names}",
                )
            response = await self._run_single_agent(
                agent,
                prompt,
                history,
                thread_id=thread_id,
                workspace_override=workspace_override,
                log_path=log_path,
                image_paths=image_paths,
                timeout_override_seconds=timeout_override_seconds,
            )
            return agent, response

        last_agent = self._agents[-1]
        last_response = AgentResponse(text="", error="No agents available")

        for agent in self._agents:
            logger.info("Trying agent '%s'%s", agent.name, label_suffix)
            response = await self._run_single_agent(
                agent,
                prompt,
                history,
                thread_id=thread_id,
                workspace_override=workspace_override,
                log_path=log_path,
                image_paths=image_paths,
                timeout_override_seconds=timeout_override_seconds,
            )
            if not response.error:
                return agent, response
            logger.warning(
                "Agent '%s'%s failed: %s — trying next", agent.name, label_suffix, response.error
            )
            last_agent = agent
            last_response = response

        logger.error("All agents failed%s. Last error: %s", label_suffix, last_response.error)
        return last_agent, last_response
