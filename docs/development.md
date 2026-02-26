# Development Log

## Project Overview

**Oh My Agent** — a multi-platform bot that uses CLI-based AI agents (Claude, Gemini, etc.) as the execution layer, instead of calling model APIs directly. Inspired by OpenClaw.

> **Architecture direction (v0.4+):** CLI-first. API agents (`agents/api/`) are deprecated — CLI agents provide a complete agentic loop (tool use, skills, context management) that API SDK calls cannot match without significant reimplementation. See [future_planning_discussion.md](future_planning_discussion.md) for full rationale.

---

## v0.4.0 — CLI-First Cleanup + Skill Sync

### What Changed

1. **Deprecated API agent layer** — `agents/api/` marked deprecated with `DeprecationWarning`. Removed from `config.yaml.example`. Code kept for reference.
2. **Added `Write` to Claude allowed_tools** — config default now `[Bash, Read, Write, Edit, Glob, Grep]`.
3. **Added Codex CLI agent** — `agents/cli/codex.py` using `codex exec --full-auto` (auto-approve + OS-level sandbox).
4. **SkillSync reverse sync** — `SkillSync.reverse_sync()` detects non-symlink skill directories in `.gemini/skills/` and `.claude/skills/`, copies them back to `skills/`. `full_sync()` runs reverse then forward on startup.
5. **Slash commands** — `/ask`, `/reset`, `/agent`, `/search` via `discord.app_commands.CommandTree`. Synced on bot startup.
6. **CLI session resume** — `ClaudeAgent` tracks session IDs per thread. Uses `--resume <session_id>` + `--output-format json` to continue sessions without re-flattening history. Falls back to fresh session if resume fails.
7. **Memory export/import** — `MemoryStore.export_data()` returns all turns + summaries as JSON. `import_data()` restores from backup.
8. **Updated README** — rewritten for CLI-first architecture with agent comparison table.

### New Files

```
src/oh_my_agent/agents/cli/codex.py    # CodexCLIAgent
```

### Architecture Changes

```
BaseAgent
  └── BaseCLIAgent
        ├── ClaudeAgent  (session resume)
        ├── GeminiCLIAgent
        └── CodexCLIAgent (new)

SkillSync
  ├── sync() → forward only
  ├── reverse_sync() → CLI dirs → skills/
  └── full_sync() → reverse + forward

AgentRegistry
  └── run(thread_id=...) → forwarded to agents supporting session resume
```

### Discussion (Retained from Planning)

### Discussion: Can CLI Agents Edit Files?

**Short answer: Yes, they already can.** But the current config happens to restrict Claude.

| CLI | File Editing | Current Config | What To Change |
|-----|-------------|----------------|----------------|
| Claude | Built-in `Edit` tool, also `Write` for new files | `allowed_tools: [Bash, Read, Edit, Glob, Grep]` — **Edit is already included** | Already works. Add `Write` to `allowed_tools` if creating new files is needed. |
| Gemini | Uses shell commands (cat, sed, etc.) via `--yolo` | No tool restrictions — has full access | Already works. |
| Codex | Built-in file editing within workspace | `--sandbox workspace-write` restricts to cwd | Will work out-of-box once integrated. |

Key insight: Claude's `--allowedTools` controls which built-in tools the agent can use. The current config already includes `Edit` (modify existing files) and `Bash` (can also edit via shell). To allow creating entirely new files, add `Write` to `allowed_tools`:

```yaml
agents:
  claude:
    allowed_tools: [Bash, Read, Write, Edit, Glob, Grep]
    #                           ^^^^^ add this for new file creation
```

Gemini with `--yolo` has unrestricted tool access — it can read, write, and execute anything.

### Discussion: Codex CLI Integration

OpenAI Codex CLI (`codex`) is a local coding agent similar to Claude CLI and Gemini CLI. Key differences:

