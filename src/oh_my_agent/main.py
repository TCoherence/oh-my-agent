from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path


def _build_agent(name: str, cfg: dict):
    """Instantiate an agent from its config dict."""
    agent_type = cfg.get("type", "cli")

    if agent_type == "cli":
        provider = cfg.get("provider", name)
        if provider == "gemini":
            from oh_my_agent.agents.cli.gemini import GeminiCLIAgent
            return GeminiCLIAgent(
                cli_path=cfg.get("cli_path", "gemini"),
                model=cfg.get("model", "gemini-2.0-flash"),
            )
        else:
            # Default to claude for any unknown CLI type
            from oh_my_agent.agents.cli.claude import ClaudeAgent
            tools = cfg.get("allowed_tools", ["Bash", "Read", "Edit", "Glob", "Grep"])
            return ClaudeAgent(
                cli_path=cfg.get("cli_path", "claude"),
                max_turns=int(cfg.get("max_turns", 25)),
                allowed_tools=tools,
                model=cfg.get("model", "sonnet"),
            )

    if agent_type == "api":
        provider = cfg.get("provider", "")
        if provider == "openai":
            from oh_my_agent.agents.api.openai import OpenAIAPIAgent
            return OpenAIAPIAgent(
                api_key=cfg["api_key"],
                model=cfg.get("model", "gpt-4o"),
                max_tokens=int(cfg.get("max_tokens", 4096)),
            )
        else:
            # Default to anthropic
            from oh_my_agent.agents.api.anthropic import AnthropicAPIAgent
            return AnthropicAPIAgent(
                api_key=cfg["api_key"],
                model=cfg.get("model", "claude-sonnet-4-6"),
                max_tokens=int(cfg.get("max_tokens", 8192)),
            )

    raise ValueError(f"Unknown agent type '{agent_type}' for agent '{name}'")


def _build_channel(cfg: dict):
    """Instantiate a platform channel from its config dict."""
    platform = cfg["platform"]
    channel_id = str(cfg["channel_id"])

    if platform == "discord":
        from oh_my_agent.gateway.platforms.discord import DiscordChannel
        return DiscordChannel(token=cfg["token"], channel_id=channel_id)

    if platform == "slack":
        from oh_my_agent.gateway.platforms.slack import SlackChannel
        return SlackChannel(token=cfg["token"], channel_id=channel_id)

    raise ValueError(f"Unknown platform '{platform}'")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    # Locate config.yaml (next to cwd or project root)
    config_path = Path("config.yaml")
    if not config_path.exists():
        logger.error("config.yaml not found. Copy config.yaml.example and fill in your values.")
        sys.exit(1)

    try:
        from oh_my_agent.config import load_config
        config = load_config(config_path)
    except Exception as exc:
        logger.error("Failed to load config.yaml: %s", exc)
        sys.exit(1)

    # Build agent registry map
    agents_cfg: dict = config.get("agents", {})
    agent_instances: dict = {}
    for agent_name, agent_cfg in agents_cfg.items():
        try:
            agent_instances[agent_name] = _build_agent(agent_name, agent_cfg)
            logger.info("Loaded agent '%s' (%s)", agent_name, agent_cfg.get("type"))
        except Exception as exc:
            logger.error("Failed to build agent '%s': %s", agent_name, exc)
            sys.exit(1)

    # Build (channel, registry) pairs
    from oh_my_agent.agents.registry import AgentRegistry
    from oh_my_agent.gateway.manager import GatewayManager

    channel_pairs = []
    for ch_cfg in config.get("gateway", {}).get("channels", []):
        channel = _build_channel(ch_cfg)
        agent_names: list[str] = ch_cfg.get("agents", [])
        selected = []
        for name in agent_names:
            if name not in agent_instances:
                logger.error("Agent '%s' referenced in channel config but not defined", name)
                sys.exit(1)
            selected.append(agent_instances[name])
        if not selected:
            logger.error("Channel %s:%s has no agents configured", ch_cfg["platform"], ch_cfg["channel_id"])
            sys.exit(1)
        registry = AgentRegistry(selected)
        channel_pairs.append((channel, registry))
        logger.info(
            "Channel %s:%s → agents: %s",
            ch_cfg["platform"],
            ch_cfg["channel_id"],
            [a.name for a in selected],
        )

    if not channel_pairs:
        logger.error("No channels configured in config.yaml")
        sys.exit(1)

    gateway = GatewayManager(channel_pairs)
    logger.info("Starting gateway with %d channel(s)...", len(channel_pairs))
    asyncio.run(gateway.start())


if __name__ == "__main__":
    main()
