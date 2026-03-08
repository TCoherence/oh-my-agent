#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_TAG="${OMA_IMAGE_TAG:-oh-my-agent:local}"

echo "[oma] building image ${IMAGE_TAG}"
docker build -t "${IMAGE_TAG}" "${REPO_ROOT}"
