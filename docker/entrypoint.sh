#!/usr/bin/env bash
set -euo pipefail

MOUNT_ROOT="${OMA_MOUNT_ROOT:-/home}"
WORKDIR="${OMA_WORKDIR:-${MOUNT_ROOT}}"
REPO_ROOT="${OMA_REPO_ROOT:-/repo}"
CONFIG_PATH="${OMA_CONFIG_PATH:-${REPO_ROOT}/config.yaml}"

mkdir -p "${MOUNT_ROOT}" "${WORKDIR}"

if [[ "${OMA_INSTALL_REPO_EDITABLE:-1}" != "0" ]]; then
  if [[ ! -f "${REPO_ROOT}/pyproject.toml" ]]; then
    echo "[oma] pyproject.toml not found at ${REPO_ROOT}; cannot install mounted repo editable." >&2
    exit 1
  fi
  echo "[oma] installing mounted repo as editable package from ${REPO_ROOT}"
  python -m pip install --disable-pip-version-check --no-deps -e "${REPO_ROOT}"
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "[oma] config not found at ${CONFIG_PATH}" >&2
  echo "[oma] expected a prepared config in the mounted repo (e.g. /repo/config.yaml)." >&2
  exit 1
fi

if [[ "${OMA_FAIL_FAST_CLI:-1}" != "0" ]]; then
  missing_bins=()
  while IFS= read -r bin; do
    [[ -n "${bin}" ]] || continue
    if ! command -v "${bin}" >/dev/null 2>&1; then
      missing_bins+=("${bin}")
    fi
  done < <(
    python - "${CONFIG_PATH}" <<'PY'
import sys
from pathlib import Path
from oh_my_agent.config import load_config

cfg = load_config(Path(sys.argv[1]))
agents = cfg.get("agents", {})
seen = set()
for name, acfg in agents.items():
    if str(acfg.get("type", "cli")) != "cli":
        continue
    cli_path = str(acfg.get("cli_path", name)).strip()
    if not cli_path:
        continue
    cmd = cli_path.split()[0]
    if cmd not in seen:
        seen.add(cmd)
        print(cmd)
PY
  )
  if ((${#missing_bins[@]} > 0)); then
    echo "[oma] missing required CLI binary/binaries: ${missing_bins[*]}" >&2
    echo "[oma] install the CLI tools or adjust agents.*.cli_path in ${CONFIG_PATH}" >&2
    exit 1
  fi
fi

cd "${WORKDIR}"

if [[ $# -eq 0 ]]; then
  set -- oh-my-agent
fi

echo "[oma] mount_root=${MOUNT_ROOT}"
echo "[oma] workdir=${WORKDIR}"
echo "[oma] config_path=${CONFIG_PATH}"
echo "[oma] command=$*"

exec "$@"
