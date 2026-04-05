# Future TODO

Items deferred to the next iteration of deals-scanner.

## Automation integration (6 jobs)

When ready to integrate with the file-driven scheduler (`~/.oh-my-agent/automations/*.yaml`):

### Daily automations (5 jobs)

| Job name | Source | Suggested cron | Notes |
|----------|--------|----------------|-------|
| `daily-credit-card-deals` | credit-cards | `0 8 * * *` | 8:00 AM |
| `daily-uscardforum` | uscardforum | `15 8 * * *` | 8:15 AM |
| `daily-rakuten-deals` | rakuten | `30 8 * * *` | 8:30 AM |
| `daily-slickdeals` | slickdeals | `0 9 * * *` | 9:00 AM |
| `daily-dealmoon-deals` | dealmoon | `30 9 * * *` | 9:30 AM |

### Weekly automation (1 job)

| Job name | Source | Suggested cron | Notes |
|----------|--------|----------------|-------|
| `weekly-deals-digest` | all-sources | `0 10 * * 0` | Sunday 10:00 AM |

Each automation YAML should use `agent: codex`, `delivery: channel`, and reference the appropriate prompt recipe from `references/prompt_recipes.md`.

## Award flight monitoring (new source)

- **Scope**: China-US award ticket availability monitoring
- **Approach**: Likely needs a dedicated script rather than web search (API/scraping)
- **Potential sources**: ExpertFlyer, AwardHacker, airline award search tools, uscreditcardguide award alerts
- **Design**: separate `source: award-flights` with its own sections and scripts
- **Dependencies**: may need dedicated API keys or browser automation

## Personalized filtering

- Filter deals by category (tech, beauty, food, etc.)
- Filter by price range or discount threshold
- Filter by quality_score minimum
- User preference profiles (e.g., "I care about Chase cards and Rakuten tech deals")

## Enhanced features

- Deal deduplication across sources (same deal reported by Slickdeals and Dealmoon)
- Price history tracking for recurring deals
- Alert/notification for deals above quality_score threshold
- Integration with browser extensions for one-click activation
