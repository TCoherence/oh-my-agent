# Oh My Agent

Discord bot that routes messages to CLI-based AI agents. Inspired by [OpenClaw](https://github.com/openclaw).

## How It Works

1. User posts a message in a designated Discord channel
2. Bot creates a **thread** from that message
3. Runs `claude` CLI as an async subprocess to generate a response
4. Posts the response in the thread (auto-chunked for Discord's 2000 char limit)
5. Follow-up messages in the same thread continue the conversation there

## Architecture

```
User (Discord)
    │ message in #channel
    ▼
AgentBot (discord.Client)
    │ on_message → create thread
    ▼
ClaudeAgent (BaseAgent)
    │ asyncio.create_subprocess_exec("claude", "-p", ...)
    ▼
Claude CLI (agentic loop)
    │ tool use, reasoning, etc.
    ▼
Response → chunked → thread.send()
```

The agent layer is abstracted behind `BaseAgent` ABC, making it easy to add other CLI agents (codex, gemini, etc.) in the future.

## Prerequisites

- Python 3.11+
- `claude` CLI installed and authenticated (`claude auth status`)
- A Discord bot token with **Message Content Intent** enabled

## Setup

### 1. Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications) and create a new application
2. **Bot** tab → click "Reset Token" → copy the token
3. Under "Privileged Gateway Intents", enable **Message Content Intent**
4. **OAuth2 → URL Generator** → select `bot` scope → select permissions:
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Read Message History
5. Open the generated URL in a browser and invite the bot to your server

### 2. Install & Configure

```bash
git clone <repo-url>
cd oh-my-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# Edit .env:
#   DISCORD_BOT_TOKEN=your-token
#   DISCORD_CHANNEL_ID=your-channel-id
```

### 3. Run

```bash
source .venv/bin/activate
oh-my-agent
```

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_BOT_TOKEN` | Yes | — | Discord bot token |
| `DISCORD_CHANNEL_ID` | Yes | — | Channel ID the bot listens to |
| `CLAUDE_MAX_TURNS` | No | `25` | Max agentic loop iterations |
| `CLAUDE_ALLOWED_TOOLS` | No | `Bash,Read,Edit,Glob,Grep` | Tools the Claude CLI can use |
| `CLAUDE_MODEL` | No | `sonnet` | Claude model to use |

## Project Structure

```
src/oh_my_agent/
  main.py              # Entry point
  config.py            # Environment config loader
  bot.py               # Discord client and thread handling
  agents/
    base.py            # BaseAgent ABC + AgentResponse
    claude.py          # Claude CLI subprocess wrapper
  utils/
    chunker.py         # Message chunking for Discord limit
```
