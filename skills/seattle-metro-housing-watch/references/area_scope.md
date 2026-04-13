# Area Scope

## Default areas

The default metro watchlist is:

- `seattle`
- `bellevue`
- `redmond`
- `kirkland`
- `issaquah`
- `bothell`
- `lynnwood`

These are the default areas for `weekly_pulse` and the default `market_snapshot`.

## Preferred granularity

- `seattle`
  - prefer city-level first
  - neighborhood detail only when public pages are clear and materially useful
- `bellevue`
  - city-level first
  - use downtown / west / east sub-areas only when they materially change the read
- `redmond`
  - city-level first
- `kirkland`
  - city-level first
- `issaquah`
  - city-level first
- `bothell`
  - city-level first
  - only use split north/south sub-areas when a public page clearly supports it
- `lynnwood`
  - city-level first

## Degradation rules

If neighborhood-level coverage is weak:

- fall back to city-level
- if city-level is still weak, use metro-level context plus a short note
- write the gap into `coverage_gaps`

Do not invent false precision just to make every area look equally detailed.
