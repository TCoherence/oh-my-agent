"""Bootloader for oh-my-agent.

Split into two phases:

1. :func:`verify_integrity` — synchronous: resolve config path, load + normalise
   config, validate, set up logging, run one-shot legacy-workspace migration.
   Returns a :class:`BootContext` bundle the caller can inspect.
2. :func:`ignite` — asynchronous: construct agents, memory stores, scheduler,
   runtime service, gateway — and run until shutdown.

``main.py`` is a thin argparse shim that calls these in sequence. The split
mirrors Agentara's "verify first, ignite later" pattern so the heavy async
kernel is only reached once config + disk layout are known-good.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class BootContext:
    """Pre-ignite configuration bundle produced by :func:`verify_integrity`."""

    config: dict
    config_path: Path
    project_root: Path
    runtime_root: Path
    logger: logging.Logger


# ---------------------------------------------------------------------------
# Builders (ignite phase)
# ---------------------------------------------------------------------------


def _setup_workspace(
    workspace_path: str,
    project_root: Path,
    skills_path: Path | None = None,
) -> Path:
    """Create and populate the agent workspace directory."""
    ws = Path(workspace_path).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)

    try:
        from oh_my_agent.skills.skill_sync import SkillSync

        syncer = SkillSync(skills_path or (project_root / "skills"), project_root=project_root)
        syncer.refresh_workspace(ws)
    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to refresh workspace contents for %s",
            ws,
            exc_info=True,
        )

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
                yolo=bool(cfg.get("yolo", True)),
                extra_args=cfg.get("extra_args"),
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
                sandbox_mode=str(cfg.get("sandbox_mode", "workspace-write")),
                dangerously_bypass_approvals_and_sandbox=bool(
                    cfg.get("dangerously_bypass_approvals_and_sandbox", False)
                ),
                extra_args=cfg.get("extra_args"),
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
                dangerously_skip_permissions=bool(cfg.get("dangerously_skip_permissions", True)),
                permission_mode=cfg.get("permission_mode"),
                extra_args=cfg.get("extra_args"),
                timeout=timeout,
                workspace=workspace,
                passthrough_env=passthrough_env,
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

    raise ValueError(f"Unknown platform '{platform}'")


# ---------------------------------------------------------------------------
# Config normalization (verify phase)
# ---------------------------------------------------------------------------


def _apply_v052_defaults(config: dict) -> None:
    skills_cfg = config.setdefault("skills", {})
    skills_cfg.setdefault("enabled", True)
    skills_cfg.setdefault("path", "skills/")
    skills_cfg.setdefault("telemetry_path", "~/.oh-my-agent/runtime/skills.db")
    skill_eval_cfg = skills_cfg.setdefault("evaluation", {})
    skill_eval_cfg.setdefault("enabled", True)
    skill_eval_cfg.setdefault("stats_recent_days", 7)
    skill_eval_cfg.setdefault("feedback_emojis", ["👍", "👎"])
    auto_disable_cfg = skill_eval_cfg.setdefault("auto_disable", {})
    auto_disable_cfg.setdefault("enabled", True)
    auto_disable_cfg.setdefault("rolling_window", 20)
    auto_disable_cfg.setdefault("min_invocations", 5)
    auto_disable_cfg.setdefault("failure_rate_threshold", 0.60)
    overlap_cfg = skill_eval_cfg.setdefault("overlap_guard", {})
    overlap_cfg.setdefault("enabled", True)
    overlap_cfg.setdefault("review_similarity_threshold", 0.45)
    source_cfg = skill_eval_cfg.setdefault("source_grounded", {})
    source_cfg.setdefault("enabled", True)
    source_cfg.setdefault("block_auto_merge", True)

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
    router_cfg.setdefault("timeout_seconds", 15)
    router_cfg.setdefault("max_retries", 1)
    router_cfg.setdefault("confidence_threshold", 0.55)
    router_cfg.setdefault("autonomy_threshold", 0.90)
    router_cfg.setdefault("context_turns", 10)
    router_cfg.setdefault("require_user_confirm", True)
    router_cfg.setdefault("extra_body", {})

    automations_cfg = config.setdefault("automations", {})
    automations_cfg.setdefault("enabled", True)
    automations_cfg.setdefault("storage_dir", "~/.oh-my-agent/automations")
    automations_cfg.setdefault("reload_interval_seconds", 5)
    automations_cfg.setdefault("timezone", "local")

    memory_cfg = config.setdefault("memory", {})
    memory_cfg.setdefault("backend", "sqlite")
    memory_cfg.setdefault("path", "~/.oh-my-agent/runtime/memory.db")

    auth_cfg = config.setdefault("auth", {})
    auth_cfg.setdefault("enabled", True)
    auth_cfg.setdefault("storage_root", "~/.oh-my-agent/runtime/auth")
    auth_cfg.setdefault("qr_poll_interval_seconds", 3)
    auth_cfg.setdefault("qr_default_timeout_seconds", 180)
    auth_providers_cfg = auth_cfg.setdefault("providers", {})
    auth_bili_cfg = auth_providers_cfg.setdefault("bilibili", {})
    auth_bili_cfg.setdefault("enabled", True)
    auth_bili_cfg.setdefault("scope_key", "default")

    agents_cfg = config.setdefault("agents", {})
    claude_cfg = agents_cfg.setdefault("claude", {})
    claude_cfg.setdefault("dangerously_skip_permissions", False)
    claude_cfg.setdefault("permission_mode", None)
    claude_cfg.setdefault("extra_args", [])

    gemini_cfg = agents_cfg.setdefault("gemini", {})
    gemini_cfg.setdefault("yolo", True)
    gemini_cfg.setdefault("extra_args", [])

    codex_cfg = agents_cfg.setdefault("codex", {})
    codex_cfg.setdefault("sandbox_mode", "workspace-write")
    codex_cfg.setdefault("dangerously_bypass_approvals_and_sandbox", False)
    codex_cfg.setdefault("extra_args", [])

    runtime_cfg = config.setdefault("runtime", {})
    runtime_cfg.setdefault("enabled", True)
    runtime_cfg.setdefault("state_path", "~/.oh-my-agent/runtime/runtime.db")
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
    cleanup_cfg.setdefault("retention_hours", 168)
    cleanup_cfg.setdefault("prune_git_worktrees", True)
    cleanup_cfg.setdefault("merged_immediate", True)

    merge_cfg = runtime_cfg.setdefault("merge_gate", {})
    merge_cfg.setdefault("enabled", True)
    merge_cfg.setdefault("auto_commit", True)
    merge_cfg.setdefault("require_clean_repo", True)
    merge_cfg.setdefault("preflight_check", True)
    merge_cfg.setdefault("target_branch_mode", "current")
    merge_cfg.setdefault("commit_message_template", "runtime(task:{task_id}): {goal_short}")


def _parse_env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean env for {name}: {raw!r}")


def _apply_agent_env_overrides(config: dict) -> None:
    agents_cfg = config.setdefault("agents", {})

    claude_cfg = agents_cfg.setdefault("claude", {})
    claude_skip = _parse_env_bool("OMA_AGENT_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS")
    if claude_skip is not None:
        claude_cfg["dangerously_skip_permissions"] = claude_skip
    if "OMA_AGENT_CLAUDE_PERMISSION_MODE" in os.environ:
        value = os.environ.get("OMA_AGENT_CLAUDE_PERMISSION_MODE", "").strip()
        claude_cfg["permission_mode"] = value or None

    gemini_cfg = agents_cfg.setdefault("gemini", {})
    gemini_yolo = _parse_env_bool("OMA_AGENT_GEMINI_YOLO")
    if gemini_yolo is not None:
        gemini_cfg["yolo"] = gemini_yolo

    codex_cfg = agents_cfg.setdefault("codex", {})
    if "OMA_AGENT_CODEX_SANDBOX_MODE" in os.environ:
        codex_cfg["sandbox_mode"] = os.environ["OMA_AGENT_CODEX_SANDBOX_MODE"].strip()
    codex_bypass = _parse_env_bool("OMA_AGENT_CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX")
    if codex_bypass is not None:
        codex_cfg["dangerously_bypass_approvals_and_sandbox"] = codex_bypass


def _runtime_root(config: dict) -> Path:
    runtime_cfg = config.get("runtime", {})
    worktree_root = Path(runtime_cfg.get("worktree_root", "~/.oh-my-agent/runtime/tasks"))
    return worktree_root.expanduser().resolve().parent


def _resolve_project_path(path_value: str | Path, project_root: Path) -> Path:
    raw_path = Path(path_value).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve()
    return (project_root / raw_path).resolve()


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


def _setup_logging(
    config: dict | None = None,
    runtime_root: Path | None = None,
) -> None:
    """Thin wrapper that delegates to ``logging_setup.setup_logging``."""
    from oh_my_agent.logging_setup import setup_logging

    setup_logging(config, runtime_root=runtime_root)


def _register_shutdown_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    on_signal,
    logger: logging.Logger,
) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, on_signal, sig)
        except NotImplementedError:
            logger.debug("Signal handlers unsupported for %s on this platform.", sig.name)


async def _shutdown(
    gateway_manager,
    scheduler,
    runtime_service,
    memory_store,
    logger: logging.Logger,
    *,
    reason: str,
    diary_writer=None,
) -> None:
    logger.info("Shutdown started reason=%s", reason)
    if scheduler:
        with suppress(Exception):
            scheduler.stop()
    if gateway_manager:
        await gateway_manager.stop()
    if runtime_service:
        await runtime_service.stop()
    if memory_store:
        await memory_store.close()
    if diary_writer is not None:
        with suppress(Exception):
            await diary_writer.stop()
    logger.info("Shutdown complete reason=%s", reason)


def _warn_if_legacy_memory_config(memory_cfg: dict, logger: logging.Logger) -> bool:
    """Emit a deprecation warning when ``memory.adaptive`` is used as fallback."""
    if "judge" in memory_cfg:
        return False
    if not isinstance(memory_cfg.get("adaptive"), dict):
        return False
    logger.warning(
        "memory.adaptive is deprecated; rename to memory.judge in config.yaml "
        "(falling back for now)."
    )
    return True


def _warn_if_legacy_memory_layout(memory_dir: Path, logger: logging.Logger) -> bool:
    """Emit a warning when the v0.8 daily/curated tier layout is detected."""
    legacy_curated = memory_dir / "curated.yaml"
    legacy_daily = memory_dir / "daily"
    if not legacy_curated.exists() and not legacy_daily.is_dir():
        return False
    logger.warning(
        "Legacy memory layout detected at %s; "
        "run `python scripts/migrate_memory_to_judge.py %s` to migrate to "
        "the single-tier JudgeStore (memories.yaml).",
        memory_dir,
        memory_dir,
    )
    return True


# ---------------------------------------------------------------------------
# Verify phase
# ---------------------------------------------------------------------------


def verify_integrity(
    config_path_raw: str | None = None,
    *,
    validate_only: bool = False,
) -> BootContext:
    """Resolve config, normalise it, validate, set up logging, migrate legacy state.

    Exits the process on fatal errors (missing config, validation failure).
    Returns a :class:`BootContext` that :func:`ignite` consumes.
    """
    # Resolve config path: explicit arg > OMA_CONFIG_PATH env > default
    if config_path_raw:
        config_path_str = config_path_raw
    else:
        config_path_str = os.environ.get("OMA_CONFIG_PATH", "config.yaml").strip() or "config.yaml"
    config_path = Path(config_path_str).expanduser()
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()

    if not config_path.exists():
        _setup_logging()
        logger = logging.getLogger(__name__)
        logger.error(
            "Config file not found at %s. Set OMA_CONFIG_PATH or provide config.yaml.",
            config_path,
        )
        sys.exit(1)

    try:
        from oh_my_agent.config import load_config
        config = load_config(config_path)
    except Exception as exc:
        _setup_logging()
        logger = logging.getLogger(__name__)
        logger.error("Failed to load config %s: %s", config_path, exc)
        sys.exit(1)

    _apply_v052_defaults(config)
    _apply_agent_env_overrides(config)

    from oh_my_agent.config_validator import validate_config
    validation = validate_config(config)

    if validate_only:
        print(validation.summary())
        sys.exit(0 if validation.ok else 1)

    runtime_root = _runtime_root(config)
    _setup_logging(config, runtime_root)
    logger = logging.getLogger(__name__)

    if not validation.ok:
        logger.error("Config validation failed:\n%s", validation.summary())
        sys.exit(1)
    elif validation.errors:
        logger.warning("Config validation warnings:\n%s", validation.summary())

    project_root = config_path.parent.resolve()
    _migrate_legacy_workspace(config, project_root, logger)

    return BootContext(
        config=config,
        config_path=config_path,
        project_root=project_root,
        runtime_root=runtime_root,
        logger=logger,
    )


# ---------------------------------------------------------------------------
# Ignite phase (async)
# ---------------------------------------------------------------------------


async def ignite(ctx: BootContext) -> None:
    """Async entry point — builds agents, memory, and starts gateway.

    This is the ``_igniteKernel`` equivalent: once verify has produced a valid
    ``BootContext``, this function owns the full lifecycle (construction →
    runtime loop → orderly shutdown).
    """
    config = ctx.config
    logger = ctx.logger
    project_root = ctx.project_root

    owner_user_ids = {
        str(uid).strip()
        for uid in config.get("access", {}).get("owner_user_ids", [])
        if str(uid).strip()
    }
    if owner_user_ids:
        logger.info("Owner-only mode enabled for %d user(s)", len(owner_user_ids))

    # Setup workspace (Layer 0 sandbox isolation)
    workspace: Path | None = None
    if config.get("workspace"):
        skills_cfg_for_ws = config.get("skills", {})
        skills_path_for_ws = (
            _resolve_project_path(skills_cfg_for_ws.get("path", "skills/"), project_root)
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
        from oh_my_agent.memory.store import SplitSQLiteMemoryStore, maybe_split_legacy_memory_db
        from oh_my_agent.memory.compressor import HistoryCompressor

        conversation_db_path = Path(memory_cfg.get("path", "~/.oh-my-agent/runtime/memory.db")).expanduser().resolve()
        runtime_db_path = Path(config.get("runtime", {}).get("state_path", "~/.oh-my-agent/runtime/runtime.db")).expanduser().resolve()
        skills_db_path = Path(config.get("skills", {}).get("telemetry_path", "~/.oh-my-agent/runtime/skills.db")).expanduser().resolve()

        await maybe_split_legacy_memory_db(
            memory_path=conversation_db_path,
            runtime_state_path=runtime_db_path,
            skills_telemetry_path=skills_db_path,
            logger=logger,
        )

        memory_store = SplitSQLiteMemoryStore(
            conversation_path=conversation_db_path,
            runtime_state_path=runtime_db_path,
            skills_telemetry_path=skills_db_path,
        )
        await memory_store.init()
        logger.info(
            "Memory stores ready: conversation=%s runtime=%s skills=%s",
            conversation_db_path,
            runtime_db_path,
            skills_db_path,
        )

        compressor = HistoryCompressor(
            store=memory_store,
            max_turns=int(memory_cfg.get("max_turns", 20)),
            summary_max_chars=int(memory_cfg.get("summary_max_chars", 500)),
        )

    # Build judge-driven memory (optional, replaces legacy adaptive memory)
    judge_store = None
    memory_judge = None
    idle_tracker = None
    _warn_if_legacy_memory_config(memory_cfg, logger)
    memory_cfg_block = memory_cfg.get("judge", memory_cfg.get("adaptive", {}))
    memory_inject_limit = int(memory_cfg_block.get("inject_limit", 12))
    memory_keyword_patterns = memory_cfg_block.get("keyword_patterns") or None
    if memory_cfg_block.get("enabled", False):
        from oh_my_agent.memory.judge_store import JudgeStore
        from oh_my_agent.memory.judge import Judge
        from oh_my_agent.memory.idle_trigger import IdleTracker

        memory_dir = str(
            Path(memory_cfg_block.get("memory_dir", "~/.oh-my-agent/memory")).expanduser().resolve()
        )
        _warn_if_legacy_memory_layout(Path(memory_dir), logger)
        judge_store = JudgeStore(
            memory_dir=memory_dir,
            synthesize_after_seconds=int(memory_cfg_block.get("synthesize_after_seconds", 6 * 3600)),
            max_evidence_per_entry=int(memory_cfg_block.get("max_evidence_per_entry", 8)),
        )
        await judge_store.load()
        memory_judge = Judge(judge_store)
        idle_seconds = float(memory_cfg_block.get("idle_seconds", 15 * 60))
        poll_interval = float(memory_cfg_block.get("idle_poll_seconds", 60))
        idle_tracker = IdleTracker(
            on_fire=lambda *_args, **_kwargs: asyncio.sleep(0),  # placeholder, manager rebinds
            idle_seconds=idle_seconds,
            poll_interval_seconds=poll_interval,
        )
        logger.info(
            "Judge memory enabled: %s active=%d idle=%ss",
            memory_dir,
            judge_store.stats()["active"],
            int(idle_seconds),
        )

    # Sync skills
    skills_cfg = config.get("skills", {})
    skill_syncer = None
    workspace_skills_dirs = None
    if skills_cfg.get("enabled", False):
        from oh_my_agent.skills.skill_sync import SkillSync

        skills_path = _resolve_project_path(skills_cfg.get("path", "skills/"), project_root)
        skill_syncer = SkillSync(skills_path, project_root=project_root)

        if workspace is not None:
            workspace_skills_dirs = [
                workspace / ".claude" / "skills",
                workspace / ".gemini" / "skills",
                workspace / ".agents" / "skills",
            ]

        forward, reverse = skill_syncer.full_sync(extra_source_dirs=workspace_skills_dirs)
        skill_syncer.refresh_workspace_dirs(workspace_skills_dirs)
        logger.info("Skills: %d synced, %d reverse-imported", forward, reverse)

    # Build scheduler (optional)
    from oh_my_agent.automation import build_scheduler_from_config

    try:
        default_target_user_id = sorted(owner_user_ids)[0] if owner_user_ids else None
        scheduler = build_scheduler_from_config(
            config,
            default_target_user_id=default_target_user_id,
            project_root=project_root,
        )
        if scheduler:
            logger.info(
                "Loaded scheduler with %d active job(s) from %s",
                len(scheduler.jobs),
                config.get("automations", {}).get("storage_dir", "~/.oh-my-agent/automations"),
            )
    except Exception as exc:
        logger.error("Failed to build scheduler from config: %s", exc)
        sys.exit(1)

    # Build runtime service (optional, defaults enabled when memory store exists)
    runtime_cfg = config.get("runtime", {"enabled": True})
    runtime_service = None
    auth_service = None
    if memory_store and bool(runtime_cfg.get("enabled", True)):
        from oh_my_agent.auth.providers.bilibili import BilibiliAuthProvider
        from oh_my_agent.auth.service import AuthService
        from oh_my_agent.runtime import RuntimeService

        auth_service = AuthService(
            memory_store,
            config=config.get("auth", {}),
            providers=[BilibiliAuthProvider()],
        )
        runtime_service = RuntimeService(
            memory_store,
            config={
                **runtime_cfg,
                "skill_evaluation": config.get("skills", {}).get("evaluation", {}),
            },
            owner_user_ids=owner_user_ids,
            repo_root=project_root,
            skill_syncer=skill_syncer,
            skills_path=(
                _resolve_project_path(skills_cfg.get("path", "skills/"), project_root)
                if skills_cfg.get("enabled", False)
                else None
            ),
            workspace_skills_dirs=workspace_skills_dirs,
            auth_service=auth_service,
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

                extra_body_cfg = router_cfg.get("extra_body", {})
                if not isinstance(extra_body_cfg, dict):
                    logger.warning(
                        "router.extra_body must be a mapping, got %s; ignoring",
                        type(extra_body_cfg).__name__,
                    )
                    extra_body_cfg = {}
                intent_router = OpenAICompatibleRouter(
                    base_url=str(router_cfg.get("base_url", "https://api.deepseek.com/v1")),
                    api_key=api_key,
                    model=str(router_cfg.get("model", "deepseek-chat")),
                    timeout_seconds=int(router_cfg.get("timeout_seconds", 15)),
                    confidence_threshold=float(router_cfg.get("confidence_threshold", 0.55)),
                    max_retries=int(router_cfg.get("max_retries", 1)),
                    extra_body=extra_body_cfg,
                )
                logger.info(
                    "Intent router enabled model=%s base=%s threshold=%.2f autonomy=%.2f timeout=%ss retries=%s require_user_confirm=%s",
                    router_cfg.get("model", "deepseek-chat"),
                    router_cfg.get("base_url", "https://api.deepseek.com/v1"),
                    float(router_cfg.get("confidence_threshold", 0.55)),
                    float(router_cfg.get("autonomy_threshold", 0.90)),
                    int(router_cfg.get("timeout_seconds", 15)),
                    int(router_cfg.get("max_retries", 1)),
                    bool(router_cfg.get("require_user_confirm", True)),
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

    if judge_store is not None and judge_store.should_synthesize():
        try:
            await judge_store.synthesize_memory_md(channel_pairs[0][1])
            logger.info("MEMORY.md refreshed at startup.")
        except Exception as exc:
            logger.warning("MEMORY.md startup synthesis failed: %s", exc)

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
        repo_root=project_root,
        intent_router=intent_router,
        router_context_turns=int(router_cfg.get("context_turns", 10)),
        router_require_user_confirm=bool(router_cfg.get("require_user_confirm", True)),
        router_autonomy_threshold=float(router_cfg.get("autonomy_threshold", 0.90)),
        skill_evaluation_config=config.get("skills", {}).get("evaluation", {}),
        judge_store=judge_store,
        judge=memory_judge,
        idle_tracker=idle_tracker,
        memory_inject_limit=memory_inject_limit,
        memory_keyword_patterns=memory_keyword_patterns,
    )
    if memory_store:
        gateway.set_memory_store(memory_store)

    diary_writer = None
    diary_cfg = memory_cfg.get("diary", {}) if isinstance(memory_cfg, dict) else {}
    if diary_cfg.get("enabled", True):
        from oh_my_agent.memory.session_diary import SessionDiaryWriter

        diary_dir_cfg = diary_cfg.get("path")
        if diary_dir_cfg:
            diary_dir = Path(str(diary_dir_cfg)).expanduser().resolve()
        else:
            diary_dir = ctx.runtime_root / "diary"
        diary_writer = SessionDiaryWriter(diary_dir)
        diary_writer.start()
        gateway.set_diary_writer(diary_writer)
        logger.info("Session diary enabled at %s", diary_dir)

    logger.info("Starting gateway with %d channel(s)...", len(channel_pairs))
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    shutdown_reason = {"value": "gateway_completed"}
    shutdown_started = False

    def _request_shutdown(sig: signal.Signals) -> None:
        shutdown_reason["value"] = f"signal:{sig.name}"
        shutdown_event.set()

    _register_shutdown_signal_handlers(loop, _request_shutdown, logger)
    gateway_task = asyncio.create_task(gateway.start(), name="gateway:start")
    shutdown_waiter = asyncio.create_task(shutdown_event.wait(), name="gateway:shutdown-wait")

    async def _shutdown_once(reason: str) -> None:
        nonlocal shutdown_started
        if shutdown_started:
            return
        shutdown_started = True
        await _shutdown(
            gateway,
            scheduler,
            runtime_service,
            memory_store,
            logger,
            reason=reason,
            diary_writer=diary_writer,
        )

    try:
        done, _ = await asyncio.wait(
            {gateway_task, shutdown_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if shutdown_waiter in done:
            await _shutdown_once(shutdown_reason["value"])
            await gateway_task
        else:
            await gateway_task
    finally:
        shutdown_waiter.cancel()
        with suppress(asyncio.CancelledError):
            await shutdown_waiter
        await _shutdown_once(shutdown_reason["value"])
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.remove_signal_handler(sig)


# Backwards-compatible alias — some code paths (and tests) still reach for
# ``_async_main`` even though it now just delegates into :func:`ignite`.
async def _async_main(config: dict, logger: logging.Logger, *, project_root: Path) -> None:
    ctx = BootContext(
        config=config,
        config_path=project_root / "config.yaml",
        project_root=project_root,
        runtime_root=_runtime_root(config),
        logger=logger,
    )
    await ignite(ctx)
