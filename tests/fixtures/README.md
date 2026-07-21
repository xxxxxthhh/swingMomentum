# Test fixtures

Synthetic data for **logic and pipeline tests**. Not historical market truth.

## The generator is the truth source

Committed OHLCV CSVs were removed in M1 (ADR 2026-07-22 §4.1). The hard filters
need SMA200, Return_126 and a 52-week high, so a usable path is 252+ bars —
past what is maintainable, or verifiable, as hand-written CSV.

Tests build paths in memory:

```python
from smm.data.generator import breakout_success, false_breakout, risk_off_spy

path = breakout_success()
path.bars              # tuple[Bar, ...], 280 bars
path.breakout_index    # index of the trigger bar
path.digest()          # stable content hash
```

| Path | Symbol | Intent |
|------|--------|--------|
| `breakout_success` | NVDA | Uptrend → tight range → volume breakout → follow-through |
| `false_breakout` | FAKE | **Identical** setup and trigger, then fails back through the level |
| `risk_off_spy` | SPY | Sustained decline ending below SMA200 with SMA50 < SMA200 |

`breakout_success` and `false_breakout` are deliberately indistinguishable **on
the trigger day** — same hard-filter result, same trigger. Only later bars
separate them. A fixture that could be told apart on the trigger day would be
quietly teaching the scanner to look ahead.

## Determinism

Noise comes from SHA-256, not `random`, whose stream is a CPython
implementation detail. The same spec produces byte-identical bars on any
machine, which is what makes the golden-digest test in
`tests/unit/test_generator.py` meaningful. Changing the generator changes those
digests — that is allowed, but it is a deliberate, reviewed act.

## Conventions

- `date` is **date-only**, treated as a US equity session date.
- Weekends are skipped; exchange holidays are **not** modelled.
- `adj_factor` is `1.0` and `adj_close == close`: synthetic paths carry no
  corporate action. That is a stated known value, not a defaulted missing one —
  see `Bar`'s docstring and ADR §3.3.

## CSV-backed provider

`FakeProvider` still reads a directory of CSVs; it is a legitimate
`DataProvider` implementation and useful for ad-hoc inspection. Tests that need
it write generated paths to a temp directory via the `ohlcv_dir` fixture in
`tests/conftest.py`. Required columns:

```text
symbol,date,open,high,low,close,volume,adj_close,adj_factor
```

`adj_close`/`adj_factor` are required. Substituting `close` for a missing
`adj_close` is precisely what ADR §3.3 forbids.
