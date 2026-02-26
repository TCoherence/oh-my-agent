# Todo / Roadmap

Items are roughly prioritized top-to-bottom within each section.

## Next Up

- [ ] **Conversation memory within threads** — currently each message is independent for CLI agents. For CLI: flatten thread history into prompt (structure already in place in `_build_prompt_with_history`). For API agents: already handled natively. Needs wiring in `GatewayManager.handle_message()`.
- [ ] **End-to-end test with real Discord** — current tests are all unit tests with mocks. Add an integration test that spins up a real Discord bot against a test server/channel.
- [ ] **Slack adapter** — `gateway/platforms/slack.py` is currently a stub. Implement using `slack_sdk` (async client). Threads in Slack use `thread_ts`.
- [ ] **Gemini CLI flags** — verify correct flags for `gemini` CLI non-interactive mode (`--yolo` may not be the real flag). Update `agents/cli/gemini.py` once confirmed.

## Features / Ideas

- [ ] **Streaming responses** — edit a Discord message in-place as tokens arrive. Requires `--output-format stream-json` for CLI agents, streaming SDK calls for API agents. Rate-limit edits to avoid Discord throttling (e.g. edit every 0.5s).
- [ ] **Agent selection via @mention** — user types `@claude fix this` or `@gemini explain` to route to a specific agent rather than using the registry fallback order.
- [ ] **Codex CLI agent** — add `agents/cli/codex.py` for OpenAI Codex CLI once it's available.
- [ ] **Telegram adapter** — `gateway/platforms/telegram.py`. Telegram supports message threads in groups via `reply_to_message_id`.
- [ ] **Feishu/Lark adapter** — `gateway/platforms/feishu.py`. Feishu has a mature bot SDK with thread support.
- [ ] **Slash commands** — `/ask`, `/reset` (clear thread history), `/agent claude` (switch agent for this session). Requires moving from `discord.Client` to `discord.app_commands`.
- [ ] **Rate limiting / request queue** — prevent hammering the CLI when multiple messages arrive simultaneously. Per-session queue with configurable concurrency.
- [ ] **File attachment support** — download Discord file attachments, pass file paths to the agent as part of the prompt context.
- [ ] **Cross-session memory** — shared memory module that allows agent sessions to reference persistent notes/context across channels. Keep sessions independent by default; opt-in via config.
- [ ] **Markdown-aware chunking** — `utils/chunker.py` currently may split inside code fences. Track open/close triple-backtick state when finding split points.

## Maintenance / Quality

- [ ] **Update README.md** — still describes v0.1.0 architecture (references deleted `bot.py`). Rewrite to reflect v0.2.0 gateway + YAML config.
- [ ] **Linting / formatting** — add `ruff` to dev deps, configure in `pyproject.toml`.
- [ ] **Type checking** — add `mypy` or `pyright` to dev deps, enable strict mode incrementally.
- [ ] **GitHub Actions CI** — run `pytest` on push/PR. Matrix over Python 3.11 and 3.12.
