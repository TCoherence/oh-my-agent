# Troubleshooting

Concrete fault patterns and how to diagnose them. Each entry follows the same shape: **symptom → diagnostic command → resolution**.

If a pattern here does not match what you see, capture the relevant `~/.oh-my-agent/runtime/logs/service.log` slice and the output of `/doctor`, then file an issue.

---

## 1. The bot does not respond on Discord

**Symptom**: messages in the configured channel produce no reply, no typing indicator, and no log activity for that message.

**Diagnose**:

```bash
# Is the process alive?
docker compose ps                # Docker
ps aux | grep oh-my-agent        # Local

# What does the bot think its channel is?
grep -E 'channel_id|owner_user_ids' config.yaml

# Did Discord even deliver the message?
tail -n 200 ~/.oh-my-agent/runtime/logs/service.log | grep -i 'on_message\|received'
```

**Resolve**:

- If process is dead: restart and check the last 50 log lines for the crash trace.
- If the channel id in `config.yaml` does not match the channel where you typed: the bot ignores out-of-channel messages by design.
- If `access.owner_user_ids` is set and your Discord user id is not in it: the gate drops the message silently (this is intentional). Add your id or remove the gate.
- If the gateway intent is missing: re-invite the bot with the **Message Content Intent** enabled in the Discord developer portal.

---

## 2. Task stuck in DRAFT

**Symptom**: `/task_start` (or an automation) created a task, but it never advances out of `DRAFT`.

**Diagnose**:

```bash
# What does the task think it is?
sqlite3 ~/.oh-my-agent/runtime/runtime.db \
  "SELECT id, type, state, risk_level, risk_reason FROM tasks ORDER BY created_at DESC LIMIT 5;"

# Anyone scheduled to approve it?
grep -E 'risk|draft' ~/.oh-my-agent/runtime/logs/service.log | tail -n 40
```

**Resolve**:

- DRAFT means the risk evaluator flagged this task. Use `/task_approve <id>` to promote it, or `/task_reject <id>` to drop it.
- For auto-fired automations that should skip risk: set `auto_approve: true` on that automation in `~/.oh-my-agent/automations/<name>.yaml`.
- If you want a class of tasks to bypass DRAFT entirely, lower the threshold under `runtime.risk_evaluation.*` — but this disables the safety gate, so prefer per-automation `auto_approve`.

---

## 3. Task hangs in RUNNING (or VALIDATING) forever

**Symptom**: `/task_status <id>` shows the task as `RUNNING` for far longer than `default_max_minutes`. No new logs about that task.

**Diagnose**:

```bash
# Heartbeat log line per task — search for the task id:
grep '<task_id>' ~/.oh-my-agent/runtime/logs/service.log | tail -n 30

# Is the agent subprocess still alive?
ps aux | grep -E 'claude|gemini|codex'
```

**Resolve**:

- Use `/task_stop <id>` from the thread — the heartbeat loop will cancel the agent subprocess on the next tick (≤ 5 s).
- If the agent process is gone but the task did not transition: check `service.log` for `agent fallback` or unhandled exceptions; the task likely needs `/task_resume <id>` or `/task_discard <id>`.
- For repeated hangs on the same skill, check the per-skill timeout in `skills.evaluation.<name>.timeout` — a too-tight value can cause `VALIDATING` to never resolve.

---

## 4. Agent fallback loop — every agent times out

**Symptom**: each `/ask` produces `AgentRegistry: all agents exhausted` or every reply is from the second-tier fallback agent.

**Diagnose**:

```bash
# Which agents are configured and in which order?
grep -A1 'agents:' config.yaml

# Are the CLI binaries actually on PATH from the bot's environment?
docker compose exec oh-my-agent which claude codex gemini   # Docker
.venv/bin/python -c "import shutil; print(shutil.which('claude'))"  # Local

# What did the failing agent say?
grep -E 'agent_run|agent fallback|SubprocessError' ~/.oh-my-agent/runtime/logs/service.log | tail -n 40
```

