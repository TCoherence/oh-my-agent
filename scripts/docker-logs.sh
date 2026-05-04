#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/docker-common.sh"

oma_docker_init

# `docker-logs.sh dashboard` follows the dashboard side-container instead.
# Default target is the bot (preserves existing behavior).
TARGET="${CONTAINER_NAME}"
if [[ $# -gt 0 && ( "$1" == "dashboard" || "$1" == "--dashboard" ) ]]; then
  TARGET="${DASHBOARD_CONTAINER_NAME}"
  shift
fi

if ! docker ps -a --format '{{.Names}}' | grep -Fxq "${TARGET}"; then
  echo "[oma] container ${TARGET} not found" >&2
  if [[ "${TARGET}" == "${CONTAINER_NAME}" ]]; then
    echo "[oma] app log file: $(oma_docker_app_log_path)" >&2
  fi
  exit 1
fi

if [[ $# -eq 0 ]]; then
  exec docker logs -f --tail 200 "${TARGET}"
fi

exec docker logs "$@" "${TARGET}"
