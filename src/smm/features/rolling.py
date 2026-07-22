"""Rolling statistics and cross-sectional ranking (ADR 2026-07-22 §4, R3).

Standard library only — no pandas or numpy. The dataset is ~600 symbols by 252
bars, so there is no performance argument, and two properties matter more than
convenience:

- **Explicit windows.** ``values[t - n + 1 : t + 1]`` is verifiable by reading
  it. ``rolling`` and ``shift`` are correct but need an extra step of reasoning,
  and no-look-ahead is the property this project can least afford to get wrong.
- **Missing is a value, not a sentinel.** Everything returns ``float | None``.
  pandas' ``NaN`` is skipped or propagated depending on each operation's
  ``skipna`` default, which is exactly how a favourable default sneaks in.

Every definition here is **frozen** by the ADR. Changing one changes historical
results, so each is pinned by a test against hand-computed values.
"""

from __future__ import annotations

from collections.abc import Sequence


def sma(values: Sequence[float], window: int) -> float | None:
    """Simple mean of the last ``window`` values, or ``None`` if too short."""
    if window <= 0:
        raise ValueError("window must be positive")
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def ema(values: Sequence[float], window: int) -> float | None:
    """Exponential mean, **seeded from the first ``window``-bar SMA** (ADR R3).

    Seeding from the first close instead would let that single value bias
    dozens of subsequent bars, and two runs starting from different history
    lengths would disagree for a long stretch.
    """
    if window <= 0:
        raise ValueError("window must be positive")
    if len(values) < window:
        return None
    alpha = 2.0 / (window + 1)
    current = sum(values[:window]) / window
    for value in values[window:]:
        current = alpha * value + (1 - alpha) * current
    return current


def true_range(high: float, low: float, previous_close: float) -> float:
    """Wilder's true range for one bar."""
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    window: int,
) -> float | None:
    """Average true range using **Wilder smoothing** (ADR R3).

    Wilder's recursion, not a simple mean of true ranges: ATR is Wilder's
    indicator and the simple-mean variant is a different, faster-decaying
    series. Seeded with the mean of the first ``window`` true ranges, which is
    how Wilder defined it.
    """
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows and closes must be the same length")
    if len(closes) < window + 1:
        return None
    ranges = [
        true_range(highs[i], lows[i], closes[i - 1]) for i in range(1, len(closes))
    ]
    current = sum(ranges[:window]) / window
    for value in ranges[window:]:
        current = (current * (window - 1) + value) / window
    return current


def total_return(values: Sequence[float], window: int) -> float | None:
    """``values[-1] / values[-1 - window] - 1`` (constitution §17.1)."""
    if window <= 0:
        raise ValueError("window must be positive")
    if len(values) < window + 1:
        return None
    base = values[-1 - window]
    if base <= 0:
        return None
    return values[-1] / base - 1.0


def slope(values: Sequence[float], window: int) -> float | None:
    """Relative change of a series over ``window`` bars (ADR R3).

    ``(x_t / x_{t-window}) - 1`` rather than an absolute difference: an absolute
    slope scales with price level, so a $500 stock would outrank a $20 one on
    price alone in any cross-sectional comparison.
    """
    if window <= 0:
        raise ValueError("window must be positive")
    if len(values) < window + 1:
        return None
    base = values[-1 - window]
    if base <= 0:
        return None
    return values[-1] / base - 1.0


def highest(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return max(values[-window:])


def max_drawdown(values: Sequence[float], window: int) -> float | None:
    """Deepest close-to-peak decline within the window (ADR R3).

    ``min(close_t / cummax(close)_t - 1)``. Returns a value ``<= 0``.
    """
    if len(values) < window:
        return None
    recent = values[-window:]
    peak = recent[0]
    worst = 0.0
    for value in recent:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    """Cross-sectional percentile on **0–100**, ties averaged (ADR R3).

    ``100 * (count_below + 0.5 * count_equal) / n``.

    Ties receive the same score by construction, and a single-element universe
    scores 50 rather than dividing by zero — a lone symbol is neither strong
    nor weak relative to a cross-section that does not exist.

    Worked example, ``[10, 20, 20, 30, 40]`` → ``[10, 40, 40, 70, 90]``.

    Callers pass only the symbols whose value is present: a missing input must
    not occupy a percentile slot, which would shift every other symbol's score.
    """
    n = len(values)
    if n == 0:
        return {}
    ordered = sorted(values.values())
    ranks: dict[str, float] = {}
    for symbol, value in values.items():
        below = sum(1 for other in ordered if other < value)
        equal = sum(1 for other in ordered if other == value)
        ranks[symbol] = 100.0 * (below + 0.5 * equal) / n
    return ranks


def weighted_score(parts: dict[str, float | None], weights: dict[str, float]) -> float | None:
    """Weighted sum, or ``None`` if **any** component is missing.

    Missing propagates rather than being dropped or renormalised: rescaling the
    surviving weights would put differently-scoped scores into the same
    cross-sectional table, which corrupts the whole ranking rather than one row.
    """
    total = 0.0
    for name, weight in weights.items():
        value = parts.get(name)
        if value is None:
            return None
        total += weight * value
    return total
