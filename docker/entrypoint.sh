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

  # Fast path: skip the pip install when an editable install for this
  # ${REPO_ROOT} is already present AND its entry-point script is on
  # disk. Editable installs work via a .pth file that adds /repo/src to
  # sys.path at interpreter start — once placed, source edits under
  # /repo reflect immediately on the next process boot. So re-running
  # `pip install -e` on every container start does nothing useful and
  # pays for itself in two ways:
  #
  #   (a) pip's uninstall-before-upgrade step has to re-stat every file
  #       listed in the RECORD manifest. On macOS Docker bind mounts
  #       (VirtioFS / gRPC FUSE) stat sometimes returns stale "deleted"
  #       inodes mid-flight, breaking the uninstall with
  #       ``[Errno 2] No such file or directory:
  #       '/home/.local/bin/oh-my-agent'`` even when the file is present
  #       on the host. The fast path sidesteps that race entirely.
  #   (b) Container boot drops the pip-install latency (~5-10s on
  #       cold start with deps cached).
  #
  # When pyproject.toml changes (new entry point, new dep, bumped
  # version) the .pth file remains valid for *imports* but new bin
  # scripts / new deps need a fresh install. Set OMA_FORCE_REINSTALL=1
  # to bypass the fast path then. We also auto-bypass if the .pth file
  # exists but the ``oh-my-agent`` console script is missing — that's
  # exactly the half-broken state where (a) above just deleted the bin
  # but pip never finished reinstalling it.
  needs_install=1
  if [[ "${OMA_FORCE_REINSTALL:-0}" != "1" ]] && \
     [[ -x "${HOME:-/home}/.local/bin/oh-my-agent" ]]; then
    shopt -s nullglob
    pth_matches=("${HOME:-/home}/.local/lib/python"*/site-packages/__editable__.oh_my_agent-*.pth)
    shopt -u nullglob
    for pth in "${pth_matches[@]}"; do
      if grep -q "${REPO_ROOT}" "${pth}" 2>/dev/null; then
        needs_install=0
        echo "[oma] editable install present (${pth} -> ${REPO_ROOT}); skipping pip install"
        echo "[oma]   override with OMA_FORCE_REINSTALL=1 after pyproject.toml changes"
        break
      fi
    done
  fi

  if [[ "${needs_install}" == "1" ]]; then
    echo "[oma] installing mounted repo as editable package from ${REPO_ROOT}"
    # Serialize concurrent installs across sibling containers (e.g. bot +
    # dashboard) that share this $HOME via a bind mount. Without the lock,
    # pip's uninstall-before-upgrade step from one container could remove
    # /home/.local/bin/oh-my-agent mid-flight while the other container is
    # mid-install — real incident: "OSError: No such file or directory:
    # '/home/.local/bin/oh-my-agent'" when starting both via docker-run.sh
    # right after a version bump. flock on a HOME-relative file means
    # siblings sharing HOME serialize; unrelated containers don't.
    mkdir -p "${HOME:-/home}/.local"
    LOCK_FILE="${HOME:-/home}/.local/.oma-pip-install.lock"
    (
      flock -x 9
      # OMA_SKIP_FRONTEND: the SPA is built at image-build/wheel time, never
      # at container start — and setup.py now fails loudly on frontend build
      # errors instead of swallowing them, which would abort container boot.
      OMA_SKIP_FRONTEND=1 python -m pip install --disable-pip-version-check --no-deps -e "${REPO_ROOT}"
    ) 9>"${LOCK_FILE}"
  fi
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

# Build the dashboard SPA — dashboard container only. ``web_dist/`` is
# gitignored and never baked into the image (the repo is bind-mounted,
# not COPY'd), so without this the operator has to ``npm run build`` on
# the host every time, which churns package-lock.json / .tanstack/ in
# their checkout. ``npm ci`` is run unconditionally (it never rewrites
# the lockfile): it wipes and reinstalls node_modules from the lock, so
# it self-heals a bind-mounted node_modules left over from a host
# (macOS-arch) build and picks up any dependency/lock bump — a
# directory-exists check would skip both. ``npm run build`` then runs
# every start so a ``git pull`` of frontend changes is reflected on
# restart. ~6s total on a service that restarts rarely; correctness
# beats the seconds. Best effort: a failure still leaves the legacy
# Jinja page at ``/``. Opt out with OMA_BUILD_FRONTEND=0 (e.g. you
# build on the host and accept the host-side churn).
if [[ "${OMA_BUILD_FRONTEND:-1}" != "0" && "${1:-}" == "oma-dashboard" \
      && -f "${REPO_ROOT}/dashboard-web/package.json" ]]; then
  if command -v npm >/dev/null 2>&1; then
    # &&-chained so ANY step failing makes the subshell exit non-zero
    # and route to the warning. A plain newline-separated list would
    # not: `set -e` is suppressed for a subshell used as an `if`
    # condition, so a failing `npm ci` would fall through to
    # `npm run build` and a lenient build could still print "OK".
    echo "[oma] dashboard: npm ci + npm run build (SPA)"
    if ( cd "${REPO_ROOT}/dashboard-web" \
         && npm ci --no-audit --no-fund \
         && npm run build ); then
      echo "[oma] dashboard: SPA build OK"
    else
      echo "[oma] dashboard: SPA build failed — serving legacy Jinja page at /" >&2
    fi
  else
    echo "[oma] dashboard: npm not found — serving legacy Jinja page at /" >&2
  fi
fi

echo "[oma] mount_root=${MOUNT_ROOT}"
echo "[oma] workdir=${WORKDIR}"
echo "[oma] config_path=${CONFIG_PATH}"
echo "[oma] command=$*"

exec "$@"