- **Non-interactive mode**: `codex exec "<prompt>"` (vs `claude -p` and `gemini -p`)
- **Built-in sandbox**: `--sandbox workspace-write` restricts writes to cwd, blocks network. OS-level enforcement.
- **Approval policy**: `--ask-for-approval on-request` for headless mode (similar to `--dangerously-skip-permissions`)
- **Shortcut**: `--full-auto` = `--ask-for-approval on-request` + `--sandbox workspace-write` — the ideal mode for oh-my-agent
- **Quiet mode**: `-q` suppresses interactive prompts, good for subprocess

Proposed implementation for `agents/cli/codex.py`:

```python
class CodexCLIAgent(BaseCLIAgent):
    def _build_command(self, prompt: str) -> list[str]:
        cmd = [
            self._cli_path,
            "exec", prompt,          # non-interactive mode
            "--full-auto",           # auto-approve + workspace sandbox
            "-q",                    # suppress prompts for subprocess
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        return cmd
```

```yaml
# config.yaml
agents:
  codex:
    type: cli
    cli_path: codex
    model: gpt-5-codex        # or o4-mini, etc.
```

### Discussion: Sandbox / Isolation

All three CLI agents support some form of sandbox. Comparison:

| Feature | Claude CLI | Gemini CLI | Codex CLI |
|---------|-----------|-----------|----------|
| **Sandbox mechanism** | Apple Seatbelt (macOS) / bubblewrap (Linux) | Seatbelt (macOS) / Docker/Podman (Linux) | OS-level (macOS/Linux) |
| **Enable flag** | `/sandbox` in interactive, or auto-allow mode | `-s` / `--sandbox` | `--sandbox <mode>` |
| **File restriction** | Read/write within cwd only | Writes restricted to project dir | Writes restricted to cwd |
| **Network isolation** | Proxy-based, approved domains only | Configurable via sandbox profile | Blocked by default |
| **Headless activation** | Not yet a CLI flag (feature requested); current workaround is `--dangerously-skip-permissions` + `--allowedTools` | `--sandbox` works in headless | `--sandbox workspace-write` works in headless |
| **Docker option** | Docker Sandbox (microVM) available | Container-based sandbox available | N/A |

**Recommended approach for oh-my-agent:**

1. **Codex**: use `--full-auto` which includes `--sandbox workspace-write` — sandbox is on by default.
2. **Gemini**: add `--sandbox` flag to `_build_command()`. Minimal change.
3. **Claude**: `--dangerously-skip-permissions` + `--allowedTools` is the current isolation mechanism. True sandbox (`/sandbox`) is interactive-only for now. Monitor for a `--sandbox` CLI flag.
4. **Long-term**: for production, consider running all CLI agents inside Docker containers for full process isolation. This is orthogonal to CLI-level sandbox and provides defense-in-depth.

### Architecture Impact

```
BaseAgent
  └── BaseCLIAgent  →  claude, gemini, codex
       │
       └── agents/api/  DEPRECATED — no new development
```

SkillSync becomes bidirectional:

```
skills/ (canonical) ←──reverse sync──┐
  └─ SkillSync.sync() ──→ .gemini/skills/ (symlink)
                        ──→ .claude/skills/ (symlink)
                                      │
                        CLI agent creates skill here
```

See [todo.md](todo.md) for the full versioned roadmap (v0.4 → v0.5 → v0.6).

---

## v0.3.0 — Memory + Skills

### What Changed

1. **Memory layer** — `MemoryStore` ABC + `SQLiteMemoryStore` persists all conversation turns to `data/memory.db`. WAL mode, FTS5 full-text search, thread-level CRUD.
2. **History compression** — `HistoryCompressor` auto-summarises old turns when `len(turns) > max_turns`. Uses the first available agent to generate a summary; falls back to truncation if all agents fail. Runs asynchronously after each response.
3. **Skill system** — `SkillSync` symlinks skills from `skills/` to `.gemini/skills/` and `.claude/skills/` on startup. Both CLIs auto-discover `SKILL.md` files via the Agent Skills standard.
4. **Async session API** — `ChannelSession.get_history()`, `append_user()`, `append_assistant()` are now async. In-memory cache avoids repeated DB reads.
5. **Agent config** — `CLAUDE.md` → `AGENT.md` (shared via symlinks to `CLAUDE.md` and `GEMINI.md` so all CLIs read the same project context).

