#!/usr/bin/env bash

oma_detect_timezone() {
  if [[ -n "${OMA_TIMEZONE:-}" ]]; then
    printf '%s\n' "${OMA_TIMEZONE}"
    return
  fi
  if [[ -n "${TZ:-}" ]]; then
    printf '%s\n' "${TZ}"
    return
  fi
  if [[ -L /etc/localtime ]]; then
    local tz_path
    tz_path="$(readlink /etc/localtime 2>/dev/null || true)"
    case "${tz_path}" in
      */zoneinfo/*)
        printf '%s\n' "${tz_path##*/zoneinfo/}"
        return
        ;;
    esac
  fi
  if [[ -f /etc/timezone ]]; then
    local tz_file
    tz_file="$(tr -d '[:space:]' < /etc/timezone 2>/dev/null || true)"
    if [[ -n "${tz_file}" ]]; then
      printf '%s\n' "${tz_file}"
      return
    fi
  fi
  printf 'UTC\n'
}

oma_docker_init() {
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
  IMAGE_TAG="${OMA_IMAGE_TAG:-oh-my-agent:local}"
  MOUNT_PATH="${OMA_DOCKER_MOUNT:-${HOME}/oh-my-agent-docker-mount}"
  CONTAINER_NAME="${OMA_CONTAINER_NAME:-oh-my-agent}"
  REPO_MOUNT_PATH="${OMA_DOCKER_REPO:-${REPO_ROOT}}"
  WORKSPACE_MOUNT_TARGET="${OMA_WORKSPACE_MOUNT_TARGET:-/home}"
  REPO_MOUNT_TARGET="${OMA_REPO_MOUNT_TARGET:-/repo}"
  WORKDIR_IN_CONTAINER="${OMA_WORKDIR_IN_CONTAINER:-${WORKSPACE_MOUNT_TARGET}}"
  CONFIG_PATH_IN_CONTAINER="${OMA_CONFIG_PATH:-${REPO_MOUNT_TARGET}/config.yaml}"
  OMA_CONTAINER_PATH="${WORKSPACE_MOUNT_TARGET}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  OMA_REPORT_TIMEZONE_VALUE="${OMA_REPORT_TIMEZONE:-$(oma_detect_timezone)}"

  # Dashboard side container — read-only monitoring page; lives in the same
  # image, runs the `oma-dashboard` entry point, port-published to host
  # loopback only. Default-on because it's cheap (~30MB extra RAM); set
  # OMA_DASHBOARD_ENABLED=0 to skip.
  DASHBOARD_CONTAINER_NAME="${OMA_DASHBOARD_CONTAINER_NAME:-${CONTAINER_NAME}-dashboard}"
  DASHBOARD_ENABLED="${OMA_DASHBOARD_ENABLED:-1}"
  DASHBOARD_PORTS="${OMA_DASHBOARD_PORTS:-8080 8081 8088 8888 9090}"
  DASHBOARD_BIND_HOST="${OMA_DASHBOARD_BIND_HOST:-127.0.0.1}"
  DASHBOARD_CONTAINER_PORT="${OMA_DASHBOARD_CONTAINER_PORT:-8080}"
}

oma_docker_ensure_ready() {
  mkdir -p "${MOUNT_PATH}"
  if [[ ! -d "${REPO_MOUNT_PATH}" ]]; then
    echo "[oma] repo mount path does not exist: ${REPO_MOUNT_PATH}" >&2
    exit 1
  fi

  if ! docker image inspect "${IMAGE_TAG}" >/dev/null 2>&1; then
    echo "[oma] image ${IMAGE_TAG} not found, building first..."
    "${SCRIPT_DIR}/docker-build.sh"
  fi
}

oma_docker_container_exists() {
  docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"
}

oma_docker_remove_existing_container() {
  if oma_docker_container_exists; then
    echo "[oma] removing existing container ${CONTAINER_NAME}"
    docker rm -f "${CONTAINER_NAME}" >/dev/null
  fi
}

