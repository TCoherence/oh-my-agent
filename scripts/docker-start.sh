#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/docker-common.sh"

oma_docker_init
oma_docker_ensure_ready
oma_docker_remove_existing_container
oma_docker_build_common_run_args

CMD=(
  docker run -d
  --restart unless-stopped
  "${OMA_DOCKER_RUN_COMMON_ARGS[@]}"
  "${IMAGE_TAG}"
)

if [[ $# -gt 0 ]]; then
  CMD+=("$@")
fi

oma_docker_print_banner "detached-service"
container_id="$("${CMD[@]}")"
echo "[oma] container id: ${container_id}"
echo "[oma] use ./scripts/docker-logs.sh to follow container stdout/stderr"
echo "[oma] app log file: $(oma_docker_app_log_path)"

# Start the dashboard side-container if enabled. Failure here is
# non-fatal — bot is already up and the dashboard is opt-in.
oma_dashboard_start_detached
