from __future__ import annotations

import asyncio
import logging
import logging.handlers
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
                model=cfg.get("model", "gemini-3-flash-preview"),
            )
        elif provider == "codex":
            from oh_my_agent.agents.cli.codex import CodexCLIAgent
            return CodexCLIAgent(
                cli_path=cfg.get("cli_path", "codex"),
                model=cfg.get("model", "o4-mini"),
            )
        else:
            # Default to claude for any unknown CLI type
            from oh_my_agent.agents.cli.claude import ClaudeAgent
            tools = cfg.get("allowed_tools", ["Bash", "Read", "Write", "Edit", "Glob", "Grep"])
            return ClaudeAgent(
                cli_path=cfg.get("cli_path", "claude"),
                max_turns=int(cfg.get("max_turns", 25)),
                allowed_tools=tools,
                model=cfg.get("model", "sonnet"),
            )

    if agent_type == "api":
        import warnings
        warnings.warn(
            f"API agent '{name}' is deprecated since v0.4.0. "
            "Use CLI agents instead. API agent support will be removed in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
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


def _setup_logging() -> None:
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Console handler — always on
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(log_format))
    root.addHandler(console)

    # Rotating file handler — one file per day, keep 7 days
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / "oh-my-agent.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    root.addHandler(file_handler)


async def _async_main(config: dict, logger: logging.Logger) -> None:
    """Async entry point — builds agents, memory, and starts gateway."""

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

    # Build memory store
    memory_cfg = config.get("memory", {})
    memory_store = None
    compressor = None

    if memory_cfg.get("backend", "sqlite") == "sqlite":
        from oh_my_agent.memory.store import SQLiteMemoryStore
        from oh_my_agent.memory.compressor import HistoryCompressor

        db_path = memory_cfg.get("path", "data/memory.db")
        memory_store = SQLiteMemoryStore(db_path)
        await memory_store.init()
        logger.info("Memory store ready: %s", db_path)

        compressor = HistoryCompressor(
            store=memory_store,
            max_turns=int(memory_cfg.get("max_turns", 20)),
            summary_max_chars=int(memory_cfg.get("summary_max_chars", 500)),
        )

    # Sync skills
    skills_cfg = config.get("skills", {})
    if skills_cfg.get("enabled", False):
        from oh_my_agent.skills.skill_sync import SkillSync

        skills_path = skills_cfg.get("path", "skills/")
        syncer = SkillSync(skills_path)
        forward, reverse = syncer.full_sync()
        logger.info("Skills: %d synced, %d reverse-imported", forward, reverse)

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

    gateway = GatewayManager(channel_pairs, compressor=compressor)
    if memory_store:
        gateway.set_memory_store(memory_store)

    logger.info("Starting gateway with %d channel(s)...", len(channel_pairs))
    try:
        await gateway.start()
    finally:
        if memory_store:
            await memory_store.close()


def main() -> None:
    _setup_logging()
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

    asyncio.run(_async_main(config, logger))


if __name__ == "__main__":
    main()
