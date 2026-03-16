#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/docker-common.sh"

oma_docker_init

if ! oma_docker_container_exists; then
  echo "[oma] container ${CONTAINER_NAME} not found" >&2
  echo "[oma] app log file: $(oma_docker_app_log_path)" >&2
  exit 1
fi

if [[ $# -eq 0 ]]; then
  exec docker logs -f --tail 200 "${CONTAINER_NAME}"
fi

exec docker logs "$@" "${CONTAINER_NAME}"
