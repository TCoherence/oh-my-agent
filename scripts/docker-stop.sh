#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/docker-common.sh"

oma_docker_init

if ! oma_docker_container_exists; then
  echo "[oma] container ${CONTAINER_NAME} is not present"
  exit 0
fi

echo "[oma] stopping container ${CONTAINER_NAME}"
docker stop "${CONTAINER_NAME}" >/dev/null
echo "[oma] stopped ${CONTAINER_NAME}"
echo "[oma] app log file: $(oma_docker_app_log_path)"
