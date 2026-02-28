from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _setup_workspace(workspace_path: str, project_root: Path, skills_path: Path | None = None) -> Path:
    """Create and populate the agent workspace directory.

    Copies ``AGENT.md`` (resolved through symlinks) so CLI agents have project
    context without access to the full dev repo.  Also copies skills into
    ``.claude/skills/`` and ``.gemini/skills/`` under the workspace so that
    CLI-native skill discovery works from the new cwd.

    Args:
        workspace_path: Path string from config (may contain ``~``).
        project_root: The application's working directory (where config.yaml lives).
        skills_path: Canonical skills directory to copy into workspace.

    Returns:
        Resolved absolute ``Path`` to the workspace.
    """
    ws = Path(workspace_path).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)

    # Copy AGENT.md / CLAUDE.md / GEMINI.md so agents have project context.
    for filename in ("AGENT.md", "CLAUDE.md", "GEMINI.md"):
        src = project_root / filename
        if not src.exists():
            continue
        # Resolve symlinks so we copy the actual content, not a dangling link.
        resolved = src.resolve() if src.is_symlink() else src
        if resolved.exists():
            shutil.copy2(resolved, ws / filename)

    # Copy skills into workspace CLI directories (not symlinks — real copies).
    if skills_path and skills_path.is_dir():
        for cli_skills_dir in (ws / ".claude" / "skills", ws / ".gemini" / "skills"):
            cli_skills_dir.mkdir(parents=True, exist_ok=True)
            for skill_dir in skills_path.iterdir():
                if not skill_dir.is_dir():
                    continue
                resolved_skill = skill_dir.resolve() if skill_dir.is_symlink() else skill_dir
                if not (resolved_skill / "SKILL.md").exists():
                    continue
                dest = cli_skills_dir / skill_dir.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(resolved_skill, dest)

    return ws


