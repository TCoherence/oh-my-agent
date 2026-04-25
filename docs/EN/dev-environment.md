# Dev Environment / Second Bot for Testing

This guide shows how to run a sandboxed dev bot inside a worktree without disturbing the production bot.

---

## 1. Why a Dev Bot?

The production bot typically runs in Docker (host bind-mounted at `~/oh-my-agent-docker-mount/`) and holds real user data: `memory.db`, automations, reports, auth credentials. Pushing experimental changes — new hooks, agent logic, skills — directly to prod risks corrupting that state, and rolling back is painful.

A dev bot solves this with three rules of isolation:

1. **Separate Discord bot token.** A single token can hold only one active gateway WebSocket; running prod and dev under the same token would make them kick each other off ([discord.py:1026](../../src/oh_my_agent/gateway/discord.py:1026)).
2. **Separate runtime root.** Dev runtime state lives under `~/.oh-my-agent-dev/`, prod under `~/.oh-my-agent/` (which inside the prod container resolves to host's `~/oh-my-agent-docker-mount/.oh-my-agent/`). The two paths never collide.
3. **Separate workspace.** Dev's agent workspace is `~/.oh-my-agent-dev/agent-workspace/`, so skill sync and `_attachments/` cleanup do not touch prod.

---

## 2. Setup (4 steps)

### 2.1 Create a Second Discord Bot

This bot must be a **completely separate Application** from prod — not just a different invite link.

**a) New Application + token**

In the [Discord Developer Portal](https://discord.com/developers/applications): **New Application** → name it (e.g. `oh-my-agent-dev`) → the Bot user is created automatically. On the **Bot** page:

- Click **Reset Token** → copy the token immediately (it's shown once; reset again if you miss it). This is `DISCORD_DEV_BOT_TOKEN`.
- Scroll to **Privileged Gateway Intents** and enable:
  - ✅ **MESSAGE CONTENT INTENT** — required. Without it the bot connects fine and looks healthy, but message content arrives empty and the bot never responds. [discord.py:1028](../../src/oh_my_agent/gateway/discord.py:1028) sets `intents.message_content = True`, which depends on this toggle.
  - `PRESENCE INTENT` and `SERVER MEMBERS INTENT` are not needed.

**b) Generate invite link with correct scopes + permissions**

**OAuth2 → OAuth2 URL Generator**:

- **Scopes**: `bot` + `applications.commands` (omitting the second one breaks slash-command registration silently).
- **Bot Permissions** (mirrors prod):
  - View Channels, Send Messages, Send Messages in Threads, Create Public Threads, Read Message History, Add Reactions, Attach Files, Embed Links, Use Slash Commands.

Copy the **Generated URL**, open it in a browser, and authorize the bot into a **test server** (or a test channel in your existing server — but not a channel where prod is already active, or both bots will respond to the same message).

**c) Copy the channel id**

Discord client → **Settings → Advanced** → enable **Developer Mode**. Right-click the test channel → **Copy Channel ID**. This is `DISCORD_DEV_CHANNEL_ID`.

### 2.2 Create the Dev Config

From the worktree root:

```bash
cp config.dev.yaml.example config.dev.yaml
```

`config.dev.yaml` is gitignored. The template references two env vars: `DISCORD_DEV_BOT_TOKEN` and `DISCORD_DEV_CHANNEL_ID`. Put both into a `.env` file at the worktree root (also gitignored):

```bash
cat <<EOF >> .env
DISCORD_DEV_BOT_TOKEN=your-dev-bot-token
DISCORD_DEV_CHANNEL_ID=your-dev-channel-id
EOF
```

`.env` must live next to the config file — `load_config()` loads `.env` from the config file's directory first, then falls back to the cwd default ([config.py:33-35](../../src/oh_my_agent/config.py:33)).

### 2.3 Bootstrap the Worktree's Own venv

This is **load-bearing**, not optional. `pip install -e .` records the editable target as the install-time directory, so the main repo's `.venv` resolves `oh_my_agent` to the main repo's `src/` — **not your worktree's `src/`**. Running dev bot from the main venv would silently load main-branch code and skip every change you made in the worktree.