oma_docker_build_common_run_args() {
  OMA_DOCKER_RUN_COMMON_ARGS=(
    --name "${CONTAINER_NAME}"
    --user "$(id -u):$(id -g)"
    --cap-drop ALL
    --security-opt no-new-privileges
    -e HOME="${WORKSPACE_MOUNT_TARGET}"
    -e PATH="${OMA_CONTAINER_PATH}"
    -e OMA_MOUNT_ROOT="${WORKSPACE_MOUNT_TARGET}"
    -e OMA_WORKDIR="${WORKDIR_IN_CONTAINER}"
    -e OMA_REPO_ROOT="${REPO_MOUNT_TARGET}"
    -e OMA_CONFIG_PATH="${CONFIG_PATH_IN_CONTAINER}"
    -e TZ="${OMA_REPORT_TIMEZONE_VALUE}"
    -e OMA_REPORT_TIMEZONE="${OMA_REPORT_TIMEZONE_VALUE}"
    -e OMA_AGENT_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS="${OMA_AGENT_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS:-true}"
    -e OMA_AGENT_CLAUDE_PERMISSION_MODE="${OMA_AGENT_CLAUDE_PERMISSION_MODE:-}"
    -e OMA_AGENT_CODEX_SANDBOX_MODE="${OMA_AGENT_CODEX_SANDBOX_MODE:-danger-full-access}"
    -e OMA_AGENT_CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX="${OMA_AGENT_CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX:-true}"
    -v "${MOUNT_PATH}:${WORKSPACE_MOUNT_TARGET}"
    -v "${REPO_MOUNT_PATH}:${REPO_MOUNT_TARGET}"
  )
}

oma_docker_print_banner() {
  local mode="$1"
  echo "[oma] starting container ${CONTAINER_NAME} (${mode})"
  echo "[oma] host mount: ${MOUNT_PATH} -> ${WORKSPACE_MOUNT_TARGET}"
  echo "[oma] repo mount: ${REPO_MOUNT_PATH} -> ${REPO_MOUNT_TARGET}"
  echo "[oma] workdir in container: ${WORKDIR_IN_CONTAINER}"
  echo "[oma] config path in container: ${CONFIG_PATH_IN_CONTAINER}"
  echo "[oma] report timezone in container: ${OMA_REPORT_TIMEZONE_VALUE}"
  echo "[oma] path in container: ${OMA_CONTAINER_PATH}"
  echo "[oma] docker agent overrides: claude_skip_permissions=${OMA_AGENT_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS:-true}, codex_sandbox=${OMA_AGENT_CODEX_SANDBOX_MODE:-danger-full-access}, codex_bypass=${OMA_AGENT_CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX:-true}"
}

oma_docker_app_log_path() {
  printf '%s/.oh-my-agent/runtime/logs/oh-my-agent.log\n' "${MOUNT_PATH}"
}


# ─────────────────────────────────────────────────────────────────────
# Dashboard helpers (parallel to the bot helpers above)
# ─────────────────────────────────────────────────────────────────────

oma_dashboard_container_exists() {
  docker ps -a --format '{{.Names}}' | grep -Fxq "${DASHBOARD_CONTAINER_NAME}"
}

oma_dashboard_remove_existing_container() {
  if oma_dashboard_container_exists; then
    echo "[oma] removing existing dashboard container ${DASHBOARD_CONTAINER_NAME}"
    docker rm -f "${DASHBOARD_CONTAINER_NAME}" >/dev/null
  fi
}

# Print port to stdout if free; return non-zero if taken.
# Tries `lsof` first (most accurate on macOS); falls back to `nc -z`. If
# neither tool is available, optimistically returns success and lets the
# subsequent `docker run` fail-detect the bind.
oma_port_is_free() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    ! lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    return
  fi
  if command -v nc >/dev/null 2>&1; then
    ! nc -z 127.0.0.1 "${port}" >/dev/null 2>&1
    return
  fi
  return 0
}

# Iterate DASHBOARD_PORTS, print the first one that's free on the host.
# Empty stdout if all are taken.
oma_dashboard_pick_port() {
  local port
  for port in ${DASHBOARD_PORTS}; do
    if oma_port_is_free "${port}"; then
      printf '%s\n' "${port}"
      return 0
    fi
  done
  return 1
}

