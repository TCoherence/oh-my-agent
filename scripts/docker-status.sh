#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/docker-common.sh"

oma_docker_init

oma_print_container_status() {
  local name="$1"
  echo "=== ${name} ==="
  docker inspect "${name}" --format \
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
  # Show port bindings (handy for the dashboard — tells the user which
  # host port is live without grepping logs).
  local ports
  ports="$(docker inspect "${name}" --format \
'{{range $p, $v := .NetworkSettings.Ports}}{{if $v}}{{$p}}=>{{(index $v 0).HostIp}}:{{(index $v 0).HostPort}} {{end}}{{end}}')"
  if [[ -n "${ports}" ]]; then
    echo "ports=${ports}"
  fi
  echo
}

if ! oma_docker_container_exists && ! oma_dashboard_container_exists; then
  echo "[oma] no containers found (bot=${CONTAINER_NAME}, dashboard=${DASHBOARD_CONTAINER_NAME})"
  echo "[oma] app log file: $(oma_docker_app_log_path)"
  exit 0
fi

if oma_docker_container_exists; then
  oma_print_container_status "${CONTAINER_NAME}"
else
  echo "[oma] bot container ${CONTAINER_NAME} not running"
  echo
fi

if oma_dashboard_container_exists; then
  oma_print_container_status "${DASHBOARD_CONTAINER_NAME}"
fi

echo "app_log=$(oma_docker_app_log_path)"