From the worktree root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Verify the editable target now points at the worktree:

```bash
cat .venv/lib/python*/site-packages/__editable__.oh_my_agent-*.pth
# expect: <worktree path>/src   (NOT /Users/.../oh-my-agent/src)
```

CLI agents (`claude`, `gemini`, `codex`) live on `PATH` and are shared with prod — no need to reinstall those.

### 2.4 Validate, Then Run

Application-level config validation (does not connect to Discord):

```bash
./.venv/bin/oh-my-agent --config config.dev.yaml --validate-config
```

If that passes, launch:

```bash
./.venv/bin/oh-my-agent --config config.dev.yaml
```

Use the explicit `./.venv/bin/...` path so you hit **this worktree's** venv (which loads this worktree's `src/`), not a globally-installed `oh-my-agent` or another worktree's venv. The startup log should show all runtime paths under `~/.oh-my-agent-dev/...`.

---

## 3. Isolation Invariants

| Concern | Dev | Prod |
|---|---|---|
| Runtime root | `~/.oh-my-agent-dev/` | `~/.oh-my-agent/` (in container) = host `~/oh-my-agent-docker-mount/.oh-my-agent/` |
| Memory DB | `~/.oh-my-agent-dev/runtime/memory.db` | container's `~/.oh-my-agent/runtime/memory.db` |
| Workspace | `~/.oh-my-agent-dev/agent-workspace/` | container's `~/.oh-my-agent/agent-workspace/` |
| Discord token | dev bot token | prod bot token |
| Discord channel | a test channel | prod channel |
| Automations | **off** by default in template | typically on |

Dev's `memory.db` starts empty. To copy memories from prod into dev:

```bash
mkdir -p ~/.oh-my-agent-dev
cp -r ~/oh-my-agent-docker-mount/.oh-my-agent/memory ~/.oh-my-agent-dev/memory
```

Do this only when you want dev to reflect prod's accumulated memory state — most testing is cleaner without it.

---

## 4. Caveats

- **One process per dev bot token.** Discord's gateway only allows one active WebSocket per token. If you want to run dev bots in two worktrees simultaneously, create a third Discord bot with its own token.
- **Dev's `automations.enabled` defaults to `false`** in the template. Flip it to `true` only when you specifically want to test the scheduler — otherwise automations will fire and spam your test channel.
- **The validator does not check env-var resolution.** `--validate-config` parses YAML and checks schema, but it will not flag a missing `DISCORD_DEV_BOT_TOKEN`; the bot will fail at startup instead. Make sure `.env` is in place before running.
- **Don't repoint `workspace:` at prod's path.** Section 1's third isolation rule relies on the dev template's default `workspace: ~/.oh-my-agent-dev/agent-workspace`. Changing it to `~/.oh-my-agent/agent-workspace` (or the docker bind path) makes dev's skill sync and `_attachments/` cleanup race prod for the same files — defeats the whole isolation.
- **Runtime tasks auto-commit to the active branch.** With `runtime.merge_gate.auto_commit: true` (template default, matches prod), autonomous tasks commit their changes to whatever branch you're checked out on. Don't kick off autonomous runtime tasks while sitting on a branch you're about to open a PR from — the runtime commits will pollute your PR history.
- **Confirm prod is unaffected.** After starting dev, verify the prod docker container is still healthy: `docker compose ps`. Prod should show no restarts, no disconnects.

---

## 5. Verifying the Setup

1. Send `hi` in your dev Discord channel — the dev bot should reply normally. (If it doesn't reply at all but the process is online, you forgot to enable MESSAGE CONTENT INTENT — see 2.1a.)
2. Run `ls ~/.oh-my-agent-dev/` — you should see `runtime/`, `agent-workspace/`, `memory/`, `reports/` directories created on first use. (`automations/` won't appear unless you flip it on.)
3. Send a message in the prod channel — the prod bot should still respond as before.

---

## 6. Cleanup

To delete a dev environment entirely:

```bash
rm -rf ~/.oh-my-agent-dev/
rm config.dev.yaml .env
```

The dev bot token and channel can be left in the Discord Developer Portal for next time, or revoked.