**Resolve**:

- Missing binary: install the CLI in the same environment as the bot (or mount it into the container).
- Auth missing: run the `/auth_status` command — if any agent reports `unauthorized`, run `/auth_login <agent>` and follow the prompted flow.
- Per-agent `env_passthrough` not exporting the API key: confirm the env var is whitelisted in `agents.<name>.env_passthrough`. With `workspace` set, the env is otherwise sanitized.
- Persistent timeouts: bump `agents.<name>.timeout` (seconds) — the default is conservative.

---

## 5. Automation never fires

**Symptom**: the YAML under `~/.oh-my-agent/automations/` exists, but the schedule never triggers.

**Diagnose**:

```bash
# What does the scheduler see?
grep -E 'scheduler|automation' ~/.oh-my-agent/runtime/logs/service.log | tail -n 40

# Status of all automations from inside Discord:
/automation_status
```

**Resolve**:

- File not loaded: check the YAML for parse errors via `oh-my-agent --validate-config`. Hot-reload only picks up syntactically valid files.
- `enabled: false` in the YAML — set to `true` or use `/automation_enable <name>`.
- Cron expression wrong: paste the expression into a cron checker; the scheduler uses standard 5-field cron (no seconds field).
- The automation fired but its task was rejected by risk eval: see Pattern 2 (DRAFT). Set `auto_approve: true` if intended.
- Try a manual fire with `/automation_run <name>` to bisect scheduling vs. execution.

---

## 6. Memory is never injected into prompts

**Symptom**: `/memories` lists active entries, but the agent never sees them in `[Remembered context]`.

**Diagnose**:

```bash
# Did the judge actually run? Look for memory_extract trace lines.
grep -E 'memory_extract|memory_inject' ~/.oh-my-agent/runtime/logs/service.log | tail -n 40

# Is the YAML file the bot reads the same one /memories shows?
ls -la ~/.oh-my-agent/memory/memories.yaml
```

**Resolve**:

- The judge only fires on explicit `/memorize`, on detected keyword patterns (configured under `memory.judge.keyword_patterns`), or after `idle_seconds` of silence (default 900). It does not run after every turn.
- Injection is scope-aware. A `thread`-scoped memory will not appear in a different thread; a `workspace`-scoped one only injects when the active workspace matches.
- `superseded` entries are never injected. Use `/memories` and confirm the entry is `status=active`.
- `memory.judge.inject_limit` (default 8) caps how many entries make it into the prompt block; raise it if your set is large but relevant.

---

## 7. HITL prompt was sent but the buttons do nothing

**Symptom**: a `WAITING_USER_INPUT` checkpoint posted a button view, but clicking it produces no log line and no state transition.

**Diagnose**:

```bash
# Is the bot still alive? Discord button views become stale on restart.
docker compose ps

# Did rehydration run after a restart?
grep '_rehydrate_hitl_prompt_views' ~/.oh-my-agent/runtime/logs/service.log | tail -n 5
```

**Resolve**:

- After a restart, button callbacks must be re-registered. This is automatic via `_rehydrate_hitl_prompt_views()`. If the log line is missing, the rehydrator did not run — most often because the channel registry is not yet ready. Restart once more and check.
- If the rehydrator did run but buttons still do nothing, you may have hit a Discord interaction-token expiry (15 min). Use the slash-command equivalent (`/task_resume <id> <answer>`) instead.

---

## 8. Merge gate blocks a repo_change task indefinitely

**Symptom**: a `repo_change` task reaches `WAITING_MERGE` and never moves on. `/task_merge <id>` returns an error.

**Diagnose**:

```bash
# Inspect the worktree:
ls ~/.oh-my-agent/runtime/tasks/<task_id>/

# Was the test/validation step actually green?
grep '<task_id>' ~/.oh-my-agent/runtime/logs/service.log | grep -iE 'test|valid'
```

**Resolve**:

- Tests failing: open the worktree, reproduce the failure manually, then either fix in the worktree and `/task_merge`, or `/task_discard <id>` and rebuild the task.
- Working tree dirty in the parent repo: the merge step refuses to clobber uncommitted changes. Stash or commit your local edits first.
- For one-off bypass: `/task_merge <id>` accepts force only when configured under `runtime.merge.allow_force`. Default is off — leaving it off is the right choice.

---

## 9. Skill auto-disabled — runs that used to work now skip

**Symptom**: a previously healthy skill is now silently skipped. `/skill_stats <name>` shows `auto_disabled: true`.

**Diagnose**:

```bash
# Recent run health:
/skill_stats <name>

# What did the auto-disable threshold consider?
sqlite3 ~/.oh-my-agent/runtime/memory.db \
  "SELECT skill_name, auto_disabled_reason FROM skill_provenance WHERE skill_name = '<name>';"
```

**Resolve**:

- Re-enable with `/skill_enable <name>`. This clears the auto-disable flag immediately.
- If the skill is still failing for a real reason (broken script, wrong env, third-party API down), it will re-trip the threshold. Read the recent invocations under `skill_invocations` to see the actual error.
- Tune sensitivity: the threshold lives under `skills.evaluation.auto_disable.*` in `config.yaml`. Default ratios are intentionally strict — loosen only if false positives are common.

---

## 10. Rate limiter saturation — bot drops messages

**Symptom**: rapid bursts of messages produce only the first few replies; later messages return a "rate limited" notice or are silently dropped.

**Diagnose**:

```bash
grep -E 'rate.?limit|throttle' ~/.oh-my-agent/runtime/logs/service.log | tail -n 40
```

**Resolve**:

- This is by design. The limiter protects upstream APIs from cost/quota blowups.
- For genuine bulk workloads, prefer `/task_start` (one autonomous task) over a flurry of `/ask` turns.
- The gateway-level limits live under `gateway.rate_limit` (or platform-specific subkey). Raise carefully and watch agent-side cost.

---

## 11. Config validation fails on startup

**Symptom**: `oh-my-agent` exits immediately with `Config validation failed:` and a list of errors.

**Diagnose**:

```bash
# Run the validator standalone — it prints structured errors:
oh-my-agent --validate-config
oh-my-agent --config /path/to/config.yaml --validate-config

# Compare against the canonical example:
diff config.yaml.example config.yaml | head -n 60
```

**Resolve**:

- Each error names the failing key path. Fix the listed key, then re-run.
- Common cases: unknown agent type, missing `cli_path`, unsupported platform (Slack is no longer supported in 1.0 — see [upgrade-guide.md](upgrade-guide.md)), `${ENV_VAR}` referenced but unset.
- Warnings (printed but non-fatal) include deprecated config aliases such as `memory.adaptive` (renamed to `memory.judge`).

---

## 12. CLI session does not resume — every turn starts cold

**Symptom**: replies feel context-free; the agent does not remember its prior turn within the same thread.

**Diagnose**:

```bash
# Is the session row actually persisted?
sqlite3 ~/.oh-my-agent/runtime/memory.db \
  "SELECT platform, channel_id, thread_id, agent, session_id FROM agent_sessions ORDER BY updated_at DESC LIMIT 10;"

grep -E 'session resume|--resume' ~/.oh-my-agent/runtime/logs/service.log | tail -n 20
```

**Resolve**:

- Only Claude supports session resume today. Codex and Gemini are stateless per turn — pass the relevant history via the prompt.
- If Claude is the active agent but `agent_sessions` is empty, the prior turn likely failed or returned a non-success response (rows are written only on success). Check the prior turn's log line for the agent outcome.
- After clearing memory with `/reset`, the row is dropped and the next turn starts a new CLI session. This is expected.

---

## 13. Image attachments are ignored

**Symptom**: you upload an image alongside a question; the agent replies but never mentions the image.

**Diagnose**:

```bash
grep -E 'attachment|image' ~/.oh-my-agent/runtime/logs/service.log | tail -n 30
```

