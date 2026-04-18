# Monitoring

What to watch in production: log patterns that warrant alerts, and how to read each section of `/doctor`.

The bot is single-user / single-host by design. There is no Prometheus exporter; monitoring is done by tailing `service.log` and running `/doctor` on demand.

---

## 1. Service log

**Location**: `~/.oh-my-agent/runtime/logs/service.log` (rotated daily, retained `service_retention_days` days, default 7).

**Format**: each line is `<ISO-timestamp> level=<LEVEL> logger=<module> msg=<message>`. Most operational lines start with `[<request_id>]` where the request id is an 8-char prefix that lets you stitch one inbound message into all its downstream lines.

```bash
# Live tail with grep filter
tail -f ~/.oh-my-agent/runtime/logs/service.log | grep -E 'WARN|ERROR'

# All lines for one request
grep '\[abc12345\]' ~/.oh-my-agent/runtime/logs/service.log
```

---

## 2. Log patterns to alert on

Group these into "page on first occurrence" (P0) and "page on threshold" (P1).

### P0 — first occurrence is interesting

| Pattern (regex) | What it means | First action |
|---|---|---|
| `AgentRegistry: all agents exhausted` | Every configured agent failed for one message | Check Pattern 4 in [troubleshooting.md](troubleshooting.md) |
| `AGENT_ERROR purpose=` | An agent subprocess raised | Read the trailing `error=` field; cross-reference the request id |
| `Gateway shutdown timed out` | SIGTERM hit the deadline before drain finished | Check the named in-flight tasks; consider raising `runtime.shutdown_timeout_seconds` |
| `Failed to signal scheduler stop` | Scheduler did not exit cleanly during shutdown | A leaked job task is likely; capture the trace before next restart |
| `CONTROL_FRAME_AUTH_REQUIRED` | An agent emitted an auth-required frame | Authenticate via `/auth_login <agent>`; tasks will requeue once approved |

### P1 — alert on rate / threshold

| Pattern (regex) | Threshold | Likely cause |
|---|---|---|
| `agent fallback` | > 5 / hour | Primary agent is unhealthy (binary missing, network, quota) |
| `IGNORE unauthorized user` | sudden burst | Outsider activity in your channel; review `access.owner_user_ids` |
| `CONTROL_FRAME_PARSE_FAILED` | > 1 / hour | An agent is emitting malformed control frames; check agent version |
| `SKILL_SYNC failed` | any sustained pattern | A skill on disk is invalid; run `/reload-skills` to surface the validator error |
| `COMPRESS failed` | > 1 / day | History compaction broken — long threads will keep growing |
| `Memory injection failed` | > 1 / day | `JudgeStore` read path threw — check `memories.yaml` integrity |
| `Recent failures` (in `/doctor` Scheduler health) | non-zero | One or more automation runs errored; inspect listed `<name>: <err>` lines |
| `rate.?limit\|throttle` | > 10 / hour | Burst load saturating the gateway-side limiter; see Pattern 10 in [troubleshooting.md](troubleshooting.md) |

---

## 3. Reading `/doctor`

`/doctor` returns a markdown report grouped into the sections below. Use it as your first stop after any reported anomaly.

### 3.1 Gateway health

```
- Bot online: `true`
- Channel bound: `<channel_id>`
```

| Field | Healthy | What red means |
|---|---|---|
| `Bot online` | `true` | Discord client lost connection — see [troubleshooting.md](troubleshooting.md) Pattern 1 |
| `Channel bound` | matches your `config.yaml` | Wrong channel id; messages outside this channel are ignored |

### 3.2 Runtime health

```
- Enabled: `true`
- Workers: `2`
- Default agent: `claude`
- Active tasks: `0`
- Recent tasks: `12`
- Task counts:
  - DRAFT: 0
  - RUNNING: 0
  - WAITING_MERGE: 0
  - WAITING_USER_INPUT: 0
  - BLOCKED: 0
```