# Build the docker-run argv for the dashboard container. Mirrors
# oma_docker_build_common_run_args but: different container name,
# OMA_FAIL_FAST_CLI=0 (no CLI binary check needed), and the chosen
# host_port:container_port mapping appended by the caller.
oma_dashboard_build_run_args() {
  OMA_DASHBOARD_RUN_ARGS=(
    --name "${DASHBOARD_CONTAINER_NAME}"
    --user "$(id -u):$(id -g)"
    --cap-drop ALL
    --security-opt no-new-privileges
    -e HOME="${WORKSPACE_MOUNT_TARGET}"
    -e PATH="${OMA_CONTAINER_PATH}"
    -e OMA_MOUNT_ROOT="${WORKSPACE_MOUNT_TARGET}"
    -e OMA_WORKDIR="${WORKDIR_IN_CONTAINER}"
    -e OMA_REPO_ROOT="${REPO_MOUNT_TARGET}"
    -e OMA_CONFIG_PATH="${CONFIG_PATH_IN_CONTAINER}"
    -e OMA_FAIL_FAST_CLI=0
    -e TZ="${OMA_REPORT_TIMEZONE_VALUE}"
    -v "${MOUNT_PATH}:${WORKSPACE_MOUNT_TARGET}"
    -v "${REPO_MOUNT_PATH}:${REPO_MOUNT_TARGET}"
  )
}

# Start the dashboard container detached. Honors DASHBOARD_ENABLED gate
# and iterates DASHBOARD_PORTS, attempting `docker run -p <host>:<container>`
# for each until one binds. Always returns success — a dashboard failure
# does NOT block bot startup. Prints a clearly labeled URL line so
# docker-logs.sh / scrollback can find the port that actually won.
#
# Why we don't trust oma_port_is_free as the authoritative gate:
#   `lsof` without sudo on macOS misses root-owned listeners (e.g. the
#   built-in Apache binds *:8080 as root and a non-sudo lsof returns
#   empty), so a port that LOOKS free can fail the actual `docker run`
#   bind. Pre-check is kept as a hint to skip slow `docker run` round-
#   trips, but the real authority is the docker run exit code — on
#   failure we clean up the partially-created container and try the
#   next port.
oma_dashboard_start_detached() {
  if [[ "${DASHBOARD_ENABLED}" != "1" ]]; then
    echo "[oma] dashboard disabled (OMA_DASHBOARD_ENABLED=${DASHBOARD_ENABLED})"
    return 0
  fi

  oma_dashboard_remove_existing_container
  oma_dashboard_build_run_args

  local launch_err_file="/tmp/oma-dashboard-launch.${$}.err"
  local port last_err="" tried_count=0
  for port in ${DASHBOARD_PORTS}; do
    tried_count=$((tried_count + 1))
    # Cheap hint pass: if the port is observably busy (and we have a way
    # to check), skip the docker round-trip for this candidate.
    if ! oma_port_is_free "${port}"; then
      echo "[oma] dashboard: port ${port} appears busy on host, trying next"
      continue
    fi
    if docker run -d --restart unless-stopped \
        "${OMA_DASHBOARD_RUN_ARGS[@]}" \
        -p "${DASHBOARD_BIND_HOST}:${port}:${DASHBOARD_CONTAINER_PORT}" \
        "${IMAGE_TAG}" \
        oma-dashboard --host 0.0.0.0 --port "${DASHBOARD_CONTAINER_PORT}" \
        >/dev/null 2>"${launch_err_file}"; then
      echo "[oma] dashboard at http://${DASHBOARD_BIND_HOST}:${port}"
      echo "[oma] dashboard container: ${DASHBOARD_CONTAINER_NAME}"
      rm -f "${launch_err_file}"
      return 0
    fi
    # docker run failed (port was busy under the hood, or some other
    # reason). Capture the last error, clean up any stranded container,
    # and try the next candidate.
    if [[ -s "${launch_err_file}" ]]; then
      last_err="$(cat "${launch_err_file}")"
    fi
    docker rm -f "${DASHBOARD_CONTAINER_NAME}" >/dev/null 2>&1 || true
    echo "[oma] dashboard: docker run failed on port ${port}, trying next"
  done

  echo "[oma] dashboard: every candidate port failed (tried ${tried_count}: ${DASHBOARD_PORTS})" >&2
  if [[ -n "${last_err}" ]]; then
    printf '%s\n' "${last_err}" | sed 's/^/[oma]   /' >&2
  fi
  echo "[oma] override with OMA_DASHBOARD_PORTS='9091 9092' or set OMA_DASHBOARD_ENABLED=0" >&2
  rm -f "${launch_err_file}"
  return 0
}
