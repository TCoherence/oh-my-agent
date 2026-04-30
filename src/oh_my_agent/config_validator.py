"""Config validation for oh-my-agent.

Provides a single ``validate_config`` entrypoint that returns a list of
structured errors/warnings.  Used by both normal startup and the
``--validate-config`` CLI flag.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_PLATFORMS = {"discord"}
UNSUPPORTED_PLATFORMS = {
    "slack": (
        '"slack" is not supported in 1.0; see docs/EN/upgrade-guide.md '
        "(v0.9.x → v1.0 section) for the rationale and post-1.0 plans"
    ),
}
SUPPORTED_AGENT_TYPES = {"cli", "api"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass
class ConfigError:
    """A single validation finding."""

    path: str
    message: str
    severity: str  # "error" | "warning"

    def __str__(self) -> str:
        tag = "ERROR" if self.severity == "error" else "WARNING"
        return f"[{tag}] {self.path}: {self.message}"


@dataclass
class ValidationResult:
    """Aggregate result of ``validate_config``."""

    errors: list[ConfigError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(e.severity == "error" for e in self.errors)

    def summary(self) -> str:
        if not self.errors:
            return "Config is valid."
        lines = [str(e) for e in self.errors]
        n_err = sum(1 for e in self.errors if e.severity == "error")
        n_warn = sum(1 for e in self.errors if e.severity == "warning")
        lines.append(f"\n{n_err} error(s), {n_warn} warning(s)")
        return "\n".join(lines)


ROUTER_RESERVED_PAYLOAD_KEYS = frozenset({"messages", "model", "max_tokens", "temperature"})


def validate_config(config: dict[str, Any]) -> ValidationResult:
    """Validate a loaded config dict.  Returns a ``ValidationResult``."""
    result = ValidationResult()
    _check_gateway(config, result)
    _check_agents(config, result)
    _check_automations(config, result)
    _check_logging(config, result)
    _check_sections(config, result)
    _check_router(config, result)
    _check_notifications(config, result)
    _check_runtime_cleanup(config, result)
    return result


# ── Gateway / channels ──────────────────────────────────────────────── #

def _check_gateway(config: dict, result: ValidationResult) -> None:
    gateway = config.get("gateway")
    if gateway is None:
        result.errors.append(ConfigError("gateway", "section is required", "error"))
        return
    if not isinstance(gateway, dict):
        result.errors.append(ConfigError("gateway", "must be a mapping", "error"))
        return

    channels = gateway.get("channels")
    if channels is None:
        result.errors.append(ConfigError("gateway.channels", "is required", "error"))
        return
    if not isinstance(channels, list) or len(channels) == 0:
        result.errors.append(ConfigError("gateway.channels", "must be a non-empty list", "error"))
        return

    for idx, ch in enumerate(channels):
        prefix = f"gateway.channels[{idx}]"
        if not isinstance(ch, dict):
            result.errors.append(ConfigError(prefix, "must be a mapping", "error"))
            continue

        # platform
        platform = ch.get("platform")
        if not platform:
            result.errors.append(ConfigError(f"{prefix}.platform", "is required", "error"))
        elif str(platform) in UNSUPPORTED_PLATFORMS:
            result.errors.append(ConfigError(
                f"{prefix}.platform",
                UNSUPPORTED_PLATFORMS[str(platform)],
                "error",
            ))
        elif str(platform) not in SUPPORTED_PLATFORMS:
            result.errors.append(ConfigError(
                f"{prefix}.platform",
                f"unsupported platform '{platform}' (expected one of {sorted(SUPPORTED_PLATFORMS)})",
                "error",
            ))

        # token
        token = ch.get("token")
        if not token or (isinstance(token, str) and not token.strip()):
            result.errors.append(ConfigError(f"{prefix}.token", "is required", "error"))

        # channel_id
        channel_id = ch.get("channel_id")
        if not channel_id:
            result.errors.append(ConfigError(f"{prefix}.channel_id", "is required", "error"))

        # agents reference
        agents_ref = ch.get("agents")
        if not agents_ref or not isinstance(agents_ref, list) or len(agents_ref) == 0:
            result.errors.append(ConfigError(f"{prefix}.agents", "must be a non-empty list", "error"))


# ── Agents ──────────────────────────────────────────────────────────── #

def _check_agents(config: dict, result: ValidationResult) -> None:
    agents = config.get("agents")
    if agents is None:
        result.errors.append(ConfigError("agents", "section is required", "error"))
        return
    if not isinstance(agents, dict) or len(agents) == 0:
        result.errors.append(ConfigError("agents", "must be a non-empty mapping", "error"))
        return

    for name, agent_cfg in agents.items():
        prefix = f"agents.{name}"
        if not isinstance(agent_cfg, dict):
            result.errors.append(ConfigError(prefix, "must be a mapping", "error"))
            continue

        agent_type = agent_cfg.get("type", "cli")
        if str(agent_type) not in SUPPORTED_AGENT_TYPES:
            result.errors.append(ConfigError(
                f"{prefix}.type",
                f"unsupported type '{agent_type}' (expected one of {sorted(SUPPORTED_AGENT_TYPES)})",
                "error",
            ))

        # CLI agents should have cli_path (warn if missing, since there are defaults)
        if str(agent_type) == "cli" and not agent_cfg.get("cli_path"):
            result.errors.append(ConfigError(
                f"{prefix}.cli_path",
                "not set; will use default binary name",
                "warning",
            ))


# ── Automations ─────────────────────────────────────────────────────── #

def _check_automations(config: dict, result: ValidationResult) -> None:
    automations = config.get("automations")
    if automations is None or not isinstance(automations, dict):
        return

    dump_channels = automations.get("dump_channels")
    if dump_channels is not None:
        if not isinstance(dump_channels, dict):
            result.errors.append(ConfigError(
                "automations.dump_channels",
                "must be a mapping of name → {platform, channel_id}",
                "warning",
            ))
        else:
            for name, entry in dump_channels.items():
                if not isinstance(entry, dict):
                    result.errors.append(ConfigError(
                        f"automations.dump_channels.{name}",
                        "must be a mapping with platform/channel_id",
                        "warning",
                    ))
                    continue
                if not entry.get("platform"):
                    result.errors.append(ConfigError(
                        f"automations.dump_channels.{name}.platform",
                        "is required",
                        "warning",
                    ))
                if not entry.get("channel_id"):
                    result.errors.append(ConfigError(
                        f"automations.dump_channels.{name}.channel_id",
                        "is required",
                        "warning",
                    ))

    storage_dir = automations.get("storage_dir")
    if not storage_dir:
        return

    # Storage dir existence is a runtime concern, not a config error.
    # But if we find YAML automation files, validate their shape.
    # That level of validation is deferred to the scheduler itself.


# ── Logging ─────────────────────────────────────────────────────────── #

def _check_logging(config: dict, result: ValidationResult) -> None:
    logging_cfg = config.get("logging")
    if logging_cfg is None:
        return
    if not isinstance(logging_cfg, dict):
        result.errors.append(ConfigError("logging", "must be a mapping", "warning"))
        return

    level = logging_cfg.get("level")
    if level is not None and str(level).upper() not in VALID_LOG_LEVELS:
        result.errors.append(ConfigError(
            "logging.level",
            f"invalid level '{level}' (expected one of {sorted(VALID_LOG_LEVELS)})",
            "warning",
        ))

    for key in ("service_retention_days", "thread_log_retention_days"):
        val = logging_cfg.get(key)
        if val is not None:
            try:
                if int(val) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                result.errors.append(ConfigError(
                    f"logging.{key}",
                    f"must be a non-negative integer, got '{val}'",
                    "warning",
                ))


# ── Generic section checks ──────────────────────────────────────────── #

def _check_sections(config: dict, result: ValidationResult) -> None:
    """Light type-checks for optional sections."""
    for section in ("runtime", "memory", "skills", "router", "auth"):
        val = config.get(section)
        if val is not None and not isinstance(val, dict):
            result.errors.append(ConfigError(section, "must be a mapping if present", "warning"))


# ── Router ──────────────────────────────────────────────────────────── #

def _check_router(config: dict, result: ValidationResult) -> None:
    """Validate the ``router`` section's typed fields."""
    router = config.get("router")
    if router is None or not isinstance(router, dict):
        return  # section-level issue already handled by _check_sections

    def _err(path: str, message: str) -> None:
        result.errors.append(ConfigError(path, message, "error"))

    # Booleans
    for key in ("enabled", "require_user_confirm"):
        if key in router and not isinstance(router[key], bool):
            _err(f"router.{key}", f"must be a bool, got {type(router[key]).__name__}")

    # Positive integers
    for key in ("timeout_seconds", "context_turns"):
        if key in router:
            val = router[key]
            if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
                _err(f"router.{key}", f"must be a positive integer, got '{val}'")

    # Non-negative integers
    if "max_retries" in router:
        val = router["max_retries"]
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            _err("router.max_retries", f"must be a non-negative integer, got '{val}'")

    # Floats in [0, 1]
    for key in ("confidence_threshold", "autonomy_threshold"):
        if key in router:
            val = router[key]
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                _err(f"router.{key}", f"must be a number in [0, 1], got '{val}'")
                continue
            fval = float(val)
            if fval < 0.0 or fval > 1.0:
                _err(f"router.{key}", f"must be in [0, 1], got {fval}")

    # autonomy_threshold >= confidence_threshold (only when both are valid numbers in [0, 1])
    ct = router.get("confidence_threshold")
    at = router.get("autonomy_threshold")
    if (
        isinstance(ct, (int, float))
        and not isinstance(ct, bool)
        and isinstance(at, (int, float))
        and not isinstance(at, bool)
        and 0.0 <= float(ct) <= 1.0
        and 0.0 <= float(at) <= 1.0
        and float(at) < float(ct)
    ):
        _err(
            "router.autonomy_threshold",
            (
                f"must be >= router.confidence_threshold (got {float(at)} < {float(ct)}); "
                "otherwise the borderline-confirmation band collapses and users never see "
                "a draft for destructive intents"
            ),
        )

    # extra_body: dict with no reserved keys
    if "extra_body" in router:
        extra = router["extra_body"]
        if not isinstance(extra, dict):
            _err("router.extra_body", f"must be a mapping, got {type(extra).__name__}")
        else:
            for reserved in ROUTER_RESERVED_PAYLOAD_KEYS:
                if reserved in extra:
                    _err(
                        f"router.extra_body.{reserved}",
                        (
                            f"reserved key '{reserved}' is set by the router itself and must not "
                            "be overridden via extra_body"
                        ),
                    )


