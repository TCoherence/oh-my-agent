# Oh My Agent

Multi-platform bot that routes messages to AI agents — CLI-based (Claude, Gemini) or API-based (Anthropic, OpenAI). Inspired by [OpenClaw](https://openclaw.dev).

Each platform channel maps to an independent agent session. Messages in the same thread retain conversation history. If the primary agent fails, the next one in the fallback chain takes over automatically.

## Architecture

```
User (Discord / Slack / ...)
         │ message
         ▼
   GatewayManager
         │ routes to ChannelSession (per channel, isolated)
         ▼
   AgentRegistry ── [claude, gemini, anthropic_api, ...]
         │ tries in order, auto-fallback on error
         ▼
   BaseAgent.run(prompt, history)
     ├── BaseCLIAgent  →  subprocess  (claude, gemini CLIs)
     └── BaseAPIAgent  →  SDK call    (Anthropic, OpenAI)
         │
         ▼
   Response → chunked → thread.send()  (-# via **agent-name**)
```

## Prerequisites

- Python 3.11+
- At least one of:
  - `claude` CLI installed and authenticated (`claude auth status`)
  - `gemini` CLI installed
  - Anthropic or OpenAI API key
- A Discord bot token with **Message Content Intent** enabled

## Setup

### 1. Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications) → create application
2. **Bot** tab → copy token → enable **Message Content Intent**
3. **OAuth2 → URL Generator** → scope `bot` → permissions: Send Messages, Create Public Threads, Send Messages in Threads, Read Message History
4. Open the generated URL to invite the bot to your server

### 2. Install

```bash
git clone <repo-url>
cd oh-my-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Optional: API-based agents
pip install -e ".[anthropic]"   # Anthropic SDK
pip install -e ".[openai]"      # OpenAI SDK
pip install -e ".[all]"         # Both
```

### 3. Configure

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml` — set your tokens and choose which agents each channel uses:

```yaml
gateway:
  channels:
    - platform: discord
      token: ${DISCORD_BOT_TOKEN}       # or paste directly
      channel_id: "${DISCORD_CHANNEL_ID}"
      agents: [claude, gemini]          # fallback order

agents:
  claude:
    type: cli
    model: sonnet
  gemini:
    type: cli
  anthropic_api:
    type: api
    provider: anthropic
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-sonnet-4-6
```

Secrets can live in a `.env` file — `${VAR}` placeholders are substituted automatically.

### 4. Run

```bash
source .venv/bin/activate
oh-my-agent
```

## Usage

- **Post a message** in the configured channel → bot creates a thread and replies there
- **Reply in the thread** → bot responds in the same thread, with conversation history
- Each reply is prefixed with `-# via **agent-name**` so you always know which agent responded
- If an agent fails or hits quota, the next one in the `agents:` list takes over silently

## Development

```bash
pip install -e ".[dev]"
pytest                        # run all tests
pytest -k "test_fallback"     # run a specific test
```

See [`docs/todo.md`](docs/todo.md) for the roadmap and [`docs/development.md`](docs/development.md) for architecture decisions.