def _build_agent(name: str, cfg: dict, workspace: Path | None = None):
    """Instantiate an agent from its config dict."""
    agent_type = cfg.get("type", "cli")

    passthrough_env: list[str] | None = cfg.get("env_passthrough")

    if agent_type == "cli":
        provider = cfg.get("provider", name)
        if provider == "gemini":
            from oh_my_agent.agents.cli.gemini import GeminiCLIAgent
            timeout = int(cfg.get("timeout", 120))
            return GeminiCLIAgent(
                cli_path=cfg.get("cli_path", "gemini"),
                model=cfg.get("model", "gemini-3-flash-preview"),
                timeout=timeout,
                workspace=workspace,
                passthrough_env=passthrough_env,
            )
        elif provider == "codex":
            from oh_my_agent.agents.cli.codex import CodexCLIAgent
            timeout = int(cfg.get("timeout", 300))
            return CodexCLIAgent(
                cli_path=cfg.get("cli_path", "codex"),
                model=cfg.get("model", "o4-mini"),
                skip_git_repo_check=bool(cfg.get("skip_git_repo_check", True)),
                timeout=timeout,
                workspace=workspace,
                passthrough_env=passthrough_env,
            )
        else:
            # Default to claude for any unknown CLI type
            from oh_my_agent.agents.cli.claude import ClaudeAgent
            timeout = int(cfg.get("timeout", 300))
            tools = cfg.get("allowed_tools", ["Bash", "Read", "Write", "Edit", "Glob", "Grep"])
            return ClaudeAgent(
                cli_path=cfg.get("cli_path", "claude"),
                max_turns=int(cfg.get("max_turns", 25)),
                allowed_tools=tools,
                model=cfg.get("model", "sonnet"),
                timeout=timeout,
                workspace=workspace,
                passthrough_env=passthrough_env,
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


def _build_channel(cfg: dict, *, owner_user_ids: set[str] | None = None):
    """Instantiate a platform channel from its config dict."""
    platform = cfg["platform"]
    channel_id = str(cfg["channel_id"])

    if platform == "discord":
        from oh_my_agent.gateway.platforms.discord import DiscordChannel
        return DiscordChannel(
            token=cfg["token"],
            channel_id=channel_id,
            owner_user_ids=owner_user_ids,
        )

    if platform == "slack":
        from oh_my_agent.gateway.platforms.slack import SlackChannel
        return SlackChannel(token=cfg["token"], channel_id=channel_id)

    raise ValueError(f"Unknown platform '{platform}'")


def _apply_v052_defaults(config: dict) -> None:
    config.setdefault("workspace", "~/.oh-my-agent/agent-workspace")
    short_ws_cfg = config.setdefault("short_workspace", {})
    short_ws_cfg.setdefault("enabled", True)
    short_ws_cfg.setdefault("ttl_hours", 24)
    short_ws_cfg.setdefault("cleanup_interval_minutes", 1440)
    short_ws_cfg.setdefault("root", "~/.oh-my-agent/agent-workspace/sessions")

    router_cfg = config.setdefault("router", {})
    router_cfg.setdefault("enabled", False)
    router_cfg.setdefault("provider", "openai_compatible")
    router_cfg.setdefault("base_url", "https://api.deepseek.com/v1")
    router_cfg.setdefault("api_key_env", "DEEPSEEK_API_KEY")
    router_cfg.setdefault("model", "deepseek-chat")
    router_cfg.setdefault("timeout_seconds", 8)
    router_cfg.setdefault("max_retries", 1)
    router_cfg.setdefault("confidence_threshold", 0.55)
    router_cfg.setdefault("require_user_confirm", True)

    memory_cfg = config.setdefault("memory", {})
    memory_cfg.setdefault("backend", "sqlite")
    memory_cfg.setdefault("path", "~/.oh-my-agent/runtime/memory.db")

    runtime_cfg = config.setdefault("runtime", {})
    runtime_cfg.setdefault("enabled", True)
    runtime_cfg.setdefault("worker_concurrency", 3)
    runtime_cfg.setdefault("worktree_root", "~/.oh-my-agent/runtime/tasks")
    runtime_cfg.setdefault("default_agent", "codex")
    runtime_cfg.setdefault("default_test_command", "pytest -q")
    runtime_cfg.setdefault("default_max_steps", 8)
    runtime_cfg.setdefault("default_max_minutes", 20)
    runtime_cfg.setdefault("risk_profile", "strict")
    runtime_cfg.setdefault("path_policy_mode", "allow_all_with_denylist")
    runtime_cfg.setdefault("denied_paths", [".env", "config.yaml", ".workspace/**", ".git/**"])
    runtime_cfg.setdefault("decision_ttl_minutes", 1440)
    runtime_cfg.setdefault("agent_heartbeat_seconds", 20)
    runtime_cfg.setdefault("test_heartbeat_seconds", 15)
    runtime_cfg.setdefault("test_timeout_seconds", 600)
    runtime_cfg.setdefault("progress_notice_seconds", 30)
    runtime_cfg.setdefault("progress_persist_seconds", 60)
    runtime_cfg.setdefault("log_event_limit", 12)
    runtime_cfg.setdefault("log_tail_chars", 1200)

    cleanup_cfg = runtime_cfg.setdefault("cleanup", {})
    cleanup_cfg.setdefault("enabled", True)
    cleanup_cfg.setdefault("interval_minutes", 60)
    cleanup_cfg.setdefault("retention_hours", 72)
    cleanup_cfg.setdefault("prune_git_worktrees", True)
    cleanup_cfg.setdefault("merged_immediate", True)

    merge_cfg = runtime_cfg.setdefault("merge_gate", {})
    merge_cfg.setdefault("enabled", True)
    merge_cfg.setdefault("auto_commit", True)
    merge_cfg.setdefault("require_clean_repo", True)
    merge_cfg.setdefault("preflight_check", True)
    merge_cfg.setdefault("target_branch_mode", "current")
    merge_cfg.setdefault("commit_message_template", "runtime(task:{task_id}): {goal_short}")


def _runtime_root(config: dict) -> Path:
    runtime_cfg = config.get("runtime", {})
    worktree_root = Path(runtime_cfg.get("worktree_root", "~/.oh-my-agent/runtime/tasks"))
    return worktree_root.expanduser().resolve().parent


def _maybe_move(src: Path, dst: Path) -> bool:
    if not src.exists() or dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


def _migrate_legacy_workspace(config: dict, project_root: Path, logger: logging.Logger) -> None:
    old_root = project_root / ".workspace"
    if not old_root.exists():
        return

    runtime_root = _runtime_root(config)
    marker = runtime_root / ".migration_v052_done"
    if marker.exists():
        return

    memory_path = Path(config.get("memory", {}).get("path", "~/.oh-my-agent/runtime/memory.db")).expanduser().resolve()
    worktree_root = Path(config.get("runtime", {}).get("worktree_root", "~/.oh-my-agent/runtime/tasks")).expanduser().resolve()
    workspace_path = Path(config.get("workspace", "~/.oh-my-agent/agent-workspace")).expanduser().resolve()
    logs_dir = runtime_root / "logs"

    targets = [memory_path, worktree_root, workspace_path, logs_dir]
    if all(p.exists() for p in targets):
        runtime_root.mkdir(parents=True, exist_ok=True)
        marker.write_text("skip: targets already exist\n", encoding="utf-8")
        return

    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = project_root / f".workspace.migrated.{ts}"
    shutil.move(str(old_root), str(backup))

    moved = 0
    moved += int(_maybe_move(backup / "memory.db", memory_path))
    moved += int(_maybe_move(backup / "memory.db-wal", memory_path.with_name(f"{memory_path.name}-wal")))
    moved += int(_maybe_move(backup / "memory.db-shm", memory_path.with_name(f"{memory_path.name}-shm")))
    moved += int(_maybe_move(backup / "tasks", worktree_root))
    moved += int(_maybe_move(backup / "agent", workspace_path))
    moved += int(_maybe_move(backup / "logs", logs_dir))

    runtime_root.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        f"migrated_from={backup}\nmoved_items={moved}\nat={datetime.now().isoformat()}\n",
        encoding="utf-8",
    )
    logger.info("Migrated legacy .workspace to external runtime root: %s", runtime_root)


