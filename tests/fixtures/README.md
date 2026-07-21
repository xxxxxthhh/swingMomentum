# Test fixtures

Synthetic data for **logic and pipeline tests**. Not historical market truth.

## OHLCV CSV format

Path: `tests/fixtures/ohlcv/*.csv`

| Column | Type | Notes |
|--------|------|--------|
| `symbol` | string | Ticker, stored uppercased by FakeProvider |
| `date` | `YYYY-MM-DD` | Calendar date (fixtures skip weekends only; not a full exchange calendar) |
| `open`, `high`, `low`, `close` | float | Must satisfy OHLC consistency |
| `volume` | float | Shares/contracts; non-negative |

- Timezone: dates are **date-only** (no intraday). Treat as US equity session dates for tests.
- No network: `FakeProvider` reads these files only.

## Files

| File | Symbol | Intent |
|------|--------|--------|
| `breakout_success.csv` | NVDA | Uptrend → tight range → volume expansion breakout path |
| `false_breakout.csv` | FAKE | Breakout then immediate failure |
| `risk_off_spy.csv` | SPY | Sustained decline for Risk-Off style regime tests (Phase 1+) |

## Usage

```python
from smm.data import FakeProvider

provider = FakeProvider()  # default: this directory's ohlcv/
bars = provider.get_daily_bars("NVDA", start=..., end=...)
```

Phase 0 does **not** assert full scanner rules against these series; Phase 1 MVP-A will.
