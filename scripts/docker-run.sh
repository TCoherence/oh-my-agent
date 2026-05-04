#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/docker-common.sh"

oma_docker_init
oma_docker_ensure_ready
oma_docker_remove_existing_container
oma_docker_build_common_run_args

CMD=(
  docker run --rm
  "${OMA_DOCKER_RUN_COMMON_ARGS[@]}"
  "${IMAGE_TAG}"
)

if [[ -t 0 && -t 1 ]]; then
  CMD=(docker run --rm -it "${OMA_DOCKER_RUN_COMMON_ARGS[@]}" "${IMAGE_TAG}")
fi

if [[ $# -gt 0 ]]; then
  CMD+=("$@")
fi

oma_docker_print_banner "attached-dev"

# Start the dashboard side-container (detached) BEFORE we exec into the
# foreground bot. dashboard's a separate `docker run -d`, so Ctrl+C of
# the bot won't tear it down — clean up both via `docker-stop.sh`. Set
# OMA_DASHBOARD_ENABLED=0 for fast dev iteration without the dashboard.
oma_dashboard_start_detached

exec "${CMD[@]}"
