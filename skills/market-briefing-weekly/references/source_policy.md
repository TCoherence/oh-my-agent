# Source Policy

## Goal

Produce reports that are current, explicit about provenance, and conservative about uncertain claims.

## Source mix

Use a balanced source mix:

- `primary / official`
  - ministries, central banks, White House / federal agencies, official speeches, official transcripts
- `company / filing`
  - earnings releases, shareholder letters, SEC filings, investor presentations
- `media / analysis`
  - reputable reporting and informed analysis
- `community / social`
  - community threads, developer signals, X.com posts, open-source maintainer commentary when relevant

Default stance:

- slightly favor primary sources for key conclusions
- use media/analysis to fill context and interpretation
- cross-check major claims whenever possible
- for AI people/community signals, treat X.com and other community posts as early signal, not the sole basis for high-risk conclusions

## What to record

Every report should explicitly include:

- `source_mix_note`
- `verification_note`
- `sources[]` with `source_type`

## Claim discipline

- Separate confirmed facts from interpretation.
- If a claim only appears in analysis or reporting, say so.
- If primary sources conflict with commentary, bias toward the primary source and note the conflict.
- Do not overstate trends from one-day noise.

## Historical continuity

- Weekly synthesis should use persisted daily reports as the baseline memory substrate.
- Bootstrap dossiers are background context, not a substitute for recent reporting.
- Discord history is not the primary trend store for this workflow.

## Finance source family requirements

Finance daily should cover these source families:

- China macro / policy
  - PBOC, Ministry of Finance, NBS, NDRC, MIIT, CSRC-related signals, State Council / top-level policy
- US macro / policy
  - Fed, Treasury, BLS, BEA, White House, SEC, other federal agencies where relevant
- US market volatility / risk appetite
  - official macro/policy plus high-signal market reporting and volatility context
- China / Hong Kong market pulse
  - official signals plus high-signal market reporting
- China property policy / financing signals
  - housing policy notices, financing policy, credit, mortgage, property-support measures
- tracked holdings / market-index lens
  - earnings releases, shareholder letters, 8-K / 10-K / 10-Q, IR pages, conference transcripts, CEO / CFO / IR public remarks

Default finance stance:

- China coverage includes macro + policy + market pulse + property finance signals
- tracked holdings use a rolling 7-day window
- `VOO`, `SPY`, and `S&P 500` should be interpreted as market / allocation lenses rather than pseudo-company events

## AI source family requirements

AI daily should cover three layers of sources:

- frontier-lab radar
  - official lab pages, docs, model cards, release notes, high-quality reporting
- topic sources
  - company blogs, release notes, model cards, docs, talks, papers, code repos, infrastructure announcements
- people/community sources
  - X.com posts, open-source maintainer commentary, developer writeups, community threads

Default AI stance:

- tracked people / groups should be consulted first
- frontier-lab radar is a mandatory section, not an optional aside
- run a bounded discovery sweep for new people strongly tied to the day's most important AI themes
- new people enter the candidate queue first
- X.com is a signal source and normally needs cross-checking before it becomes a five-layer core conclusion
- if a frontier signal remains unverified, keep it in watchlist form or `unverified frontier signals`

## Section density and degraded sections

- Do not let sections collapse into one sentence of generic filler.
- If a section has no high-confidence incremental signal, state `no high-confidence incremental signal` explicitly and explain what is still worth watching.
- Use `coverage_gaps` and `confidence_flags` to record thin-evidence sections instead of pretending the section is complete.
