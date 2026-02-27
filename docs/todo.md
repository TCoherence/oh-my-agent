# Todo / Roadmap

Items are organized by target version. Each feature annotates its dependencies with `⬅ depends on`.

See [future_planning_discussion.md](future_planning_discussion.md) for detailed rationale behind these decisions.

---

## Feature Dependency Graph

```mermaid
graph TD
    %% ── v0.3.0 (Done) ──────────────────────────────
    MEM["✅ Memory (SQLite + FTS5)"]
    COMP["✅ History Compression"]
    SKILL["✅ Skill System (SkillSync)"]
    FALLBACK["✅ Agent Registry Fallback"]

    %% ── v0.4.0 (Done) ──────────────────────────────
    DEPRECATE["✅ Deprecate API Agents"]
    CODEX["✅ Add Codex CLI Agent"]
    WORKSPACE["✅ Workspace Directory Isolation"]
    ENVSANITIZE["✅ Env Variable Sanitization"]
    SKILLCOPY["✅ Skill Copy to Workspace"]
    WRITE["✅ Add Write to Claude Tools"]
    REVERSE["✅ SkillSync Reverse Sync"]
    SLASH["✅ Slash Commands"]
    DIRECTMENTION["✅ Direct Agent Selection (@mention + /ask agent)"]
    README["✅ Update README"]
    RESUME["✅ CLI Session Resume"]
    MEMIO["✅ Memory Export/Import"]

    %% ── v0.4.0 (Deferred) ──────────────────────────
    STREAM["Streaming Responses"]

    %% ── v0.5.0 ─────────────────────────────────────
    SKILLCREATE["Agent-Driven Skill Creation"]
    SKILLTEST["Skill Testing / Validation"]
    SKILLPERM["Skill Permission Manifest"]
    XMEM["Cross-Session Memory Search"]

    %% ── v0.6.0 ─────────────────────────────────────
    ROUTING["Smart Agent Routing"]
    COLLAB["Agent Collaboration"]
    MENTION["Intent-Based Agent Selection"]

    %% ── Backlog ─────────────────────────────────────
    DOCKER["Docker-Based Isolation"]
    RATELIMIT["Rate Limiting"]

    %% ── Dependencies ────────────────────────────────

    %% v0.4 internal (sandbox isolation)
    WORKSPACE -->|"cwd for CLI sandbox"| CODEX
    WORKSPACE -->|"cwd for CLI sandbox"| SKILL
    SKILLCOPY -->|"copy skills into workspace"| WORKSPACE
    REVERSE --> SKILLCOPY
    DEPRECATE --> README
    CODEX --> README

    %% v0.4 → v0.5
    REVERSE --> SKILLCREATE
    WRITE --> SKILLCREATE
    SKILL --> REVERSE
    SKILLCREATE --> SKILLTEST
    SKILLCREATE --> SKILLPERM
    SLASH -->|"/search command"| XMEM
    MEM --> XMEM
    MEM --> MEMIO
    COMP --> RESUME

    %% v0.5 → v0.6
    CODEX --> ROUTING
    FALLBACK --> ROUTING
    ROUTING --> COLLAB
    ROUTING --> MENTION

    %% Backlog
    WORKSPACE --> DOCKER
    ENVSANITIZE --> DOCKER
    CODEX -->|"multi-agent needed"| RATELIMIT

    %% Style
    classDef done fill:#2d6a4f,stroke:#1b4332,color:#fff
    classDef v04 fill:#1d3557,stroke:#457b9d,color:#fff
    classDef v05 fill:#6a040f,stroke:#9d0208,color:#fff
    classDef v06 fill:#7b2cbf,stroke:#9d4edd,color:#fff
    classDef backlog fill:#495057,stroke:#6c757d,color:#fff

    class MEM,COMP,SKILL,FALLBACK,DEPRECATE,CODEX,WORKSPACE,ENVSANITIZE,SKILLCOPY,WRITE,REVERSE,SLASH,DIRECTMENTION,README,RESUME,MEMIO done
    class STREAM v04
    class SKILLCREATE,SKILLTEST,SKILLPERM,XMEM v05
    class ROUTING,COLLAB,MENTION v06
    class DOCKER,RATELIMIT backlog
```

