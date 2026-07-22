"""Deterministic synthetic OHLCV paths (ADR 2026-07-22 §4).

The hard filters need SMA200, Return_126 and a 52-week high, so a usable
fixture is at least 252 bars — well past what is maintainable as hand-written
CSV. The generator is the truth source; tests build paths in memory rather than
reading committed files.

Determinism is load-bearing: the same spec must produce byte-identical bars on
any machine, otherwise the golden-hash regression test means nothing. Noise is
therefore derived from SHA-256 rather than :mod:`random`, whose stream is a
CPython implementation detail.

Paths are built in explicit phases so the breakout bar can be priced against
the history that actually precedes it, instead of hoping drift produces one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta

from smm.domain.models import Bar

# Synthetic paths carry no corporate action. adj_factor is a known 1.0, not a
# missing value — see Bar's docstring.
_NO_CORPORATE_ACTION = 1.0


@dataclass(frozen=True, slots=True)
class SyntheticPath:
    """A generated path plus the landmarks tests need to assert against."""

    symbol: str
    bars: tuple[Bar, ...]
    breakout_index: int | None = None

    def digest(self) -> str:
        """Stable content hash, for golden-value regression tests."""
        payload = ";".join(
            f"{b.date.isoformat()},{b.open:.6f},{b.high:.6f},{b.low:.6f},"
            f"{b.close:.6f},{b.volume:.1f},{b.adj_close:.6f},{b.adj_factor:.6f}"
            for b in self.bars
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _noise(seed: str, index: int) -> float:
    """Deterministic pseudo-noise in ``[-1, 1)``, stable across interpreters."""
    digest = hashlib.sha256(f"{seed}:{index}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**63 - 1.0


def _weekdays(start: date, count: int) -> list[date]:
    """``count`` consecutive weekdays from ``start`` (holidays are out of scope)."""
    out: list[date] = []
    cursor = start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _make_bar(
    symbol: str,
    day: date,
    *,
    prev_close: float,
    close: float,
    volume: float,
    wiggle: float,
) -> Bar:
    """Build one OHLC bar around ``close`` that satisfies the Bar invariants."""
    open_ = prev_close * (1.0 + wiggle * 0.3)
    hi_body, lo_body = max(open_, close), min(open_, close)
    high = hi_body * (1.0 + abs(wiggle) * 0.6 + 0.001)
    low = lo_body * (1.0 - abs(wiggle) * 0.6 - 0.001)
    return Bar(
        symbol=symbol,
        date=day,
        open=round(open_, 4),
        high=round(high, 4),
        low=round(low, 4),
        close=round(close, 4),
        volume=round(volume, 1),
        adj_close=round(close, 4),
        adj_factor=_NO_CORPORATE_ACTION,
    )


def _grow(
    symbol: str,
    days: list[date],
    *,
    seed: str,
    start_price: float,
    drift: float,
    noise_amp: float,
    base_volume: float,
    volume_mult: float = 1.0,
    offset: int = 0,
    bars: list[Bar] | None = None,
) -> list[Bar]:
    """Append a drifting segment to ``bars`` (or start a new path)."""
    bars = list(bars or [])
    prev_close = bars[-1].close if bars else start_price
    for i, day in enumerate(days):
        wiggle = _noise(seed, offset + i) * noise_amp
        close = prev_close * (1.0 + drift + wiggle)
        vol = base_volume * volume_mult * (1.0 + _noise(seed + "v", offset + i) * 0.15)
        bars.append(
            _make_bar(symbol, day, prev_close=prev_close, close=close, volume=vol, wiggle=wiggle)
        )
        prev_close = close
    return bars


def _breakout_level(bars: list[Bar], window: int) -> float:
    """Highest high of the ``window`` bars *before* the current one.

    Excluding the current bar is what makes the trigger computable from
    ``bars[:t + 1]`` alone — the no-look-ahead property tests rely on.
    """
    return max(b.high for b in bars[-window:])


def breakout_success(
    *,
    symbol: str = "NVDA",
    start: date = date(2023, 1, 2),
    total_bars: int = 280,
    breakout_window: int = 20,
    seed: str = "breakout_success",
) -> SyntheticPath:
    """Uptrend → tight consolidation → volume breakout → follow-through.

    Passes the hard filters and triggers: close clears the prior 20-bar high on
    volume well above its own 20-day average.
    """
    days = _weekdays(start, total_bars)
    trend_n = total_bars - 40
    bars = _grow(
        symbol,
        days[:trend_n],
        seed=seed,
        start_price=50.0,
        drift=0.0035,
        noise_amp=0.006,
        base_volume=1_000_000,
    )
    # Consolidation: flat and quiet, so the breakout bar's relative volume is
    # unambiguous rather than an artefact of a noisy baseline.
    bars = _grow(
        symbol,
        days[trend_n : trend_n + 20],
        seed=seed,
        start_price=0,
        drift=0.0,
        noise_amp=0.003,
        base_volume=1_000_000,
        volume_mult=0.7,
        offset=trend_n,
        bars=bars,
    )
    level = _breakout_level(bars, breakout_window)
    avg_volume = sum(b.volume for b in bars[-breakout_window:]) / breakout_window
    breakout_index = len(bars)
    prev_close = bars[-1].close
    bars.append(
        _make_bar(
            symbol,
            days[breakout_index],
            prev_close=prev_close,
            # Clear the level without violating the frozen 2.5 ATR extension
            # guard. A "success" fixture that only passed the breakout/volume
            # subset would teach the scanner a contract the config rejects.
            close=level * 1.005,
            volume=avg_volume * 1.9,
            wiggle=0.004,
        )
    )
    bars = _grow(
        symbol,
        days[breakout_index + 1 :],
        seed=seed,
        start_price=0,
        drift=0.004,
        noise_amp=0.005,
        base_volume=avg_volume,
        volume_mult=1.2,
        offset=breakout_index + 1,
        bars=bars,
    )
    return SyntheticPath(symbol=symbol, bars=tuple(bars), breakout_index=breakout_index)


def false_breakout(
    *,
    symbol: str = "FAKE",
    start: date = date(2023, 1, 2),
    total_bars: int = 280,
    breakout_window: int = 20,
    seed: str = "false_breakout",
) -> SyntheticPath:
    """Same setup and trigger as :func:`breakout_success`, then it fails.

    The breakout bar is identical in kind; only what follows differs, so a
    scanner cannot separate the two using information available on the trigger
    day. That is the point — it keeps the fixture honest about look-ahead.
    """
    days = _weekdays(start, total_bars)
    trend_n = total_bars - 40
    bars = _grow(
        symbol,
        days[:trend_n],
        seed=seed,
        start_price=30.0,
        drift=0.0035,
        noise_amp=0.006,
        base_volume=800_000,
    )
    bars = _grow(
        symbol,
        days[trend_n : trend_n + 20],
        seed=seed,
        start_price=0,
        drift=0.0,
        noise_amp=0.003,
        base_volume=800_000,
        volume_mult=0.7,
        offset=trend_n,
        bars=bars,
    )
    level = _breakout_level(bars, breakout_window)
    avg_volume = sum(b.volume for b in bars[-breakout_window:]) / breakout_window
    breakout_index = len(bars)
    bars.append(
        _make_bar(
            symbol,
            days[breakout_index],
            prev_close=bars[-1].close,
            # Match the successful path's legal extension: only the bars after
            # this session may distinguish a false breakout from a successful
            # one (the no-lookahead contract).
            close=level * 1.005,
            volume=avg_volume * 1.7,
            wiggle=0.004,
        )
    )
    # Fails back through the breakout level within a few sessions.
    bars = _grow(
        symbol,
        days[breakout_index + 1 :],
        seed=seed,
        start_price=0,
        drift=-0.006,
        noise_amp=0.004,
        base_volume=avg_volume,
        volume_mult=1.1,
        offset=breakout_index + 1,
        bars=bars,
    )
    return SyntheticPath(symbol=symbol, bars=tuple(bars), breakout_index=breakout_index)


def risk_off_spy(
    *,
    symbol: str = "SPY",
    start: date = date(2023, 1, 2),
    total_bars: int = 300,
    seed: str = "risk_off_spy",
) -> SyntheticPath:
    """Benchmark that ends in Risk-Off: close below SMA200 and SMA50 below SMA200."""
    days = _weekdays(start, total_bars)
    rise_n = total_bars // 2
    bars = _grow(
        symbol,
        days[:rise_n],
        seed=seed,
        start_price=380.0,
        drift=0.0016,
        noise_amp=0.004,
        base_volume=50_000_000,
    )
    bars = _grow(
        symbol,
        days[rise_n:],
        seed=seed,
        start_price=0,
        drift=-0.0028,
        noise_amp=0.005,
        base_volume=50_000_000,
        volume_mult=1.3,
        offset=rise_n,
        bars=bars,
    )
    return SyntheticPath(symbol=symbol, bars=tuple(bars), breakout_index=None)


def trending(
    symbol: str,
    *,
    start: date = date(2023, 1, 2),
    total_bars: int = 300,
    drift: float = 0.002,
    start_price: float = 100.0,
    base_volume: float = 5_000_000,
    seed: str | None = None,
) -> SyntheticPath:
    """A plain drifting series — the building block for a synthetic universe."""
    return SyntheticPath(
        symbol=symbol,
        bars=tuple(
            _grow(
                symbol,
                _weekdays(start, total_bars),
                seed=seed or f"trend:{symbol}",
                start_price=start_price,
                drift=drift,
                noise_amp=0.006,
                base_volume=base_volume,
            )
        ),
    )


#: Sector key -> (benchmark ETF, member symbols). Two GICS sectors is enough to
#: prove sector RS actually discriminates; more would only add runtime.
SYNTHETIC_SECTORS: dict[str, tuple[str, tuple[str, ...]]] = {
    "information_technology": ("XLK", ("SYNT1", "SYNT2", "SYNT3", "SYNT4")),
    "health_care": ("XLV", ("SYNH1", "SYNH2", "SYNH3", "SYNH4")),
}

#: Per-symbol drift, chosen so each sector has clear leaders and laggards
#: relative to its own ETF and to the benchmark. Without that spread the
#: cross-sectional ranking is uniform and proves nothing.
_UNIVERSE_DRIFT: dict[str, float] = {
    "SPY": 0.0010,
    "XLK": 0.0016,
    "SYNT1": 0.0034,
    "SYNT2": 0.0026,
    "SYNT3": 0.0012,
    "SYNT4": 0.0002,
    "XLV": 0.0008,
    "SYNH1": 0.0028,
    "SYNH2": 0.0018,
    "SYNH3": 0.0006,
    "SYNH4": -0.0004,
}


def synthetic_universe(
    *,
    start: date = date(2023, 1, 2),
    total_bars: int = 300,
) -> dict[str, SyntheticPath]:
    """A whole offline cross-section: benchmark, sector ETFs, and members.

    The three original paths cover single-symbol logic but cannot exercise M2:
    with no sector ETFs, ``RS_Sector`` is missing for every symbol, so
    ``RelativeStrengthScore`` is missing for every symbol and the candidate set
    is empty by construction. Ranking two stocks is degenerate besides.

    This keeps the offline path able to produce a real, non-empty result — the
    same property ``smm ingest --source synthetic`` already has.
    """
    paths: dict[str, SyntheticPath] = {}
    for symbol, drift in _UNIVERSE_DRIFT.items():
        # ETFs carry far more volume than their members, as in the real market.
        is_fund = symbol in {"SPY"} | {etf for etf, _ in SYNTHETIC_SECTORS.values()}
        paths[symbol] = trending(
            symbol,
            start=start,
            total_bars=total_bars,
            drift=drift,
            start_price=400.0 if symbol == "SPY" else 100.0,
            base_volume=40_000_000 if is_fund else 3_000_000,
        )
    return paths


def universe_rows(as_of: date) -> list[dict[str, str]]:
    """Universe-snapshot rows for :func:`synthetic_universe`'s members.

    ETFs are deliberately absent: they are benchmarks, and constitution §10
    limits the universe to common stock.
    """
    rows: list[dict[str, str]] = []
    for sector, (_etf, members) in SYNTHETIC_SECTORS.items():
        rows.extend(
            {
                "symbol": symbol,
                "name": f"Synthetic {symbol}",
                "index_membership": "sp500",
                "sector": sector,
                "snapshot_date": as_of.isoformat(),
            }
            for symbol in members
        )
    return rows


#: Named paths the test suite builds instead of reading committed CSVs.
SYNTHETIC_PATHS = {
    "breakout_success": breakout_success,
    "false_breakout": false_breakout,
    "risk_off_spy": risk_off_spy,
}
