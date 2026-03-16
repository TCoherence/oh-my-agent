#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/docker-common.sh"

oma_docker_init

if ! oma_docker_container_exists; then
  echo "[oma] container ${CONTAINER_NAME} not found"
  echo "[oma] app log file: $(oma_docker_app_log_path)"
  exit 0
fi

docker inspect "${CONTAINER_NAME}" --format \
'name={{.Name}}
status={{.State.Status}}
running={{.State.Running}}
paused={{.State.Paused}}
restarting={{.State.Restarting}}
exit_code={{.State.ExitCode}}
started_at={{.State.StartedAt}}
finished_at={{.State.FinishedAt}}
restart_policy={{.HostConfig.RestartPolicy.Name}}
image={{.Config.Image}}'

echo "app_log=$(oma_docker_app_log_path)"
