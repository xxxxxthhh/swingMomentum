# Universe snapshots

Dated index-membership files (ADR 2026-07-22 §2). Membership is a
point-in-time fact, so it is committed here rather than fetched at run time — a
runtime scrape would make the same `as_of` replay differently on two days.

## ⚠️ Survivorship disclaimer (constitution §12.3)

> These snapshots list **current** constituents. They are for engineering tests
> and the daily forward pipeline only. They **must not** be used to produce
> formal historical backtest conclusions — that requires true historical
> membership data. Backtesting today's constituents over the past silently
> excludes every company that was removed, which flatters results.

## File format

```text
configs/universe/YYYY-MM-DD_<label>.csv
```

| Column | Notes |
|--------|-------|
| `symbol` | Ticker, uppercased |
| `name` | Human-readable, for auditing the file itself |
| `index_membership` | `sp500`, `ndx100`, or `both` |
| `snapshot_date` | `YYYY-MM-DD`, must match the filename prefix on every row |

The filename date and the `snapshot_date` column are cross-checked on load, so
a file cannot be renamed into a different point in time.

## Selection rules

| | Rule |
|---|---|
| **Allowed** | The largest `snapshot_date <= as_of` |
| **Forbidden** | Any snapshot dated after `as_of` — that is look-ahead |
| **Forbidden** | Inventing an empty or full universe when none qualifies |
| **Forbidden** | Serving a snapshot older than `universe.max_snapshot_age_days` |

The age limit fails closed on purpose. Constituents drift continuously, and a
silently stale universe puts the cross-sectional ranking on the wrong sample
without ever announcing itself. If 90 days proves too noisy in practice, raise
the threshold in config — do not downgrade it to a warning.

## Current files

| File | Rows | Status |
|------|------|--------|
| `2026-07-22_seed.csv` | 31 | **Seed only** — exercises the loader end to end |

**The production snapshot is a pending follow-up.** The seed is a small set of
unambiguous large caps, not S&P 500 ∪ Nasdaq-100. Cross-sectional momentum and
relative-strength ranking are meaningless on 31 names, so M2/M3 need the real
~600-symbol list before any ranking output should be read as signal. Assembling
and verifying it is a data task, deliberately kept out of the M1 code change.