### Dependency Summary

| Feature                           | Hard Dependencies                                                        | Soft / Recommended                  |
| --------------------------------- | ------------------------------------------------------------------------ | ----------------------------------- |
| **Deprecate API agents**          | None (independent)                                                       | —                                   |
| **Add Codex CLI agent**           | None (independent)                                                       | —                                   |
| **Workspace directory isolation** | None (independent)                                                       | Codex agent (sandbox scoped to cwd) |
| **Env variable sanitization**     | None (independent)                                                       | —                                   |
| **Skill copy to workspace**       | ⬅ SkillSync reverse sync, ⬅ Workspace isolation                          | —                                   |
| **Add `Write` to Claude tools**   | None (config change only)                                                | —                                   |
| **SkillSync reverse sync**        | ✅ Skill System (v0.3)                                                    | —                                   |
| **Streaming responses**           | None (independent)                                                       | —                                   |
| **Slash commands**                | None (independent, but `/search` only useful after cross-session memory) | Cross-session memory                |
| **Direct agent selection**        | ✅ Slash commands (`/ask agent`), ✅ Agent registry                       | —                                   |
| **Update README**                 | Deprecate API agents, Add Codex (wait for arch to settle)                | —                                   |
| **Agent-driven skill creation**   | ⬅ SkillSync reverse sync, ⬅ Add `Write` to Claude tools                  | Skill testing                       |
| **Skill testing / validation**    | ⬅ Agent-driven skill creation                                            | —                                   |
| **Skill permission manifest**     | ⬅ Agent-driven skill creation                                            | —                                   |
| **CLI session resume**            | ✅ History Compression (v0.3)                                             | —                                   |
| **Cross-session memory search**   | ✅ Memory (v0.3), ⬅ Slash commands (`/search`)                            | —                                   |
| **Memory export/import**          | ✅ Memory (v0.3)                                                          | —                                   |
| **Smart agent routing**           | ✅ Agent Registry (v0.3), ⬅ Add Codex (need ≥3 agents)                    | —                                   |
| **Agent collaboration**           | ⬅ Smart agent routing                                                    | —                                   |
| **Intent-based agent selection**  | ⬅ Smart agent routing                                                     | —                                   |
| **Docker-based isolation**        | ⬅ Workspace isolation, ⬅ Env sanitization (understand app-level first)   | —                                   |
| **Rate limiting**                 | ⬅ Add Codex (multi-agent concurrency increases load)                     | —                                   |

### Critical Paths

```
Path 1 (Self-Evolution):
  ✅ Skill System → ✅ SkillSync Reverse Sync → Agent-Driven Skill Creation → Skill Testing
                    ✅ Write Tool ────────────↗

Path 2 (Multi-Agent Intelligence):
  ✅ Codex CLI Agent ──→ Smart Agent Routing → Agent Collaboration
  ✅ Agent Registry ──↗                      → Intent-Based Agent Selection

Path 3 (Memory Evolution):
  ✅ Memory → Cross-Session Memory Search ← ✅ Slash Commands (/search)
```

### Unblocked Work (v0.5.0)

All v0.5.0 features are now unblocked:

1. **Agent-driven skill creation** (deps: ✅ reverse sync, ✅ Write tool)
2. **Cross-session memory search** (deps: ✅ Memory, ✅ `/search` command)

---

## v0.4.0 — CLI-First Cleanup + Skill Sync ✅

All v0.4.0 items complete. See **Done (v0.4.0)** section below for the full list.

Deferred to backlog:
- [ ] **Streaming responses** — planned as a status-monitor feature in a future release.

## v0.5.0 — Self-Evolution

- [ ] **Agent-driven skill creation** — user requests skill → agent creates it → auto sync. *(⬅ depends on: ✅ SkillSync reverse sync, ✅ Write tool)*
- [ ] **Skill testing / validation** — auto-verify newly created skills. *(⬅ depends on: Agent-driven skill creation)*
- [ ] **Skill permission manifest** — `permissions:` in SKILL.md frontmatter (network, filesystem, env_vars). Declarative capability control. *(⬅ depends on: Agent-driven skill creation)*
- [ ] **Cross-session memory search** — FTS5 search across threads via `/search`. *(⬅ depends on: ✅ Memory v0.3, ✅ Slash commands `/search`)*

