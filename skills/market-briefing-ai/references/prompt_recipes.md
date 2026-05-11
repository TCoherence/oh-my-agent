# Prompt Recipes

Use these when the user wants a concrete prompt, an automation prompt, or a scoped variant instead of one generic `market-briefing-ai` invocation.

All prompts below are Chinese-first and assume persisted report outputs under `~/.oh-my-agent/reports/market-briefing/`.

## AI daily

### Broad AI daily with frontier radar

```text
Use the market-briefing-ai skill in daily_digest mode for ai for the current local date. Read prior stored reports and the current AI people pool, then begin with a Frontier Labs / Frontier Model Radar section covering OpenAI, Anthropic, Google DeepMind, Meta, xAI, Mistral, Qwen, and DeepSeek. After that, structure the report around tracked people/community signals plus the five layers: energy, chips, infra, model, application. Consult tracked people/groups first, do a bounded discovery sweep for new relevant people, and keep rumors in unverified frontier signals unless they are cross-checked by stronger sources. Persist Markdown + JSON under ~/.oh-my-agent/reports/market-briefing/ and return a structured Chinese chat summary per the SKILL.md final-answer format.
```

### Frontier-focused AI daily

```text
Use the market-briefing-ai skill in daily_digest mode for ai for the current local date, with special emphasis on frontier-lab and frontier-model signals such as GPT-6-class or Anthropic Mythos-class developments. Start with a Frontier Labs / Frontier Model Radar section, then map the implications into people/community, energy, chips, infra, model, and application. Official sources and high-quality media take priority; unverified social signals stay in watchlist or unverified frontier signals rather than the main thesis. Persist Markdown + JSON and return a structured Chinese chat summary per the SKILL.md final-answer format.
```