# ── External push notifications ─────────────────────────────────────── #

_PUSH_PROVIDERS = {"bark", "noop"}
_PUSH_LEVELS = {"passive", "active", "timeSensitive", "critical"}
_PUSH_EVENT_KEYS = {
    "mention_owner",
    "task_draft",
    "task_waiting_merge",
    "ask_user",
    "automation_complete",
    "automation_failed",
}


def _check_notifications(config: dict, result: ValidationResult) -> None:
    """Validate the optional ``notifications`` section."""
    section = config.get("notifications")
    if section is None:
        return
    if not isinstance(section, dict):
        result.errors.append(ConfigError("notifications", "must be a mapping", "error"))
        return
    if not section.get("enabled", False):
        return  # skip rest of checks when disabled

    provider = section.get("provider", "bark")
    if provider not in _PUSH_PROVIDERS:
        result.errors.append(ConfigError(
            "notifications.provider",
            f"unknown provider '{provider}', expected one of {sorted(_PUSH_PROVIDERS)}",
            "error",
        ))
        return

    if provider == "bark":
        bark = section.get("bark") or {}
        if not isinstance(bark, dict):
            result.errors.append(ConfigError(
                "notifications.bark", "must be a mapping", "error",
            ))
            return
        env_name = bark.get("device_key_env", "")
        if not env_name or not isinstance(env_name, str):
            result.errors.append(ConfigError(
                "notifications.bark.device_key_env",
                "must be a non-empty env-var name (e.g. \"BARK_DEVICE_KEY\")",
                "error",
            ))
        elif not os.environ.get(env_name):
            # warning — env may be injected later (e.g. via systemd unit env)
            result.errors.append(ConfigError(
                "notifications.bark.device_key_env",
                f"env var ${env_name} is not set",
                "warning",
            ))

    events = section.get("events") or {}
    if not isinstance(events, dict):
        result.errors.append(ConfigError(
            "notifications.events", "must be a mapping", "error",
        ))
    else:
        for key, value in events.items():
            if key not in _PUSH_EVENT_KEYS:
                result.errors.append(ConfigError(
                    f"notifications.events.{key}",
                    f"unknown event kind, expected one of {sorted(_PUSH_EVENT_KEYS)}",
                    "warning",
                ))
            if not isinstance(value, bool):
                result.errors.append(ConfigError(
                    f"notifications.events.{key}",
                    f"must be a bool, got {type(value).__name__}",
                    "error",
                ))

    levels = section.get("levels") or {}
    if not isinstance(levels, dict):
        result.errors.append(ConfigError(
            "notifications.levels", "must be a mapping", "error",
        ))
    else:
        for key, value in levels.items():
            if key not in _PUSH_EVENT_KEYS:
                result.errors.append(ConfigError(
                    f"notifications.levels.{key}",
                    f"unknown event kind, expected one of {sorted(_PUSH_EVENT_KEYS)}",
                    "warning",
                ))
            if value not in _PUSH_LEVELS:
                result.errors.append(ConfigError(
                    f"notifications.levels.{key}",
                    f"must be one of {sorted(_PUSH_LEVELS)}, got '{value}'",
                    "error",
                ))


