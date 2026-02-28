from __future__ import annotations

import logging

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

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        force_agent: str | None = None,
        workspace_override=None,
        log_path=None,
    ) -> tuple[BaseAgent, AgentResponse]:
        """Try each agent in order. Return the first successful (agent, response) pair.

        If all agents fail, returns the last agent and its error response.

        *thread_id* is forwarded to agents that accept it (e.g. for session resume).
        *force_agent* bypasses fallback and runs only the named agent.
        """
        if force_agent is not None:
            agent = self.get_agent(force_agent)
            if agent is None:
                names = [a.name for a in self._agents]
                return self._agents[0], AgentResponse(
                    text="",
                    error=f"Agent '{force_agent}' not found. Available: {names}",
                )
            import inspect
            sig = inspect.signature(agent.run)
            kwargs = {}
            if "thread_id" in sig.parameters:
                kwargs["thread_id"] = thread_id
            if "workspace_override" in sig.parameters:
                kwargs["workspace_override"] = workspace_override
            if "log_path" in sig.parameters:
                kwargs["log_path"] = log_path
            response = await agent.run(prompt, history, **kwargs)
            return agent, response

        last_agent = self._agents[-1]
        last_response = AgentResponse(text="", error="No agents available")

        for agent in self._agents:
            logger.info("Trying agent '%s'", agent.name)
            # Pass thread_id to agents that support it (e.g. ClaudeAgent)
            import inspect
            sig = inspect.signature(agent.run)
            kwargs = {}
            if "thread_id" in sig.parameters:
                kwargs["thread_id"] = thread_id
            if "workspace_override" in sig.parameters:
                kwargs["workspace_override"] = workspace_override
            if "log_path" in sig.parameters:
                kwargs["log_path"] = log_path
            response = await agent.run(prompt, history, **kwargs)
            if not response.error:
                return agent, response
            logger.warning(
                "Agent '%s' failed: %s — trying next", agent.name, response.error
            )
            last_agent = agent
            last_response = response

        logger.error("All agents failed. Last error: %s", last_response.error)
        return last_agent, last_response
