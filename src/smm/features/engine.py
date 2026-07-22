"""Per-symbol feature computation (Plan v1.1 M2, ADR 2026-07-22).

Two structural guarantees, both deliberate:

**No look-ahead comes from the input, not from discipline.** The series is
truncated to ``date <= as_of`` once, at :func:`compute_features`. A feature
therefore *cannot* read the future, because future bars are not in its input —
there is nothing to remember to get right in each rolling function.

**Only the adjusted series is visible.** Bars are converted to
:class:`~smm.domain.views.AdjustedBar` on entry, which does not expose raw OHLC
at all (M1 ADR §3.3). Dollar volume uses ``adj_close * volume`` per ADR R4
rather than opening a second door to the primary series: the 20-day window sits
where ``adj_factor`` has converged to ~1.0, so the difference is far below what
could move a $20M threshold.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from smm.config.schema import FeaturesSection
from smm.domain.models import Bar
from smm.domain.views import AdjustedBar, to_adjusted
from smm.features.rolling import (
    atr,
    ema,
    highest,
    max_drawdown,
    slope,
    sma,
    total_return,
)

#: Why a symbol produced no features. Recorded rather than silently dropped.
REASON_INSUFFICIENT_HISTORY = "insufficient_history"
REASON_NO_BARS_AS_OF = "no_bars_as_of"


@dataclass(frozen=True, slots=True)
class SymbolFeatures:
    """One symbol's feature vector at ``as_of``. Every field may be ``None``."""

    symbol: str
    as_of: date
    bar_count: int

    sma_fast: float | None
    sma_slow: float | None
    ema: float | None
    sma_fast_slope: float | None
    sma_slow_slope: float | None
    atr: float | None
    returns: dict[int, float | None]
    high_52w: float | None
    distance_from_high: float | None
    drawdown: float | None
    extension_atr: float | None
    avg_dollar_volume: float | None
    close: float


@dataclass(frozen=True, slots=True)
class ExcludedSymbol:
    """A symbol that legitimately produced no features.

    Distinct from a validation failure, which halts the run: a newly listed
    constituent is not bad data, it simply does not meet a precondition.
    """

    symbol: str
    as_of: date
    reason: str
    bar_count: int


def _truncate(bars: Sequence[Bar], as_of: date) -> list[AdjustedBar]:
    """The single point where the future is cut off."""
    return [to_adjusted(b) for b in bars if b.date <= as_of]


def _trailing_sma(values: Sequence[float], window: int, count: int) -> list[float]:
    """The last ``count`` values of the rolling ``window``-SMA series."""
    points: list[float] = []
    for end in range(len(values) - count + 1, len(values) + 1):
        if end < window:
            continue
        value = sma(values[:end], window)
        if value is not None:
            points.append(value)
    return points


def compute_features(
    bars: Sequence[Bar],
    *,
    as_of: date,
    cfg: FeaturesSection,
) -> SymbolFeatures | ExcludedSymbol:
    """Compute one symbol's features, or say why it was excluded.

    Returns :class:`ExcludedSymbol` rather than raising: too little history is a
    normal condition for part of a universe, and halting the whole daily run for
    one newly listed constituent would make fail-closed noisy enough that
    someone eventually routes around it.
    """
    if not bars:
        return ExcludedSymbol(
            symbol="", as_of=as_of, reason=REASON_NO_BARS_AS_OF, bar_count=0
        )
    symbol = bars[0].symbol
    view = _truncate(bars, as_of)
    if len(view) < cfg.min_history_bars:
        return ExcludedSymbol(
            symbol=symbol,
            as_of=as_of,
            reason=(
                REASON_NO_BARS_AS_OF if not view else REASON_INSUFFICIENT_HISTORY
            ),
            bar_count=len(view),
        )

    closes = [b.adj_close for b in view]
    highs = [b.adj_high for b in view]
    lows = [b.adj_low for b in view]
    volumes = [b.volume for b in view]

    # Only the tail of each moving-average series is needed — slope reads the
    # last `slope_window + 1` points. Building the whole series would be
    # quadratic for no benefit.
    fast_series = _trailing_sma(closes, cfg.sma_fast, cfg.slope_window + 1)
    slow_series = _trailing_sma(closes, cfg.sma_slow, cfg.slope_window + 1)

    atr_value = atr(highs, lows, closes, cfg.atr_window)
    ema_value = ema(closes, cfg.ema_window)
    high = highest(highs, cfg.high_window)
    last_close = closes[-1]

    dollar_volumes = [
        close * volume
        for close, volume in zip(
            closes[-cfg.dollar_volume_window :],
            volumes[-cfg.dollar_volume_window :],
            strict=True,
        )
    ]

    return SymbolFeatures(
        symbol=symbol,
        as_of=as_of,
        bar_count=len(view),
        sma_fast=sma(closes, cfg.sma_fast),
        sma_slow=sma(closes, cfg.sma_slow),
        ema=ema_value,
        sma_fast_slope=slope(fast_series, cfg.slope_window),
        sma_slow_slope=slope(slow_series, cfg.slope_window),
        atr=atr_value,
        returns={w: total_return(closes, w) for w in cfg.return_windows},
        high_52w=high,
        distance_from_high=(
            None if high is None or high <= 0 else (high - last_close) / high
        ),
        drawdown=max_drawdown(closes, cfg.drawdown_window),
        extension_atr=(
            None
            if ema_value is None or atr_value is None or atr_value <= 0
            else (last_close - ema_value) / atr_value
        ),
        avg_dollar_volume=(
            sum(dollar_volumes) / len(dollar_volumes) if dollar_volumes else None
        ),
        close=last_close,
    )
