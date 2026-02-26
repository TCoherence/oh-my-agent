from __future__ import annotations

import logging
import sys


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    try:
        from oh_my_agent.config import Config

        config = Config.from_env()
    except KeyError as exc:
        logger.error("Missing required environment variable: %s", exc)
        sys.exit(1)

    from oh_my_agent.agents.claude import ClaudeAgent

    agent = ClaudeAgent(
        max_turns=config.claude_max_turns,
        allowed_tools=config.claude_allowed_tools,
        model=config.claude_model,
    )

    from oh_my_agent.bot import AgentBot

    bot = AgentBot(config=config, agent=agent)

    logger.info("Starting bot with agent '%s'...", agent.name)
    bot.run(config.discord_bot_token, log_handler=None)


if __name__ == "__main__":
    main()