| Field | What to watch |
|---|---|
| `Enabled` | `false` means runtime tasks are off entirely; only chat works |
| `Workers` | Concurrent task limit; raising costs more API budget |
| `Active tasks` | Sustained > workers means tasks queue up — check for stuck jobs |
| `DRAFT` count | Tasks waiting on human approval; combine with `/task_list` |
| `RUNNING` count | Currently executing; cross-reference with `ps aux \| grep -E 'claude\|gemini\|codex'` |
| `WAITING_MERGE` count | Repo-change tasks awaiting merge gate |
| `WAITING_USER_INPUT` count | HITL prompts pending user reply |
| `BLOCKED` count | Should normally be 0; non-zero means a dependency cycle or auth wait |

### 3.3 HITL health

```
- Active prompts: `3`
  - waiting: `2`
  - resolving: `1`
```

| Field | Meaning |
|---|---|
| `Active prompts` | Total open prompts in this channel |
| `waiting` | Posted to user, no response yet |
| `resolving` | User answered, agent is consuming the answer |

A prompt sitting in `resolving` for more than a minute usually means the agent crashed mid-resume; check `service.log` for the parent task id.

### 3.4 Scheduler health

```
- Enabled: `true`
- Loaded automations: `4`
- Active jobs: `4`
- Recent failures: `1`
  - market_briefing: HTTP 502 from upstream feed
```

| Field | Meaning |
|---|---|
| `Enabled` | False means no scheduler instance was constructed at startup |
| `Loaded automations` | Count of YAML files that parsed successfully |
| `Active jobs` | Count of active scheduler entries; if `Active jobs` < `Loaded automations`, some are disabled |
| `Recent failures` | Per-automation last error; the line below names the failing automation and the truncated error |

### 3.5 Auth health

```
- Active auth waits: `0`
```

Non-zero means at least one task is suspended awaiting `/auth_login`. Run `/auth_status` for details.

### 3.6 Log pointers

Just paths — verify they exist and are writable:

```
- Service log: `/Users/.../runtime/logs/service.log`
- Thread log root: `/Users/.../runtime/logs/threads`
```

### 3.7 Recent failure hints (conditional)

This section appears only when there were recent failures. The block is a verbatim text excerpt of the most recent failed task / agent error — use it to skip the log dive when the problem is fresh.

---

## 4. Disk usage

The bot writes to:

| Path | Growth driver | Cleanup |
|---|---|---|
| `~/.oh-my-agent/runtime/logs/service.log*` | Every request | Auto-rotated; configured by `runtime.service_retention_days` |
| `~/.oh-my-agent/runtime/logs/threads/<id>/` | Per-task verbose logs | Janitor sweeps after `runtime.cleanup.thread_log_retention_hours` |
| `~/.oh-my-agent/runtime/tasks/<task_id>/` | Per-task worktrees | Janitor sweeps after `runtime.cleanup.task_workspace_retention_hours` |
| `~/.oh-my-agent/runtime/memory.db` | Conversation history | Compaction trims old turns; manual prune via SQL |
| `~/.oh-my-agent/runtime/runtime.db` | Task state | Janitor deletes terminal tasks after retention |
| `~/.oh-my-agent/memory/memories.yaml` | Judge entries | Bounded by `memory.judge.max_memories` |

Spot check growth:

```bash
du -sh ~/.oh-my-agent/runtime/* ~/.oh-my-agent/memory/*
```

---

## 5. Cost / budget signals

Cost is dominated by agent subprocess token usage. The bot does not bill directly, but you can correlate:

| Source | What to count |
|---|---|
| `AGENT_OK purpose=... elapsed=Xs response_len=Y` lines | One agent turn per line; long responses trend high |
| Per-skill `/skill_stats <name>` | `recent_invocations` × your average per-invocation cost |
| Provider dashboard (Anthropic / OpenAI / Google) | Authoritative spend |

If you suspect a runaway: `grep AGENT_RUNNING ~/.oh-my-agent/runtime/logs/service.log | tail -n 50` to see which threads are running back-to-back.
