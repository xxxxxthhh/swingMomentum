"""M7 D-anchored candidate and external portfolio-input contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.domain.enums import MarketRegime, SignalState
from smm.domain.identity import make_logical_signal_id, make_setup_key
from smm.domain.models import Bar, PortfolioSnapshot, PrintBar
from smm.features.cross_section import ScoredSymbol
from smm.features.engine import SymbolFeatures
from smm.risk.candidate_inputs import (
    EvaluationFacts,
    TriggerCandidateSource,
    build_candidate_evaluation_inputs,
)
from smm.signals.lifecycle import SignalTransition

REPO = Path(__file__).resolve().parents[2]
CONFIG = load_config(REPO / "configs" / "smm_v1_1_0.yaml").config
TRIGGER_AS_OF = date(2024, 6, 19)
EVALUATION_AS_OF = date(2024, 6, 20)
WATCHLIST_ENTRY = date(2024, 6, 17)
VERSION = "SMM-V1.1.0"
CONFIG_HASH = "a" * 64


def transition(symbol: str = "AAA") -> SignalTransition:
    setup_key = make_setup_key(
        symbol,
        breakout_window=20,
        watchlist_entry=WATCHLIST_ENTRY,
    )
    return SignalTransition(
        signal_id=make_logical_signal_id(
            symbol=symbol,
            setup_key=setup_key,
            strategy_version=VERSION,
        ),
        symbol=symbol,
        setup_key=setup_key,
        watchlist_entry=WATCHLIST_ENTRY,
        from_state=SignalState.WATCHLISTED,
        to_state=SignalState.TRIGGERED,
        as_of=TRIGGER_AS_OF,
        reason_codes=("breakout_confirmed",),
        strategy_version=VERSION,
        config_hash=CONFIG_HASH,
        breakout_level=104.0,
        relative_volume=1.5,
        extension_atr=0.8,
    )


def print_bar(day: date, *, symbol: str = "AAA", low: float, close: float) -> PrintBar:
    return PrintBar(
        symbol=symbol,
        date=day,
        open=close - 1,
        high=close + 1,
        low=low,
        close=close,
        volume=1_000_000,
    )


def feature(symbol: str = "AAA", **updates: object) -> SymbolFeatures:
    values: dict[str, object] = {
        "symbol": symbol,
        "as_of": TRIGGER_AS_OF,
        "bar_count": 252,
        "sma_fast": 100.0,
        "sma_slow": 90.0,
        "ema": 101.0,
        "sma_fast_slope": 1.0,
        "sma_slow_slope": 1.0,
        "atr": 5.0,
        "returns": {21: 0.1, 63: 0.2, 126: 0.3},
        "high_52w": 110.0,
        "distance_from_high": 0.05,
        "drawdown": 0.02,
        "extension_atr": 0.8,
        "avg_dollar_volume": 50_000_000.0,
        "close": 105.0,
    }
    values.update(updates)
    return SymbolFeatures(**values)


def score(symbol: str = "AAA", **updates: object) -> ScoredSymbol:
    values: dict[str, object] = {
        "symbol": symbol,
        "sector": "information_technology",
        "rs_spy_short": 0.1,
        "rs_spy_long": 0.2,
        "rs_sector": 0.05,
        "momentum_score": 80.0,
        "relative_strength_score": 60.0,
    }
    values.update(updates)
    return ScoredSymbol(**values)


def source(**updates: object) -> TriggerCandidateSource:
    values: dict[str, object] = {
        "transition": transition(),
        "sessions": (WATCHLIST_ENTRY, date(2024, 6, 18), TRIGGER_AS_OF),
        "print_bars": (
            print_bar(WATCHLIST_ENTRY, low=98.0, close=100.0),
            print_bar(date(2024, 6, 18), low=96.0, close=102.0),
            print_bar(TRIGGER_AS_OF, low=97.0, close=105.0),
        ),
        "print_provenance_id": "split-history:AAA:2024-06-19",
        "trigger_features": feature(),
        "trigger_score": score(),
        "feature_strategy_version": VERSION,
        "feature_config_hash": CONFIG_HASH,
    }
    values.update(updates)
    return TriggerCandidateSource(**values)


def evaluation(**updates: object) -> EvaluationFacts:
    values: dict[str, object] = {
        "as_of": EVALUATION_AS_OF,
        "regime": MarketRegime.RISK_ON,
        "strategy_version": VERSION,
        "config_hash": CONFIG_HASH,
    }
    values.update(updates)
    return EvaluationFacts(**values)


def portfolio(**updates: object) -> PortfolioSnapshot:
    values: dict[str, object] = {
        "as_of": EVALUATION_AS_OF,
        "account_equity": "100000",
        "available_cash": "100000",
        "gross_exposure_capital": "0",
        "portfolio_initial_risk": "0",
        "sector_initial_risk": {},
        "cluster_initial_risk": {},
        "open_symbols": frozenset(),
        "reserved_signal_ids": frozenset(),
        "strategy_version": VERSION,
        "config_hash": CONFIG_HASH,
    }
    values.update(updates)
    return PortfolioSnapshot(**values)


def build(**updates: object):
    values: dict[str, object] = {
        "sources": (source(),),
        "evaluation": evaluation(),
        "portfolio": portfolio(),
        "stop": CONFIG.stop,
        "execution": CONFIG.execution,
    }
    values.update(updates)
    return build_candidate_evaluation_inputs(**values)


def test_builds_x_identity_candidate_from_d_anchored_true_print_provenance() -> None:
    result = build()

    assert result.portfolio == portfolio()
    assert len(result.candidates) == len(result.provenance) == 1
    candidate = result.candidates[0]
    provenance = result.provenance[0]
    assert candidate.as_of == EVALUATION_AS_OF
    assert candidate.regime is MarketRegime.RISK_ON
    assert candidate.entry_reference == Decimal("105.0")
    assert candidate.stop_reference == Decimal("95.0")
    assert candidate.estimated_entry_cost_per_share == Decimal("0.083750")
    assert candidate.estimated_total_cost_per_share == Decimal("0.160000")
    assert candidate.momentum_score == 80.0
    assert candidate.relative_strength_score == 60.0
    assert candidate.sector == "information_technology"
    assert candidate.risk_cluster == "unclassified"
    assert provenance.trigger_as_of == TRIGGER_AS_OF
    assert provenance.trigger_feature_as_of == TRIGGER_AS_OF
    assert provenance.print_sessions == (
        WATCHLIST_ENTRY,
        date(2024, 6, 18),
        TRIGGER_AS_OF,
    )
    assert provenance.print_provenance_id == "split-history:AAA:2024-06-19"
    assert provenance.feature_strategy_version == VERSION
    assert provenance.feature_config_hash == CONFIG_HASH


def test_rejects_missing_d_print_bar_as_unretrievable_not_x_recomputed() -> None:
    with pytest.raises(DataValidationError, match="retrievable PrintBar coverage"):
        build(
            sources=(
                source(
                    print_bars=(
                        print_bar(WATCHLIST_ENTRY, low=98.0, close=100.0),
                        print_bar(date(2024, 6, 18), low=96.0, close=102.0),
                    )
                ),
            )
        )


def test_rejects_unretrievable_d_feature_snapshot_identity() -> None:
    with pytest.raises(DataValidationError, match="trigger feature identity"):
        build(
            sources=(
                source(trigger_features=feature(as_of=EVALUATION_AS_OF)),
            )
        )


def test_rejects_d_feature_snapshot_from_a_different_config_identity() -> None:
    with pytest.raises(DataValidationError, match="feature snapshot identity"):
        build(sources=(source(feature_config_hash="b" * 64),))


def test_rejects_d_prints_without_reconstruction_provenance_identity() -> None:
    with pytest.raises(DataValidationError, match="PrintBar provenance identity"):
        build(sources=(source(print_provenance_id=""),))


def test_rejects_provider_native_bar_at_d_print_boundary() -> None:
    provider_bar = Bar(
        symbol="AAA",
        date=TRIGGER_AS_OF,
        open=104.0,
        high=106.0,
        low=97.0,
        close=105.0,
        volume=1_000_000,
        adj_close=105.0,
        adj_factor=1.0,
    )

    with pytest.raises(DataValidationError, match="PrintBar"):
        build(
            sources=(
                source(
                    print_bars=(
                        print_bar(WATCHLIST_ENTRY, low=98.0, close=100.0),
                        print_bar(date(2024, 6, 18), low=96.0, close=102.0),
                        provider_bar,
                    )
                ),
            )
        )


def test_rejects_snapshot_that_does_not_match_x_evaluation_identity() -> None:
    with pytest.raises(DataValidationError, match="portfolio snapshot identity"):
        build(portfolio=portfolio(as_of=TRIGGER_AS_OF))


def test_rejects_non_triggered_source_and_never_retriggers_at_x() -> None:
    non_triggered = transition().model_copy(update={"to_state": SignalState.EXPIRED})

    with pytest.raises(DataValidationError, match="must be TRIGGERED"):
        build(sources=(source(transition=non_triggered),))


def test_rejects_same_day_trigger_instead_of_retriggering_at_x() -> None:
    same_day = transition().model_copy(update={"as_of": EVALUATION_AS_OF})

    with pytest.raises(DataValidationError, match="must follow trigger as_of"):
        build(
            sources=(
                source(
                    transition=same_day,
                    sessions=(WATCHLIST_ENTRY, date(2024, 6, 18), EVALUATION_AS_OF),
                    print_bars=(
                        print_bar(WATCHLIST_ENTRY, low=98.0, close=100.0),
                        print_bar(date(2024, 6, 18), low=96.0, close=102.0),
                        print_bar(EVALUATION_AS_OF, low=97.0, close=105.0),
                    ),
                    trigger_features=feature(as_of=EVALUATION_AS_OF),
                ),
            )
        )


@pytest.mark.parametrize(
    ("sessions", "print_bars"),
    [
        (
            (date(2024, 6, 18), TRIGGER_AS_OF),
            (
                print_bar(date(2024, 6, 18), low=96.0, close=102.0),
                print_bar(TRIGGER_AS_OF, low=97.0, close=105.0),
            ),
        ),
        (
            (WATCHLIST_ENTRY, date(2024, 6, 18)),
            (
                print_bar(WATCHLIST_ENTRY, low=98.0, close=100.0),
                print_bar(date(2024, 6, 18), low=96.0, close=102.0),
            ),
        ),
    ],
)
def test_rejects_print_session_window_that_does_not_span_watchlist_through_d(
    sessions: tuple[date, ...],
    print_bars: tuple[PrintBar, ...],
) -> None:
    with pytest.raises(DataValidationError, match="cover watchlist entry through trigger"):
        build(sources=(source(sessions=sessions, print_bars=print_bars),))


def test_rejects_d_anchored_stop_outside_frozen_atr_distance_band() -> None:
    with pytest.raises(DataValidationError, match="outside frozen ATR bounds"):
        build(
            sources=(
                source(
                    print_bars=(
                        print_bar(WATCHLIST_ENTRY, low=98.0, close=100.0),
                        print_bar(date(2024, 6, 18), low=90.0, close=102.0),
                        print_bar(TRIGGER_AS_OF, low=97.0, close=105.0),
                    )
                ),
            )
        )


def test_rejects_d_anchored_stop_that_is_not_positive() -> None:
    impossible_stop = CONFIG.stop.model_copy(update={"atr_buffer": 100.0})

    with pytest.raises(DataValidationError, match="does not yield a positive stop"):
        build(stop=impossible_stop)


@pytest.mark.parametrize(
    ("entry_cost", "total_cost"),
    [
        (Decimal("0"), Decimal("0.1")),
        (Decimal("0.1"), Decimal("0.09")),
    ],
)
def test_rejects_incomplete_or_non_positive_cost_estimate(
    entry_cost: Decimal,
    total_cost: Decimal,
) -> None:
    from smm.risk import candidate_inputs

    with pytest.raises(DataValidationError, match="positive and complete"):
        candidate_inputs._validate_cost_estimate(
            entry_cost=entry_cost,
            total_cost=total_cost,
        )


def test_rejects_internally_inconsistent_frozen_stop_distance_config() -> None:
    inconsistent_stop = CONFIG.stop.model_copy(
        update={"min_stop_distance_atr": 3.0, "max_stop_distance_atr": 2.5}
    )

    with pytest.raises(DataValidationError, match="min_stop_distance_atr"):
        build(stop=inconsistent_stop)
