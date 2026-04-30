#!/usr/bin/env bash
set -euo pipefail

# Clear all slash commands on the Discord application(s) the active config
# points at, then exit. Next normal start of `oh-my-agent` re-registers
# whatever commands the current code defines.
#
# Usage:
#   ./scripts/clear-slash-commands.sh                # prod (default config)
#   ./scripts/clear-slash-commands.sh --dev          # dev (~/.oh-my-agent/dev-config.yaml)
#   ./scripts/clear-slash-commands.sh --config PATH  # explicit config
#
# Env overrides:
#   OMA_BIN          path to oh-my-agent binary (default: ./.venv/bin/oh-my-agent if present, else PATH)
#   OMA_DEV_CONFIG   path used by --dev (default: ~/.oh-my-agent/dev-config.yaml)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
    sed -n '4,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

DEV_CONFIG_DEFAULT="${OMA_DEV_CONFIG:-${HOME}/.oh-my-agent/dev-config.yaml}"
CONFIG_ARG=()
LABEL="default config"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dev)
            CONFIG_ARG=(--config "${DEV_CONFIG_DEFAULT}")
            LABEL="dev (${DEV_CONFIG_DEFAULT})"
            shift
            ;;
        --config)
            if [[ $# -lt 2 ]]; then
                echo "[oma] --config needs a path argument" >&2
                exit 2
            fi
            CONFIG_ARG=(--config "$2")
            LABEL="custom ($2)"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "[oma] unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

# Resolve the binary: prefer repo-local venv, fall back to PATH so users with
# a system-wide install or activated venv don't have to set OMA_BIN.
if [[ -z "${OMA_BIN:-}" ]]; then
    if [[ -x "${REPO_ROOT}/.venv/bin/oh-my-agent" ]]; then
        OMA_BIN="${REPO_ROOT}/.venv/bin/oh-my-agent"
    else
        OMA_BIN="oh-my-agent"
    fi
fi

echo "[oma] clearing slash commands — ${LABEL}"
cd "${REPO_ROOT}"
exec "${OMA_BIN}" "${CONFIG_ARG[@]}" --clear-commands
