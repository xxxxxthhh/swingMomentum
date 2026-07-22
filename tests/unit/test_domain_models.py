"""Domain model construction and invariants."""

from __future__ import annotations

from datetime import date

import pytest

from smm.core.errors import StateTransitionError
from smm.domain.enums import MarketRegime, RiskVerdict, SignalState
from smm.domain.models import (
    ALLOWED_SIGNAL_TRANSITIONS,
    Bar,
    RiskDecision,
    Signal,
    StrategyIdentity,
    assert_signal_transition,
)


def test_bar_ok() -> None:
    bar = Bar(
        symbol="NVDA",
        date=date(2024, 1, 2),
        open=100,
        high=105,
        low=99,
        close=104,
        volume=1e6,
        adj_close=104,
        adj_factor=1.0,
    )
    assert bar.symbol == "NVDA"


def test_bar_rejects_bad_ohlc() -> None:
    # Adjusted fields are supplied so the failure can only come from the OHLC
    # invariant, not from a missing-field error.
    with pytest.raises(ValueError, match="high must be"):
        Bar(
            symbol="X",
            date=date(2024, 1, 2),
            open=100,
            high=90,
            low=95,
            close=100,
            volume=1,
            adj_close=100,
            adj_factor=1.0,
        )


def test_signal_construct() -> None:
    sig = Signal(
        id="SMM-V1.0.0:NVDA:abc",
        symbol="NVDA",
        as_of=date(2024, 3, 1),
        state=SignalState.DETECTED,
        setup_key="NVDA|bw20|lvl100.0000|a2024-03-01",
        strategy_version="SMM-V1.0.0",
        config_hash="0" * 64,
        reason_codes=["hard_filter_pass"],
    )
    assert sig.state is SignalState.DETECTED


def test_risk_reject_no_positive_size() -> None:
    with pytest.raises(ValueError, match="positive size"):
        RiskDecision(
            signal_id="x",
            symbol="X",
            as_of=date(2024, 1, 2),
            strategy_version="SMM-V1.0.0",
            config_hash="a" * 64,
            verdict=RiskVerdict.REJECT,
            reason_codes=("risk_portfolio_heat_limit_reached",),
            quantity=100,
            entry_reference=100,
            stop_reference=90,
            unit_risk=11,
            planned_capital=10100,
            planned_initial_risk=1100,
            sector="information_technology",
            risk_cluster="growth",
            regime=MarketRegime.RISK_ON,
        )


def test_risk_reject_size_none_ok() -> None:
    d = RiskDecision(
        signal_id="x",
        symbol="X",
        as_of=date(2024, 1, 2),
        strategy_version="SMM-V1.0.0",
        config_hash="a" * 64,
        verdict=RiskVerdict.REJECT,
        reason_codes=("risk_portfolio_heat_limit_reached",),
        quantity=0,
        entry_reference=100,
        stop_reference=90,
        unit_risk=11,
        planned_capital=0,
        planned_initial_risk=0,
        sector="information_technology",
        risk_cluster="growth",
        regime=MarketRegime.RISK_ON,
    )
    assert d.verdict is RiskVerdict.REJECT


def test_allowed_transition() -> None:
    assert_signal_transition(SignalState.DETECTED, SignalState.WATCHLISTED)
    assert_signal_transition(SignalState.ELIGIBLE, SignalState.RISK_ACCEPTED)
    assert_signal_transition(SignalState.TRIGGERED, SignalState.RISK_ACCEPTED)
    assert_signal_transition(SignalState.TRIGGERED, SignalState.RISK_REJECTED)


def test_illegal_transition() -> None:
    with pytest.raises(StateTransitionError):
        assert_signal_transition(SignalState.DETECTED, SignalState.ACTIVE)


def test_terminal_states_have_no_exits() -> None:
    for terminal in (
        SignalState.CANCELLED,
        SignalState.EXITED,
        SignalState.STOPPED,
        SignalState.EXPIRED,
    ):
        assert ALLOWED_SIGNAL_TRANSITIONS[terminal] == frozenset()


def test_strategy_identity() -> None:
    ident = StrategyIdentity(version="SMM-V1.0.0", config_hash="abc")
    assert ident.version.startswith("SMM-")
