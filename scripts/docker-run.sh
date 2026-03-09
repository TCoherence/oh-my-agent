#!/usr/bin/env bash
set -euo pipefail

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

mkdir -p "${MOUNT_PATH}"
if [[ ! -d "${REPO_MOUNT_PATH}" ]]; then
  echo "[oma] repo mount path does not exist: ${REPO_MOUNT_PATH}" >&2
  exit 1
fi

if ! docker image inspect "${IMAGE_TAG}" >/dev/null 2>&1; then
  echo "[oma] image ${IMAGE_TAG} not found, building first..."
  "${SCRIPT_DIR}/docker-build.sh"
fi

if docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
  echo "[oma] removing existing container ${CONTAINER_NAME}"
  docker rm -f "${CONTAINER_NAME}" >/dev/null
fi

CMD=(
  docker run --rm
  --name "${CONTAINER_NAME}"
  --user "$(id -u):$(id -g)"
  --cap-drop ALL
  --security-opt no-new-privileges
  -e HOME="${WORKSPACE_MOUNT_TARGET}"
  -e PATH="${WORKSPACE_MOUNT_TARGET}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  -e OMA_MOUNT_ROOT="${WORKSPACE_MOUNT_TARGET}"
  -e OMA_WORKDIR="${WORKDIR_IN_CONTAINER}"
  -e OMA_REPO_ROOT="${REPO_MOUNT_TARGET}"
  -e OMA_CONFIG_PATH="${CONFIG_PATH_IN_CONTAINER}"
  -e OMA_AGENT_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS="${OMA_AGENT_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS:-true}"
  -e OMA_AGENT_CLAUDE_PERMISSION_MODE="${OMA_AGENT_CLAUDE_PERMISSION_MODE:-}"
  -e OMA_AGENT_CODEX_SANDBOX_MODE="${OMA_AGENT_CODEX_SANDBOX_MODE:-danger-full-access}"
  -e OMA_AGENT_CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX="${OMA_AGENT_CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX:-true}"
  -v "${MOUNT_PATH}:${WORKSPACE_MOUNT_TARGET}"
  -v "${REPO_MOUNT_PATH}:${REPO_MOUNT_TARGET}"
  "${IMAGE_TAG}"
)

if [[ -t 0 && -t 1 ]]; then
  CMD=(docker run --rm -it "${CMD[@]:3}")
fi

if [[ $# -gt 0 ]]; then
  CMD+=("$@")
fi

echo "[oma] starting container ${CONTAINER_NAME}"
echo "[oma] host mount: ${MOUNT_PATH} -> ${WORKSPACE_MOUNT_TARGET}"
echo "[oma] repo mount: ${REPO_MOUNT_PATH} -> ${REPO_MOUNT_TARGET}"
echo "[oma] workdir in container: ${WORKDIR_IN_CONTAINER}"
echo "[oma] config path in container: ${CONFIG_PATH_IN_CONTAINER}"
echo "[oma] path in container: ${WORKSPACE_MOUNT_TARGET}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
echo "[oma] docker agent overrides: claude_skip_permissions=${OMA_AGENT_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS:-true}, codex_sandbox=${OMA_AGENT_CODEX_SANDBOX_MODE:-danger-full-access}, codex_bypass=${OMA_AGENT_CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX:-true}"

exec "${CMD[@]}"