### New Files

```
src/oh_my_agent/
  memory/
    store.py                     # MemoryStore ABC + SQLiteMemoryStore (WAL, FTS5)
    compressor.py                # HistoryCompressor (agent summary + truncation fallback)
  skills/
    skill_sync.py                # SkillSync: symlinks skills/ → CLI native dirs

skills/                          # Skill definitions (Agent Skills standard)
  weather/
    SKILL.md
    scripts/weather.sh
```

### Database Schema

```sql
turns(id, platform, channel_id, thread_id, role, content, author, agent, created_at)
turns_fts(content)               -- FTS5 full-text index
summaries(id, platform, channel_id, thread_id, summary, turns_start, turns_end, created_at)
```

### New Config Sections

```yaml
memory:
  backend: sqlite
  path: data/memory.db
  max_turns: 20
  summary_max_chars: 500

skills:
  enabled: true
  path: skills/
```

---

## v0.2.0 — Gateway + Multi-Agent Architecture

### What Changed

Full architectural refactor to support:
1. **Gateway layer** — platform-agnostic channel abstraction, each channel maps to an independent session
2. **Agent registry** — ordered fallback across multiple agents, with attribution in replies
3. **Thread-level conversation history** — same thread retains context across messages
4. **API agents** — direct SDK calls to Anthropic and OpenAI (alongside CLI agents)
5. **YAML config** — `config.yaml` replaces flat `.env` for structured multi-channel setup

### Architecture

```
User (Discord / Slack / ...)
         │ message
         ▼
   GatewayManager
         │ routes to ChannelSession
         ▼
   ChannelSession ──── histories: {thread_id → [turns]}
         │
         ▼
   AgentRegistry ─── [claude, gemini, anthropic_api, ...]
         │ fallback chain
         ▼
   BaseAgent.run(prompt, history)
     ├── BaseCLIAgent (claude, gemini)
     │     subprocess → flatten history into prompt
     └── BaseAPIAgent (anthropic, openai)
           SDK → native messages array
         │
         ▼
   Response → chunk → send to thread
   (with `-# via **agent_name**` attribution)
```

### Project Structure

```
src/oh_my_agent/
  main.py                        # Entry: load config.yaml, build GatewayManager
  config.py                      # YAML loader with ${ENV_VAR} substitution
  gateway/
    base.py                      # IncomingMessage, BaseChannel ABC
    session.py                   # ChannelSession (per-channel state + thread histories)
    manager.py                   # GatewayManager: routes messages, calls agents
    platforms/
      discord.py                 # Discord adapter
      slack.py                   # Slack stub (NotImplementedError)
  agents/
    base.py                      # BaseAgent ABC + AgentResponse
    registry.py                  # AgentRegistry with ordered fallback
    cli/
      base.py                    # BaseCLIAgent (shared subprocess + history → prompt)
      claude.py                  # Claude CLI agent
      gemini.py                  # Gemini CLI agent
    api/
      base.py                    # BaseAPIAgent
      anthropic.py               # Anthropic API agent (multi-turn)
      openai.py                  # OpenAI API agent (multi-turn)
  utils/
    chunker.py                   # Message chunking for platform limits
```

### Configuration

```yaml
# config.yaml
gateway:
  channels:
    - platform: discord
      token: ${DISCORD_BOT_TOKEN}
      channel_id: "${DISCORD_CHANNEL_ID}"
      agents: [claude, gemini]   # fallback order

agents:
  claude:
    type: cli
    cli_path: claude
    max_turns: 25
    model: sonnet
    allowed_tools: [Bash, Read, Edit, Glob, Grep]
  gemini:
    type: cli
    cli_path: gemini
  anthropic_api:
    type: api
    provider: anthropic
    model: claude-sonnet-4-6
    api_key: ${ANTHROPIC_API_KEY}
