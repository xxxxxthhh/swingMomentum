"""Per-symbol features (Plan v1.1 M2, ADR 2026-07-22)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from smm.config.loader import load_config
from smm.data.generator import synthetic_universe
from smm.features.engine import (
    REASON_INSUFFICIENT_HISTORY,
    ExcludedSymbol,
    SymbolFeatures,
    compute_features,
)

CFG = load_config(None).config.features
UNIVERSE = synthetic_universe()
BARS = list(UNIVERSE["SYNT1"].bars)
AS_OF = BARS[-1].date


def features(bars=BARS, as_of=AS_OF):
    return compute_features(bars, as_of=as_of, cfg=CFG)


# --- happy path ------------------------------------------------------------


def test_every_feature_is_present_past_the_gate() -> None:
    """The history gate is meaningless if a gated symbol still yields None."""
    f = features()
    assert isinstance(f, SymbolFeatures)
    for name in (
        "sma_fast",
        "sma_slow",
        "ema",
        "sma_fast_slope",
        "sma_slow_slope",
        "atr",
        "high_52w",
        "distance_from_high",
        "drawdown",
        "extension_atr",
        "avg_dollar_volume",
    ):
        assert getattr(f, name) is not None, f"{name} is None past min_history_bars"
    assert all(v is not None for v in f.returns.values())


def test_returns_cover_the_configured_windows() -> None:
    assert set(features().returns) == set(CFG.return_windows)


# --- history gate ----------------------------------------------------------


def test_exactly_min_history_passes() -> None:
    bars = BARS[-CFG.min_history_bars :]
    assert isinstance(compute_features(bars, as_of=bars[-1].date, cfg=CFG), SymbolFeatures)


def test_one_bar_short_is_excluded() -> None:
    bars = BARS[-(CFG.min_history_bars - 1) :]
    result = compute_features(bars, as_of=bars[-1].date, cfg=CFG)
    assert isinstance(result, ExcludedSymbol)
    assert result.reason == REASON_INSUFFICIENT_HISTORY
    assert result.bar_count == CFG.min_history_bars - 1


def test_exclusion_names_the_symbol_rather_than_raising() -> None:
    """A newly listed constituent must not halt the whole run."""
    result = compute_features(BARS[:10], as_of=AS_OF, cfg=CFG)
    assert isinstance(result, ExcludedSymbol)
    assert result.symbol == "SYNT1"


def test_partial_shortfall_excludes_only_the_short_symbols() -> None:
    results = {
        "SYNT1": compute_features(BARS, as_of=AS_OF, cfg=CFG),
        "SHORT": compute_features(BARS[:50], as_of=AS_OF, cfg=CFG),
    }
    assert isinstance(results["SYNT1"], SymbolFeatures)
    assert isinstance(results["SHORT"], ExcludedSymbol)


# --- no look-ahead ---------------------------------------------------------


def test_as_of_truncates_the_series() -> None:
    """Values at as_of must ignore everything after it."""
    mid = BARS[-30].date
    with_future = compute_features(BARS, as_of=mid, cfg=CFG)
    without_future = compute_features(
        [b for b in BARS if b.date <= mid], as_of=mid, cfg=CFG
    )
    assert with_future == without_future


@pytest.mark.parametrize(
    "field",
    ["sma_fast", "sma_slow", "ema", "atr", "high_52w", "drawdown", "extension_atr"],
)
def test_no_rolling_feature_reads_the_future(field: str) -> None:
    mid = BARS[-40].date
    full = compute_features(BARS, as_of=mid, cfg=CFG)
    truncated = compute_features([b for b in BARS if b.date <= mid], as_of=mid, cfg=CFG)
    assert getattr(full, field) == getattr(truncated, field)


def test_as_of_before_any_bar_is_excluded_not_crashed() -> None:
    result = compute_features(BARS, as_of=BARS[0].date - timedelta(days=1), cfg=CFG)
    assert isinstance(result, ExcludedSymbol)
    assert result.bar_count == 0


# --- adjusted-only ---------------------------------------------------------


def test_features_use_the_adjusted_series() -> None:
    """A dividend-bearing bar must move the features, proving adj_close is read.

    If the engine read raw close, halving adj_close would change nothing.
    """
    halved = [b.model_copy(update={"adj_close": b.close * 0.5, "adj_factor": 0.5}) for b in BARS]
    baseline = compute_features(BARS, as_of=AS_OF, cfg=CFG)
    adjusted = compute_features(halved, as_of=AS_OF, cfg=CFG)
    assert adjusted.close == pytest.approx(baseline.close * 0.5)
    assert adjusted.sma_slow == pytest.approx(baseline.sma_slow * 0.5)


def test_dollar_volume_uses_the_adjusted_close() -> None:
    """ADR R4: adj_close x volume, not a second door to the primary series."""
    f = features()
    recent = [b for b in BARS if b.date <= AS_OF][-CFG.dollar_volume_window :]
    expected = sum(b.adj_close * b.volume for b in recent) / len(recent)
    assert f.avg_dollar_volume == pytest.approx(expected)


# --- derived quantities ----------------------------------------------------


def test_distance_from_high_is_non_negative() -> None:
    assert features().distance_from_high >= 0


def test_drawdown_is_non_positive() -> None:
    assert features().drawdown <= 0


def test_extension_is_measured_in_atr() -> None:
    f = features()
    assert f.extension_atr == pytest.approx((f.close - f.ema) / f.atr)


def test_a_laggard_scores_below_a_leader() -> None:
    leader = compute_features(list(UNIVERSE["SYNT1"].bars), as_of=AS_OF, cfg=CFG)
    laggard = compute_features(list(UNIVERSE["SYNH4"].bars), as_of=AS_OF, cfg=CFG)
    assert leader.returns[126] > laggard.returns[126]