def _setup_logging(runtime_root: Path | None = None) -> None:
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    # Console handler — always on
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(log_format))
    root.addHandler(console)

    # Rotating file handler — one file per day, keep 7 days.
    runtime_root = runtime_root or Path("~/.oh-my-agent/runtime").expanduser().resolve()
    log_dir = runtime_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
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

    owner_user_ids = {
        str(uid).strip()
        for uid in config.get("access", {}).get("owner_user_ids", [])
        if str(uid).strip()
    }
    if owner_user_ids:
        logger.info("Owner-only mode enabled for %d user(s)", len(owner_user_ids))

    # Setup workspace (Layer 0 sandbox isolation)
    project_root = Path.cwd()
    workspace: Path | None = None
    if config.get("workspace"):
        skills_cfg_for_ws = config.get("skills", {})
        skills_path_for_ws = (
            Path(skills_cfg_for_ws.get("path", "skills/")).resolve()
            if skills_cfg_for_ws.get("enabled")
            else None
        )
        workspace = _setup_workspace(str(config["workspace"]), project_root, skills_path_for_ws)
        logger.info("Workspace: %s", workspace)

    # Build agent registry map
    agents_cfg: dict = config.get("agents", {})
    agent_instances: dict = {}
    for agent_name, agent_cfg in agents_cfg.items():
        try:
            agent_instances[agent_name] = _build_agent(agent_name, agent_cfg, workspace=workspace)
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

        db_path = str(Path(memory_cfg.get("path", "~/.oh-my-agent/runtime/memory.db")).expanduser().resolve())
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
    skill_syncer = None
    workspace_skills_dirs = None
    if skills_cfg.get("enabled", False):
        from oh_my_agent.skills.skill_sync import SkillSync

        skills_path = skills_cfg.get("path", "skills/")
        skill_syncer = SkillSync(skills_path)

        if workspace is not None:
            workspace_skills_dirs = [
                workspace / ".claude" / "skills",
                workspace / ".gemini" / "skills",
            ]

        forward, reverse = skill_syncer.full_sync(extra_source_dirs=workspace_skills_dirs)
        logger.info("Skills: %d synced, %d reverse-imported", forward, reverse)

    # Build scheduler (optional)
    from oh_my_agent.automation import build_scheduler_from_config

    try:
        default_target_user_id = sorted(owner_user_ids)[0] if owner_user_ids else None
        scheduler = build_scheduler_from_config(
            config,
            default_target_user_id=default_target_user_id,
        )
        if scheduler:
            logger.info("Loaded scheduler with %d job(s)", len(scheduler.jobs))
    except Exception as exc:
        logger.error("Failed to build scheduler from config: %s", exc)
        sys.exit(1)

    # Build runtime service (optional, defaults enabled when memory store exists)
    runtime_cfg = config.get("runtime", {"enabled": True})
    runtime_service = None
    if memory_store and bool(runtime_cfg.get("enabled", True)):
        from oh_my_agent.runtime import RuntimeService

        runtime_service = RuntimeService(
            memory_store,
            config=runtime_cfg,
            owner_user_ids=owner_user_ids,
            repo_root=project_root,
            skill_syncer=skill_syncer,
            skills_path=(Path(skills_cfg.get("path", "skills/")).resolve() if skills_cfg.get("enabled", False) else None),
        )
        logger.info(
            "Runtime enabled (workers=%s, default_agent=%s)",
            runtime_cfg.get("worker_concurrency", 3),
            runtime_cfg.get("default_agent", "codex"),
        )
    elif not memory_store:
        logger.warning("Runtime disabled: memory backend is required.")

    intent_router = None
    router_cfg = config.get("router", {})
    if bool(router_cfg.get("enabled", False)):
        if str(router_cfg.get("provider", "openai_compatible")) != "openai_compatible":
            logger.warning("Unsupported router provider: %s", router_cfg.get("provider"))
        else:
            api_key = os.environ.get(str(router_cfg.get("api_key_env", "DEEPSEEK_API_KEY")), "").strip()
            if not api_key:
                logger.warning(
                    "Router enabled but API key env %s is empty; router disabled.",
                    router_cfg.get("api_key_env", "DEEPSEEK_API_KEY"),
                )
            else:
                from oh_my_agent.gateway.router import OpenAICompatibleRouter

                intent_router = OpenAICompatibleRouter(
                    base_url=str(router_cfg.get("base_url", "https://api.deepseek.com/v1")),
                    api_key=api_key,
                    model=str(router_cfg.get("model", "deepseek-chat")),
                    timeout_seconds=int(router_cfg.get("timeout_seconds", 8)),
                    confidence_threshold=float(router_cfg.get("confidence_threshold", 0.55)),
                    max_retries=int(router_cfg.get("max_retries", 1)),
                )
                logger.info(
                    "Intent router enabled model=%s base=%s threshold=%.2f timeout=%ss retries=%s",
                    router_cfg.get("model", "deepseek-chat"),
                    router_cfg.get("base_url", "https://api.deepseek.com/v1"),
                    float(router_cfg.get("confidence_threshold", 0.55)),
                    int(router_cfg.get("timeout_seconds", 8)),
                    int(router_cfg.get("max_retries", 1)),
                )

    # Build (channel, registry) pairs
    from oh_my_agent.agents.registry import AgentRegistry
    from oh_my_agent.gateway.manager import GatewayManager

    channel_pairs = []
    for ch_cfg in config.get("gateway", {}).get("channels", []):
        channel = _build_channel(ch_cfg, owner_user_ids=owner_user_ids)
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

    gateway = GatewayManager(
        channel_pairs,
        compressor=compressor,
        scheduler=scheduler,
        owner_user_ids=owner_user_ids,
        skill_syncer=skill_syncer,
        workspace_skills_dirs=workspace_skills_dirs,
        runtime_service=runtime_service,
        short_workspace={
            **config.get("short_workspace", {}),
            "base_workspace": str(workspace) if workspace is not None else None,
        },
        intent_router=intent_router,
    )
    if memory_store:
        gateway.set_memory_store(memory_store)

    logger.info("Starting gateway with %d channel(s)...", len(channel_pairs))
    try:
        await gateway.start()
    finally:
        if memory_store:
            await memory_store.close()


def main() -> None:
    # Locate config.yaml (next to cwd or project root)
    config_path = Path("config.yaml")
    if not config_path.exists():
        _setup_logging()
        logger = logging.getLogger(__name__)
        logger.error("config.yaml not found. Copy config.yaml.example and fill in your values.")
        sys.exit(1)

    try:
        from oh_my_agent.config import load_config
        config = load_config(config_path)
    except Exception as exc:
        _setup_logging()
        logger = logging.getLogger(__name__)
        logger.error("Failed to load config.yaml: %s", exc)
        sys.exit(1)

    _apply_v052_defaults(config)
    runtime_root = _runtime_root(config)
    _setup_logging(runtime_root)
    logger = logging.getLogger(__name__)
    _migrate_legacy_workspace(config, Path.cwd(), logger)

    asyncio.run(_async_main(config, logger))


if __name__ == "__main__":
    main()
