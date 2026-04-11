# Operator Guide

This guide covers installation, daily operation, diagnostics, and upgrade procedures for oh-my-agent.

---

## 1. Installation

### 1.1 Local Install (venv)

```bash
# Clone the repo
git clone https://github.com/TCoherence/oh-my-agent.git
cd oh-my-agent

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .

# Prepare config
cp config.yaml.example config.yaml
# Edit config.yaml — fill in DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, etc.

# (Optional) Create a .env file for secrets
echo 'DISCORD_BOT_TOKEN=your-token-here' >> .env
echo 'DISCORD_CHANNEL_ID=your-channel-id' >> .env

# Start
oh-my-agent
```

**Prerequisites**: Python ≥ 3.11, plus the CLI agents you reference in config (`claude`, `gemini`, `codex`) must be installed and on `PATH`.

### 1.2 Docker / Compose

```bash
# Build and start
docker compose up -d

# Follow logs
docker compose logs -f

# Stop
docker compose down
```

The `compose.yaml` at the repo root:
- Builds the image from `Dockerfile`
- Mounts the repo at `/repo` (editable install at container start)
- Persists runtime state in a named volume `oma-runtime`
- Forwards environment variables from the host `.env`

**First-run checklist**:
1. `cp config.yaml.example config.yaml` and fill in tokens
2. Create `.env` with `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, and any API keys
3. `docker compose up -d`

#### Using the shell scripts (alternative)

The `scripts/` directory provides finer-grained Docker control:

| Script | Purpose |
|--------|---------|
| `docker-build.sh` | Build the image |
| `docker-start.sh` | Start in detached mode with `--restart unless-stopped` |
| `docker-run.sh` | One-shot run (for debugging) |
| `docker-logs.sh` | Follow container logs |
| `docker-stop.sh` | Stop and remove the container |
| `docker-status.sh` | Show container status |

### 1.3 Config Validation

Before starting, you can validate your config:

```bash
oh-my-agent --validate-config
# or with a custom path:
oh-my-agent --config /path/to/config.yaml --validate-config
```

Exit code 0 = valid, exit code 1 = errors found. Warnings are printed but don't block startup.

---

## 2. Restart Procedure

### Local

```bash
# Ctrl-C to stop, then:
oh-my-agent
```

### Docker Compose

```bash
docker compose restart
# or full down/up cycle:
docker compose down && docker compose up -d
```

The bot gracefully shuts down running tasks on SIGTERM. Interrupted tasks transition to FAILED and can be retried.

---

## 3. Diagnostics

### 3.1 Service Log

Location: `~/.oh-my-agent/runtime/logs/service.log` (or inside the Docker volume).

Log format (structured key=value):
```
2026-04-10T10:20:11.123Z level=INFO logger=oh_my_agent.gateway.manager msg=agent running
```

Rotated daily, kept for `service_retention_days` (default: 7).

### 3.2 Discord Commands

| Command | Purpose |
|---------|---------|
| `/task_status <id>` | Show task state, step info, timing |
| `/task_list` | List all tasks with status |
| `/task_logs <id>` | Tail recent task log events |
| `/task_changes <id>` | Show files changed by a task |
| `/memories` | List adaptive memories (optional category filter) |
| `/search <query>` | Full-text search across conversation history |

### 3.3 Task Logs

Each runtime task writes events to the `runtime_events` SQLite table. Use `/task_logs <task_id>` to see the latest events, or query the database directly:

```bash
sqlite3 ~/.oh-my-agent/runtime/runtime.db \
  "SELECT timestamp, event_type, payload FROM runtime_events WHERE task_id='<id>' ORDER BY timestamp DESC LIMIT 20;"
```

---

## 4. Automation

### 4.1 Missed-Job Policy

The scheduler uses a **skip** policy for missed jobs. If the bot was offline when a job was scheduled to run, it does **not** retroactively fire. The job simply runs at its next scheduled interval.

### 4.2 Automation State

Automation run history is tracked in `runtime.db` (`automation_state` table). Use `/task_list` or query the database to inspect last-run timestamps and errors.

---

## 5. Backup

### 5.1 What to Back Up

| Path | Contents | Priority |
|------|----------|----------|
| `config.yaml` + `.env` | Configuration and secrets | Critical |
| `~/.oh-my-agent/runtime/memory.db` | Conversation history | High |
| `~/.oh-my-agent/runtime/runtime.db` | Task state, events, decisions | High |
| `~/.oh-my-agent/runtime/skills.db` | Skill telemetry and feedback | Medium |
| `~/.oh-my-agent/memory/` | Adaptive memory (daily + curated YAML) | Medium |
| `skills/` | Custom skills | Medium |
| `~/.oh-my-agent/runtime/logs/` | Service logs | Low (rotated) |

### 5.2 Backup Procedure

```bash
# Stop the bot first to ensure SQLite consistency
# Then copy the runtime directory:
cp -r ~/.oh-my-agent/runtime/ ~/backup/oh-my-agent-runtime-$(date +%Y%m%d)/
cp config.yaml ~/backup/
cp -r skills/ ~/backup/skills/
```

For Docker, the runtime state lives in the `oma-runtime` volume:
```bash
docker compose down
docker run --rm -v oma-runtime:/data -v $(pwd)/backup:/backup alpine \
  cp -r /data /backup/oma-runtime-$(date +%Y%m%d)
```

### 5.3 Restore

```bash
# Stop the bot
# Replace runtime files with backup:
cp -r ~/backup/oh-my-agent-runtime-YYYYMMDD/* ~/.oh-my-agent/runtime/
# Restart
oh-my-agent
```

---

## 6. Upgrade Procedure

### 6.1 Pre-Upgrade Checklist

1. **Back up** all runtime state (see §5)
2. **Check current schema version**:
   ```bash
   sqlite3 ~/.oh-my-agent/runtime/runtime.db \
     "SELECT version FROM schema_version;"
   ```
3. **Read release notes** for the target version

### 6.2 Performing the Upgrade

```bash
# Pull latest code
git pull origin main

# Reinstall
pip install -e .

# Validate config (new versions may add required fields)
oh-my-agent --validate-config

# Start
oh-my-agent
```

For Docker:
```bash
docker compose down
git pull origin main
docker compose build
docker compose up -d
```

Schema migrations run automatically on startup. The bot logs the current schema version at boot:
```
level=INFO logger=oh_my_agent.memory.store msg=Schema version: 1
```

### 6.3 Rollback

If an upgrade causes issues:

1. Stop the bot
2. Restore the backup (§5.3)
3. Check out the previous version: `git checkout v0.7.3` (or the tag you were on)
4. Reinstall: `pip install -e .`
5. Restart

> **Note**: Forward-migrated databases cannot be used with older code versions. Always restore from backup when rolling back.

### 6.4 Version Compatibility Matrix

| From | To | Migration | Notes |
|------|----|-----------|-------|
| 0.7.x | 0.8.x | Automatic | Schema v1; config gains `logging` block (optional) |
| < 0.5.2 | ≥ 0.5.2 | Automatic | Legacy `.workspace/` migrated to `~/.oh-my-agent/` |
| monolith DB | split DBs | Automatic | `memory.db` split into `memory.db` + `runtime.db` + `skills.db` |

### 6.5 What Gets Migrated

- **SQLite schema**: version tracked in `schema_version` table; forward-only migrations run at startup
- **Config format**: new optional sections added by `_apply_v052_defaults()`; existing configs remain valid
- **Runtime paths**: legacy `.workspace/` auto-migrated to `~/.oh-my-agent/` tree
- **Memory store**: monolith `memory.db` auto-split into conversation / runtime / skills DBs
