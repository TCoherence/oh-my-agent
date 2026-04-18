# Upgrade Guide

Per-version upgrade procedures. Each section has the same shape: **what changed**, **what to back up**, **steps**, **verification**.

The "current → next" path is incremental: if you skip versions, run each section in order.

---

## General SOP — applies to every upgrade

```bash
# 1. Note current version
pip show oh-my-agent || git -C /path/to/oh-my-agent rev-parse HEAD

# 2. Stop the bot
docker compose down       # Docker
# or Ctrl-C the local process

# 3. Snapshot state
cp -a ~/.oh-my-agent ~/.oh-my-agent.backup-$(date +%Y%m%d)

# 4. Pull the new version
git pull
pip install -e .          # or rebuild the Docker image

# 5. Validate config before booting
oh-my-agent --validate-config

# 6. Start
docker compose up -d      # or `oh-my-agent` for local

# 7. Watch the first 60s of logs for warnings
tail -f ~/.oh-my-agent/runtime/logs/service.log
```

If anything goes wrong, restore the backup and downgrade:

```bash
rm -rf ~/.oh-my-agent
mv ~/.oh-my-agent.backup-YYYYMMDD ~/.oh-my-agent
git checkout <previous-version>
pip install -e .
```

---

## v0.7.x → v0.8.0

**What changed**:
- Service layer extracted from `discord.py` — adapter behavior unchanged for users.
- New `--validate-config` CLI flag.
- First-class `compose.yaml` at repo root.
- New `seattle-metro-housing-watch` skill bundled.

**What to back up**: standard SOP backup is sufficient. No schema change.

**Steps**:
1. Run general SOP.
2. (Optional) If you maintained a custom `compose.yaml`, diff it against the new one in repo root and reconcile.
3. (Optional) `oh-my-agent --validate-config` — pre-1.0 it only warns; you can ignore unfamiliar warnings.

**Verification**:
- `/doctor` reports `Bot online: true` and `Runtime health: enabled: true`.
- Existing automations and skills behave identically.

---

## v0.8.0 → v0.8.1

**What changed** (memory hygiene pass):
- New `MemoryEntry` schema fields (`scope`, `status`, `evidence`, `last_observed_at`, etc.).
- Lazy YAML migration on load — old files keep working.
- Two-stage dedup, fast/slow promotion, scope-aware bucketed retrieval.

**Back up**: `~/.oh-my-agent/memory/` (in addition to general SOP).

**Steps**: general SOP only. No manual migration.

**Verification**:
- After first restart, `/memories` lists existing entries with new fields populated (sensible defaults for old rows).
- `service.log` contains `memory_extract` / `memory_merge` / `memory_promote` / `memory_inject` lines on next idle judge fire.

---

## v0.8.1 → v0.8.2

**What changed**:
- `paper-digest` skill added.
- `youtube-podcast-digest` skill added.
- Per-automation `auto_approve` flag.
- Claude agent now uses `--output-format stream-json --verbose`.
- `agents/api/` deleted (deprecated since v0.4.0). Configs that still use `type: api` will fail validation.

**Back up**: `~/.oh-my-agent/automations/` (in addition to general SOP).

**Steps**:
1. Search your `config.yaml` for `type: api` — replace any such agent block with the equivalent `type: cli` block (Claude / Codex / Gemini).
2. (Optional) Add `auto_approve: true` to long-running automations (≥ 20 min) so they don't pile up in DRAFT. Existing bundled automations are pre-set.
3. Run general SOP.

**Verification**:
- `oh-my-agent --validate-config` returns no `type: api` errors.
- Long-running automations advance past DRAFT within one cron tick.

---

## v0.8.2 → v0.9.0 (BREAKING — memory subsystem rewrite)

**What changed**:
- The legacy daily/curated tier system, post-turn `MemoryExtractor`, and `/promote` slash command are **removed**.
- New: single-tier `JudgeStore` at `~/.oh-my-agent/memory/memories.yaml` + event-driven `Judge` agent.
- New triggers: thread idle (15 min default), explicit `/memorize`, natural-language keywords.
- Config rename: `memory.adaptive` → `memory.judge`. The old key is accepted as a fallback with a startup warning.