```

### Implementation Status (v0.2.0)

| Component | Status |
|---|---|
| `config.py` — YAML loader | Done |
| `agents/base.py` — history param | Done |
| `agents/registry.py` — fallback | Done |
| `agents/cli/base.py` — subprocess + history | Done |
| `agents/cli/claude.py` | Done |
| `agents/cli/gemini.py` | Done |
| `agents/api/anthropic.py` | Done |
| `agents/api/openai.py` | Done |
| `gateway/base.py` — IncomingMessage, BaseChannel | Done |
| `gateway/session.py` — ChannelSession | Done |
| `gateway/manager.py` — GatewayManager | Done |
| `gateway/platforms/discord.py` | Done |
| `gateway/platforms/slack.py` | Stub |
| End-to-end testing | Pending |

### Roadmap (Future)

> Moved to [todo.md](todo.md) — versioned roadmap starting from v0.4.0.

---

## v0.1.0 — MVP (Discord + Claude CLI)

Initial working bot: Discord channel → Claude CLI → thread reply.

- Single platform (Discord), single agent (Claude CLI)
- No conversation history (stateless per message)
- Environment variable config

---

## Architecture Decisions

### Why CLI subprocess instead of API SDK?
The `claude` CLI provides a complete agentic loop (tool use, file operations, bash, context management). Wrapping it as a subprocess means all of that is available without reimplementing it.

### Why deprecate API agents? (v0.4+)
CLI agents and API agents have a fundamental incompatibility in abstraction level:

| Dimension | CLI Agent | API Agent |
|-----------|-----------|----------|
| Context Engineering | CLI manages it (AGENT.md, skills, tool use) | Must build from scratch |
| Tool Use | Built-in (Bash, Read, Edit, Grep…) | Must define function schemas |
| Skill System | Native (SKILL.md auto-discovery) | Cannot use |
| Iteration Cost | Zero — CLI upgrades are free | Must track API changes + build infra |

Maintaining both paths doubles the surface area without proportional value. The API agent code is kept for reference but receives no new development.

### Why session-per-channel?
Each channel is an independent workspace. Cross-channel contamination would be confusing and potentially leak context between users/projects. Future: opt-in shared memory via explicit memory module.

### Why flatten history into prompt for CLI agents?
CLI agents are stateless by design. Flattening history into the prompt string is the simplest approach. The alternative (`claude --resume <session_id>`) requires JSON output parsing and session ID storage — added complexity for future versions.

### Why `asyncio.create_subprocess_exec` not `subprocess.run`?
`subprocess.run` blocks the event loop. With multiple concurrent channel sessions and async Discord/Slack clients, a blocking call would stall all other processing.

### Why `--dangerously-skip-permissions`?
Required for headless mode. Without it, the CLI prompts for permission confirmations that cannot be answered in a subprocess. `--allowedTools` still constrains what the agent can do.

### Can CLI agents edit files? (v0.4+)
Yes. Claude CLI has built-in `Edit` and `Write` tools — `Edit` is already in the default `allowed_tools` config. Gemini CLI uses shell commands via `--yolo` with no tool restrictions. Codex CLI has native file editing within its workspace sandbox. The key constraint is not capability but **scope** — sandbox and `allowed_tools` control *where* and *what* they can touch, not *whether* they can edit.

### Why add Codex CLI? (v0.4+)
Codex CLI is the only CLI agent with **built-in, headless-friendly sandbox** (`--sandbox workspace-write`). Adding it provides: (1) a third fallback agent for resilience, (2) a sandbox-first reference implementation, and (3) access to OpenAI models via the same CLI-agent architecture. Its `codex exec` non-interactive mode maps cleanly to `BaseCLIAgent._build_command()`.

### Sandbox strategy (v0.4+)
Short-term: enable CLI-level sandbox where available (Codex `--full-auto`, Gemini `--sandbox`). Claude CLI's sandbox is interactive-only for now — `--allowedTools` serves as the guardrail. Long-term: wrap all CLI agents in Docker containers for full process isolation, independent of CLI-level sandboxing.
