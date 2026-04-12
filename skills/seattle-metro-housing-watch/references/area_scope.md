# Area Scope

## V1 required areas

The default v1 metro watchlist is:

- `seattle`
- `bellevue`
- `redmond`
- `kirkland`
- `issaquah`

These are the required areas for `weekly_pulse` and the default `market_snapshot`.

## Optional expansion areas

Document but do not require in v1:

- `bothell`
- `lynnwood`

Only expand into them when:

- the user explicitly asks, or
- the main metro story clearly runs through those areas and coverage is easy to support

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

## Degradation rules

If neighborhood-level coverage is weak:

- fall back to city-level
- if city-level is still weak, use metro-level context plus a short note
- write the gap into `coverage_gaps`

Do not invent false precision just to make every area look equally detailed.