**Back up**: `~/.oh-my-agent/memory/` is **mandatory**. The migration script writes a backup automatically, but a separate copy is cheap insurance.

**Steps**:

1. Stop the bot.
2. Back up:
   ```bash
   cp -a ~/.oh-my-agent/memory ~/.oh-my-agent/memory.pre-v0.9
   ```
3. Run the migration script. From the repo root:
   ```bash
   # Dry-run first to see what it will do
   python scripts/migrate_memory_to_judge.py ~/.oh-my-agent/memory --dry-run

   # Then for real (curated-only)
   python scripts/migrate_memory_to_judge.py ~/.oh-my-agent/memory

   # Or also import daily entries:
   python scripts/migrate_memory_to_judge.py ~/.oh-my-agent/memory --include-daily
   ```
   The script writes its own backup directory next to the source.
4. Update `config.yaml`:
   ```diff
    memory:
      backend: sqlite
      path: ~/.oh-my-agent/runtime/memory.db
      max_turns: 20
      summary_max_chars: 500
   -  adaptive:
   +  judge:
        enabled: true
        memory_dir: ~/.oh-my-agent/memory
   -    promotion_threshold: 0.7
   +    inject_limit: 12
   +    idle_seconds: 900
   +    idle_poll_seconds: 60
   +    synthesize_after_seconds: 21600
   +    max_evidence_per_entry: 8
   +    keyword_patterns:
   +      - 记一下
   +      - remember this
   ```
   (Leaving `adaptive:` works but emits a deprecation warning — fix it once.)
5. Remove any references to `/promote` from your operational notes — the command no longer exists.
6. Start the bot.

**Verification**:
- `~/.oh-my-agent/memory/memories.yaml` exists and contains your migrated entries.
- `~/.oh-my-agent/memory/MEMORY.md` is regenerated within ~10 minutes (or earlier if a thread goes idle).
- `/memories` lists active entries.
- `/memorize "test pin"` writes a new entry and `/memories` shows it.
- No `MemoryExtractor` lines appear in `service.log` — only `memory_extract` (Judge) lines, and only on triggers.

**If you skip the migration**: the bot will start cleanly but with an empty memory store. Your old `daily/`, `curated.yaml`, and any `/promote` history are unreachable from the new code path until you run the script.

---

## v0.9.0 → v0.9.x (Phase A) — restart/recovery + warnings

**What changed**:
- New `tests/test_restart_recovery.py` and `tests/test_upgrade_paths.py` (developer-facing only).
- Startup now emits explicit deprecation warnings if it detects:
  - `memory.adaptive` in your config (use `memory.judge`).
  - Legacy `daily/` or `curated.yaml` files in `memory_dir` (run the v0.9.0 migration script).

**Back up**: standard SOP.

**Steps**: general SOP only.

**Verification**:
- If you migrated cleanly during v0.9.0, no new warnings appear.
- If warnings appear, they name the exact file path or config key — fix and restart.

---

## v0.9.x → v1.0 (BREAKING — Slack removed)

**What changed**:
- Slack is **not supported** in 1.0. The previous Slack adapter was a stub that never reached parity.
- `config_validator` now rejects `platform: slack` with an explicit error pointing to this guide.
- `src/oh_my_agent/gateway/platforms/slack.py` is deleted.
- This is part of the 1.0 contract freeze: 1.0 = Discord-only, single-user, self-hosted. Slack support may return post-1.0 as a real implementation.

**Back up**: standard SOP.

**Steps**:
1. If your `config.yaml` has any `gateway.channels[]` entry with `platform: slack`, **remove that entry**. The validator will reject the file and the bot will refuse to start.
2. (Optional) Delete the commented `# - platform: slack` example from your `config.yaml` for cleanliness.
3. Run general SOP.

**Verification**:
- `oh-my-agent --validate-config` returns 0.
- `/doctor` works as before.

**If you depended on the Slack stub**: nothing actually worked before; you weren't getting messages through it. The change is contractual, not behavioral. Track post-1.0 plans for real Slack support — file an issue with your use case.

---

## Future versions

This guide is updated per release. When a new version ships, a new section is added at the top — the existing sections do not change.
