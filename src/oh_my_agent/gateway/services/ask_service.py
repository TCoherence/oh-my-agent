from __future__ import annotations

from typing import TYPE_CHECKING

from oh_my_agent.gateway.services.types import ServiceResult

if TYPE_CHECKING:
    from oh_my_agent.agents.registry import AgentRegistry
    from oh_my_agent.gateway.session import ChannelSession


class AskService:
    """Auxiliary service for ask-related slash commands.

    The core ask flow remains in GatewayManager.handle_message(); this service
    only handles validation, history reset, history formatting, and agent list
    assembly.
    """

    async def validate_ask_params(self, registry: AgentRegistry | None, agent_name: str | None) -> str | None:
        if not agent_name or registry is None:
            return None
        if registry.get_agent(agent_name) is not None:
            return None
        names = [agent.name for agent in registry.agents]
        return f"Unknown agent `{agent_name}`. Available: {', '.join(f'`{name}`' for name in names)}"

    async def reset_history(self, session: ChannelSession | None, thread_id: str) -> ServiceResult:
        if session is None:
            return ServiceResult(success=False, message="No session available.")
        await session.clear_history(thread_id)
        return ServiceResult(success=True, message="History cleared for this thread.")

    async def get_history(self, session: ChannelSession | None, thread_id: str) -> ServiceResult:
        if session is None:
            return ServiceResult(success=False, message="No session available.")
        history = await session.get_history(thread_id)
        if not history:
            return ServiceResult(success=True, message="No history for this thread yet.")
        lines = [f"**Thread history** — {len(history)} turns:"]
        for idx, turn in enumerate(history, 1):
            role = turn.get("role", "?")
            label = turn.get("author") or turn.get("agent") or role
            content = str(turn.get("content", ""))
            preview = content[:120] + ("…" if len(content) > 120 else "")
            lines.append(f"`{idx}` **{label}** [{role}]: {preview}")
        return ServiceResult(success=True, message="\n".join(lines)[:2000])

    async def list_agents(self, registry: AgentRegistry | None) -> ServiceResult:
        if registry is None:
            return ServiceResult(success=False, message="No agents configured.")
        lines = ["**Available agents** (in fallback order):"]
        for idx, agent in enumerate(registry.agents, 1):
            lines.append(f"{idx}. `{agent.name}`")
        return ServiceResult(success=True, message="\n".join(lines))
