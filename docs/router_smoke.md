# Router Smoke Test

This smoke test validates the optional OpenAI-compatible intent router. The expected behavior is:

- short chat messages stay in normal reply flow
- long coding requests create a runtime draft instead of starting autonomous execution immediately

## Preconditions

1. Install dependencies and dev extras:

```bash
pip install -e ".[dev]"
```

2. Export the router API key expected by your config:

```bash
export DEEPSEEK_API_KEY=your-key
```

3. Enable the router in `config.yaml`:

```yaml
router:
  enabled: true
  provider: openai_compatible
  base_url: "https://api.deepseek.com/v1"
  api_key_env: DEEPSEEK_API_KEY
  model: deepseek-chat
  timeout_seconds: 8
  max_retries: 1
  confidence_threshold: 0.55
  require_user_confirm: true
```

4. Make sure runtime is enabled, because router-created tasks are handed to the runtime service.

## Smoke Procedure

1. Start the bot with the router-enabled config.
2. Send a short conversational message such as `hi`.
3. Confirm the bot stays in chat mode and does not create a runtime draft.
4. Send a long coding request such as `please fix the failing tests, update docs, run pytest -q, and show me the result`.
5. Confirm the bot responds with the router draft message and does not execute autonomously yet.
6. Approve the draft through the normal runtime approval flow.

## Expected Results

- The short message is classified as `reply_once`.
- The long coding request is classified as `propose_task` with confidence at or above the threshold.
- The created task uses `source="router"` and `force_draft=True`.
- The thread receives the draft-confirm message:

> Router suggested this as a long task and created a draft. Approve to start autonomous execution, or reject/suggest to keep it in chat flow.

## Automated Coverage

The smoke path is backed by unit tests:

- `tests/test_router.py`
- `tests/test_manager.py::test_router_propose_task_creates_runtime_draft_and_skips_reply`
