FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# The image only carries runtime dependencies and helper CLIs.
# Project source is mounted later at /repo during `docker run`.
WORKDIR /opt/oh-my-agent-image

# Base system tools plus tini and Node-based agent CLIs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates tini nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY docker/entrypoint.sh /usr/local/bin/oma-entrypoint

RUN chmod +x /usr/local/bin/oma-entrypoint

# Preinstall the external agent CLIs so the container can fail fast if config references them.
RUN npm install -g @anthropic-ai/claude-code @google/gemini-cli @openai/codex

RUN python -m pip install --upgrade pip

# Extract Python runtime dependencies from pyproject.toml without baking repo source into the image.
# Includes the `dashboard` optional-deps group so the `oma-dashboard` entry point works in the
# same image without a second pip install at container start. Single tomllib extract keeps base
# + dashboard versions in lockstep with pyproject.toml — no drift.
RUN python - <<'PY' >/tmp/oma-requirements.txt
import tomllib
from pathlib import Path
data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
project = data.get("project", {})
for dep in project.get("dependencies", []):
    print(dep)
for dep in project.get("optional-dependencies", {}).get("dashboard", []):
    print(dep)
PY

# Install runtime deps now; the mounted repo itself is installed editable at container start.
RUN python -m pip install -r /tmp/oma-requirements.txt 'setuptools>=75' \
    && rm -f /tmp/oma-requirements.txt

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/oma-entrypoint"]
CMD ["oh-my-agent"]
