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

Default stance:

- slightly favor primary sources for key conclusions
- use media/analysis to fill context and interpretation
- cross-check major claims whenever possible

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

Finance daily should cover three source families:

- China macro / policy
  - PBOC, Ministry of Finance, NBS, NDRC, MIIT, CSRC-related signals, State Council / top-level policy
- US macro / policy
  - Fed, Treasury, BLS, BEA, White House, SEC, other federal agencies where relevant
- tracked holdings / market-index lens
  - earnings releases, shareholder letters, 8-K / 10-K / 10-Q, IR pages, conference transcripts, CEO / CFO / IR public remarks

Default finance stance:

- China coverage defaults to macro + policy, not broad China listed-company scanning
- tracked holdings use a rolling 7-day window
- `VOO`, `SPY`, and `S&P 500` should be interpreted as market / allocation lenses rather than pseudo-company events

## Section density and degraded sections

- Do not let sections collapse into one sentence of generic filler.
- If a section has no high-confidence incremental signal, state that explicitly and explain what is still worth watching.
- Use `coverage_gaps` and `confidence_flags` to record thin-evidence sections instead of pretending the section is complete.