# ── Runtime cleanup ─────────────────────────────────────────────────── #

def _check_runtime_cleanup(config: dict, result: ValidationResult) -> None:
    """Validate ``runtime.cleanup`` retention shape.

    Hard-errors when ``runtime`` or ``runtime.cleanup`` are present but
    not mappings — boot-time defaulting calls ``setdefault`` on them and
    would crash before validation reports the issue.
    """
    runtime = config.get("runtime")
    if runtime is None:
        return
    if not isinstance(runtime, dict):
        result.errors.append(ConfigError(
            "runtime", "must be a mapping if present", "error",
        ))
        return
    cleanup = runtime.get("cleanup")
    if cleanup is None:
        return
    if not isinstance(cleanup, dict):
        result.errors.append(ConfigError(
            "runtime.cleanup", "must be a mapping if present", "error",
        ))
        return
    by_outcome = cleanup.get("retention_hours_by_outcome")
    if by_outcome is None:
        return
    if not isinstance(by_outcome, dict):
        result.errors.append(ConfigError(
            "runtime.cleanup.retention_hours_by_outcome",
            "must be a mapping if present",
            "error",
        ))
        return
    for key in ("success", "failure", "default"):
        v = by_outcome.get(key)
        if v is None:
            continue
        try:
            if int(v) < 0:
                raise ValueError
        except (TypeError, ValueError):
            result.errors.append(ConfigError(
                f"runtime.cleanup.retention_hours_by_outcome.{key}",
                f"must be a non-negative integer, got '{v}'",
                "error",
            ))
