"""Market regime (constitution §14, ADR R2)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from smm.config.loader import load_config
from smm.core.errors import DataValidationError, FailClosedError
from smm.data.generator import risk_off_spy, synthetic_universe
from smm.domain.enums import MarketRegime
from smm.features.engine import ExcludedSymbol, SymbolFeatures, compute_features
from smm.features.regime import classify_regime, resolve_regime

CONFIG = load_config(None).config


def benchmark(*, close: float, fast: float, slow: float) -> SymbolFeatures:
    return SymbolFeatures(
        symbol="SPY",
        as_of=date(2024, 6, 7),
        bar_count=300,
        sma_fast=fast,
        sma_slow=slow,
        ema=close,
        sma_fast_slope=0.0,
        sma_slow_slope=0.0,
        atr=1.0,
        returns={21: 0.0, 63: 0.0, 126: 0.0},
        high_52w=close,
        distance_from_high=0.0,
        drawdown=0.0,
        extension_atr=0.0,
        avg_dollar_volume=1e9,
        close=close,
    )


# --- the three states ------------------------------------------------------


def test_risk_on() -> None:
    assert classify_regime(benchmark(close=110, fast=105, slow=100)) is MarketRegime.RISK_ON


def test_risk_off_below_the_slow_average() -> None:
    assert classify_regime(benchmark(close=95, fast=105, slow=100)) is MarketRegime.RISK_OFF


def test_neutral_above_slow_but_below_fast() -> None:
    assert classify_regime(benchmark(close=102, fast=105, slow=100)) is MarketRegime.NEUTRAL


def test_neutral_when_the_fast_average_is_below_the_slow() -> None:
    """Above SMA200 but the trend structure has not recovered."""
    assert classify_regime(benchmark(close=110, fast=95, slow=100)) is MarketRegime.NEUTRAL


# --- total partition -------------------------------------------------------


def test_close_equal_to_the_slow_average_is_neutral() -> None:
    """Not below it, so not Risk-Off; not above it, so not Risk-On."""
    assert classify_regime(benchmark(close=100, fast=99, slow=100)) is MarketRegime.NEUTRAL


def test_close_equal_to_the_fast_average_is_neutral() -> None:
    assert classify_regime(benchmark(close=105, fast=105, slow=100)) is MarketRegime.NEUTRAL


def test_every_arrangement_classifies_exactly_once() -> None:
    """No gap and no overlap across the boundary neighbourhood."""
    seen = set()
    for close in (98, 99, 100, 101, 105, 110):
        for fast in (99, 100, 105):
            for slow in (100,):
                regime = classify_regime(benchmark(close=close, fast=fast, slow=slow))
                assert regime in MarketRegime
                seen.add(regime)
    assert seen == {MarketRegime.RISK_ON, MarketRegime.NEUTRAL, MarketRegime.RISK_OFF}


# --- fail closed (ADR R2) --------------------------------------------------


def test_missing_benchmark_fails_the_run() -> None:
    with pytest.raises(DataValidationError, match="cannot determine market"):
        resolve_regime({}, benchmark_symbol="SPY")


def test_missing_benchmark_is_never_reported_as_risk_on() -> None:
    """The specific failure R2 exists to prevent."""
    with pytest.raises(FailClosedError):
        resolve_regime({}, benchmark_symbol="SPY")


def test_failure_names_why_the_benchmark_was_unavailable() -> None:
    excluded = {
        "SPY": ExcludedSymbol(
            symbol="SPY", as_of=date(2024, 6, 7), reason="insufficient_history", bar_count=12
        )
    }
    with pytest.raises(DataValidationError, match="insufficient_history, 12 bars"):
        resolve_regime({}, benchmark_symbol="SPY", excluded=excluded)


def test_unavailable_moving_averages_fail_rather_than_default() -> None:
    # slots=True, so replace() rather than __dict__ surgery.
    broken = replace(benchmark(close=100, fast=99, slow=100), sma_slow=None)
    with pytest.raises(DataValidationError, match="cannot classify regime"):
        classify_regime(broken)


# --- against generated paths -----------------------------------------------


def test_risk_off_fixture_classifies_risk_off() -> None:
    bars = list(risk_off_spy().bars)
    features = compute_features(bars, as_of=bars[-1].date, cfg=CONFIG.features)
    assert classify_regime(features) is MarketRegime.RISK_OFF


def test_rising_benchmark_classifies_risk_on() -> None:
    bars = list(synthetic_universe()["SPY"].bars)
    features = compute_features(bars, as_of=bars[-1].date, cfg=CONFIG.features)
    assert classify_regime(features) is MarketRegime.RISK_ON
