#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/docker-common.sh"

oma_docker_init

# Stop the dashboard first — it's read-only and cheap to lose; the bot's
# terminal log should be the last thing on stdout.
if oma_dashboard_container_exists; then
  echo "[oma] stopping dashboard container ${DASHBOARD_CONTAINER_NAME}"
  docker stop "${DASHBOARD_CONTAINER_NAME}" >/dev/null
  echo "[oma] stopped ${DASHBOARD_CONTAINER_NAME}"
fi

if ! oma_docker_container_exists; then
  echo "[oma] container ${CONTAINER_NAME} is not present"
  echo "[oma] app log file: $(oma_docker_app_log_path)"
  exit 0
fi

echo "[oma] stopping container ${CONTAINER_NAME}"
docker stop "${CONTAINER_NAME}" >/dev/null
echo "[oma] stopped ${CONTAINER_NAME}"
echo "[oma] app log file: $(oma_docker_app_log_path)"
