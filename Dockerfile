FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/oh-my-agent-src

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates tini nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY skills ./skills
COPY docs ./docs
COPY AGENTS.md AGENT.md CHANGELOG.md config.yaml.example .env.example ./
COPY docker/entrypoint.sh /usr/local/bin/oma-entrypoint

RUN chmod +x /usr/local/bin/oma-entrypoint \
    && npm install -g @anthropic-ai/claude-code @google/gemini-cli @openai/codex \
    && python -m pip install --upgrade pip \
    && python -m pip install -e .

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/oma-entrypoint"]
CMD ["oh-my-agent"]