## v0.6.0 — Multi-Agent Intelligence

- [ ] **Smart agent routing** — route by task type instead of simple fallback. *(⬅ depends on: ✅ Agent Registry v0.3, Add Codex — need ≥3 agents for routing to matter)*
- [ ] **Agent collaboration** — multi-agent workflows (write + review). *(⬅ depends on: Smart agent routing)*
- [ ] **Intent-based agent selection** — automatically choose/override agent by query type (beyond explicit `@agent`). *(⬅ depends on: Smart agent routing)*
- [ ] **Telegram adapter** — `gateway/platforms/telegram.py`. *(independent from agent features)*
- [ ] **Feishu/Lark adapter** — `gateway/platforms/feishu.py`. *(independent from agent features)*

## Backlog (Unprioritized)

- [ ] **Slack adapter** — implement `slack_sdk` async client. *(independent)*
- [ ] **Rate limiting / request queue** — per-session queue. *(⬅ soft dependency: Add Codex — more agents = more concurrency pressure)*
- [ ] **File attachment support** — download Discord attachments, pass to agent. *(independent)*
- [ ] **Markdown-aware chunking** — track code fence state in `chunker.py`. *(independent)*
- [ ] **SQLite → PostgreSQL migration** — swap `MemoryStore` backend. *(⬅ depends on: ✅ Memory v0.3, recommended after Memory export/import)*
- [ ] **End-to-end test with real Discord** — integration test against real server. *(independent)*
- [ ] **Docker-based agent isolation** — run CLI agents in containers. *(⬅ depends on: Enable CLI sandbox — understand CLI-level isolation first)*

## Maintenance / Quality

- [ ] **Linting / formatting** — add `ruff` to dev deps. *(independent)*
- [ ] **Type checking** — add `mypy` or `pyright`. *(independent)*
- [ ] **GitHub Actions CI** — `pytest` on push/PR. *(⬅ soft dependency: Linting/formatting — nice to lint in CI too)*

## Done (v0.4.0)

- [x] **Deprecate API agent layer** — `agents/api/` marked deprecated with warnings. Removed from `config.yaml.example`.
- [x] **Add `Write` to Claude allowed_tools** — config updated to `[Bash, Read, Write, Edit, Glob, Grep]`.
- [x] **Add Codex CLI agent** — `agents/cli/codex.py` using `codex exec --full-auto`.
- [x] **Sandbox isolation** — three-layer model (workspace cwd + env sanitization + CLI-native sandbox).
  - [x] **Workspace directory isolation** — top-level `workspace` config; `_setup_workspace()` creates dir, copies `AGENT.md` and skills; `BaseCLIAgent` sets `cwd=workspace`.
  - [x] **Environment variable sanitization** — `_build_env()` whitelist via `_SAFE_ENV_KEYS`; per-agent `env_passthrough` for explicit key forwarding.
  - [x] **Skill copy to workspace** — `_setup_workspace()` copies skills into `workspace/.claude/skills/` and `workspace/.gemini/skills/`.
- [x] **SkillSync reverse sync** — `reverse_sync()` imports agent-created skills back to `skills/`; `full_sync()` runs both directions on startup.
- [x] **Slash commands** — `/ask`, `/reset`, `/agent`, `/search` via `discord.app_commands`.
- [x] **Direct agent selection** — thread-level `@claude/@gemini/@codex` and `/ask` optional `agent` override.
- [x] **CLI session resume** — `ClaudeAgent` tracks session IDs per thread, uses `--resume` for subsequent messages.
- [x] **Memory export/import** — `MemoryStore.export_data()` / `import_data()` for JSON backup/restore.
- [x] **Update README.md** — rewritten for v0.4.0 CLI-first architecture with sandbox docs.

## Done (v0.3.0)

- [x] **Conversation memory within threads** — `MemoryStore` with SQLite backend.
- [x] **Memory compression** — `HistoryCompressor` auto-summarises old turns.
- [x] **Skill system** — `SkillSync` symlinks to CLI native dirs.
- [x] **Gemini CLI model update** — `gemini-3-flash-preview`.
- [x] **Gemini fallback** — `agents: [claude, gemini]` with ordered fallback.
