"""Config validation for oh-my-agent.

Provides a single ``validate_config`` entrypoint that returns a list of
structured errors/warnings.  Used by both normal startup and the
``--validate-config`` CLI flag.
"""

from __future__ import annotations

import logging
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


def validate_config(config: dict[str, Any]) -> ValidationResult:
    """Validate a loaded config dict.  Returns a ``ValidationResult``."""
    result = ValidationResult()
    _check_gateway(config, result)
    _check_agents(config, result)
    _check_automations(config, result)
    _check_logging(config, result)
    _check_sections(config, result)
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