**Resolve**:

- Only `image/*` MIME types ≤ 10 MB are forwarded. Other types are dropped silently.
- Codex uses `--image` natively. Claude and Gemini receive a copy under `workspace/_attachments/` plus an instruction in the prompt — without `workspace`, the copy step is skipped and the image reference may not resolve. Configure `workspace` (see [config-reference.md](config-reference.md)).
- Image-only messages get a default analysis prompt; if you want a specific question, include text in the same message.

---

## 14. Skill changes don't show up after editing

**Symptom**: edited a `SKILL.md` or its scripts, but the agent still uses the old version.

**Diagnose**:

```bash
# Is the symlink current?
ls -la .claude/skills/<name>
ls -la .gemini/skills/<name>

# When did the bot last sync?
grep -E 'skill_sync|full_sync' ~/.oh-my-agent/runtime/logs/service.log | tail -n 20
```

**Resolve**:

- Run `/reload-skills` from Discord. This triggers `full_sync()` and revalidates every skill.
- Validation failure blocks the reload — the slash command reports which skill failed and why.
- With `workspace` configured, skills are **copied** (not symlinked) into the workspace. `/reload-skills` re-copies; without it, edits in `skills/` are not reflected in the workspace until reload.

---

## 15. `/doctor` reports a red status

**Symptom**: `/doctor` highlights a section in red.

**Resolve**: each red section corresponds to a class of issue. See [monitoring.md](monitoring.md) for the section-by-section glossary.

---

## 16. Runtime task fails with `max_turns`

**Symptom**: a runtime task (from `/task_start` or an automation) ends in `FAILED` with an error that mentions "max_turns" or "reached maximum number of turns". For automation-sourced tasks, the thread has a partial result then nothing.

**Diagnose**:

```bash
# Which task and which agent hit it?
grep 'hit max turns' ~/.oh-my-agent/runtime/logs/service.log | tail -n 10

# Confirm the task's budget (not the skill's config, the *task* row):
sqlite3 ~/.oh-my-agent/runtime/memory.db \
  "SELECT id, automation_name, skill_name, agent_max_turns, status, error FROM runtime_tasks WHERE status='FAILED' ORDER BY ended_at DESC LIMIT 5;"

# What does the skill advertise?
grep -E 'max_turns|timeout_seconds' skills/<name>/SKILL.md
```

**Resolve**:

- **One-shot rescue**: the thread should carry a "Re-run +30 turns" button (primary style). Click it to spawn a sibling task with `agent_max_turns = parent + 30` (fallback base 25). The button TTL is `runtime.decision_ttl_minutes` (default 24 h). No button surfacing? Confirm `owner_user_ids` is set and the `_surface_rerun_bump_turns_button` log line fires after the failure — if not, you probably hit the failure in the chat path (`/ask` or bare slash skill), which doesn't own a runtime task. Re-invoke via `/task_start` or an automation to get the button.
- **Persistent fix**: bump the skill's `metadata.max_turns` in `skills/<name>/SKILL.md` (typical: 40–60 for multi-source digests). `timeout_seconds` alone is not enough — claude uses `--max-turns` independently. After editing, run `/reload-skills`.
- **Not a skill task**: `/task_start` tasks inherit claude's default 25 turns. Raise the ceiling by either creating a dedicated skill with higher `max_turns`, or accepting the one-shot button bump for the current run.
- Do **not** expect retry to help — `max_turns` is classified as terminal (retrying just consumes the same budget and fails again). See [task-model.md §7](task-model.md) for the full retry-vs-terminal taxonomy.

---

## When to escalate

If none of the patterns match, capture and attach to a GitHub issue:

1. The last 200 lines of `~/.oh-my-agent/runtime/logs/service.log`
2. The output of `/doctor`
3. The redacted `config.yaml` (strip tokens)
4. The version (`pip show oh-my-agent` or the `git rev-parse HEAD` of your checkout)
