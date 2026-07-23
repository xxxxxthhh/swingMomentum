"""RiskDecision-to-lifecycle projection contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from smm.core.errors import DataValidationError
from smm.domain.enums import MarketRegime, RiskVerdict, SignalState
from smm.domain.identity import make_logical_signal_id, make_setup_key
from smm.domain.models import RiskDecision
from smm.risk import project_risk_decisions_to_transitions
from smm.signals.lifecycle import SignalTransition

TRIGGERED_AS_OF = date(2024, 6, 19)
DECISION_AS_OF = date(2024, 6, 20)
WATCHLIST_ENTRY = date(2024, 6, 18)
STRATEGY_VERSION = "SMM-V1.1.0"
CONFIG_HASH = "a" * 64
CIRCUIT_STATE_IDENTITY = "b" * 64


def triggered_transition(symbol: str = "AAA") -> SignalTransition:
    setup_key = make_setup_key(
        symbol,
        breakout_window=20,
        watchlist_entry=WATCHLIST_ENTRY,
    )
    return SignalTransition(
        signal_id=make_logical_signal_id(
            symbol=symbol,
            setup_key=setup_key,
            strategy_version=STRATEGY_VERSION,
        ),
        symbol=symbol,
        setup_key=setup_key,
        watchlist_entry=WATCHLIST_ENTRY,
        from_state=SignalState.DETECTED,
        to_state=SignalState.TRIGGERED,
        as_of=TRIGGERED_AS_OF,
        reason_codes=("breakout_confirmed",),
        strategy_version=STRATEGY_VERSION,
        config_hash=CONFIG_HASH,
        breakout_level=101.25,
        relative_volume=1.75,
        extension_atr=0.5,
    )


def decision(source: SignalTransition, **updates: object) -> RiskDecision:
    verdict = updates.get("verdict", RiskVerdict.ACCEPT)
    values: dict[str, object] = {
        "signal_id": source.signal_id,
        "symbol": source.symbol,
        "as_of": DECISION_AS_OF,
        "strategy_version": STRATEGY_VERSION,
        "config_hash": CONFIG_HASH,
        "entry_risk_multiplier": Decimal("0.5"),
        "circuit_state_identity": CIRCUIT_STATE_IDENTITY,
        "verdict": verdict,
        "reason_codes": ("risk_sized_by_per_trade",),
        "quantity": 9,
        "entry_reference": Decimal("101"),
        "stop_reference": Decimal("95"),
        "unit_risk": Decimal("6"),
        "planned_capital": Decimal("909"),
        "planned_initial_risk": Decimal("54"),
        "sector": "technology",
        "risk_cluster": "software",
        "regime": MarketRegime.RISK_ON,
    }
    if verdict is RiskVerdict.REJECT:
        values.update(
            quantity=0,
            planned_capital=Decimal("0"),
            planned_initial_risk=Decimal("0"),
        )
    values.update(updates)
    return RiskDecision(**values)


def test_projects_each_verdict_in_evaluation_order_and_copies_trigger_facts() -> None:
    first_source = triggered_transition("AAA")
    second_source = triggered_transition("BBB")
    rejected = decision(
        second_source,
        verdict=RiskVerdict.REJECT,
        reason_codes=("risk_off_new_entries_blocked", "risk_cash_exhausted"),
        regime=MarketRegime.RISK_OFF,
    )
    accepted = decision(first_source, reason_codes=("risk_sized_by_per_trade",))

    transitions = project_risk_decisions_to_transitions(
        (rejected, accepted),
        (first_source, second_source),
    )

    assert [transition.signal_id for transition in transitions] == [
        second_source.signal_id,
        first_source.signal_id,
    ]
    assert [transition.to_state for transition in transitions] == [
        SignalState.RISK_REJECTED,
        SignalState.RISK_ACCEPTED,
    ]
    assert all(transition.from_state is SignalState.TRIGGERED for transition in transitions)
    assert all(transition.as_of == DECISION_AS_OF for transition in transitions)
    assert transitions[0].reason_codes == rejected.reason_codes
    assert transitions[1].reason_codes == accepted.reason_codes
    assert transitions[0].setup_key == second_source.setup_key
    assert transitions[0].watchlist_entry == second_source.watchlist_entry
    assert transitions[0].breakout_level == second_source.breakout_level
    assert transitions[0].relative_volume == second_source.relative_volume
    assert transitions[0].extension_atr == second_source.extension_atr


def test_empty_risk_decision_batch_projects_to_an_empty_tuple() -> None:
    assert project_risk_decisions_to_transitions((), ()) == ()


@pytest.mark.parametrize(
    "decision_as_of",
    [TRIGGERED_AS_OF, date(2024, 6, 18)],
    ids=["same_session", "earlier_session"],
)
def test_rejects_risk_decision_not_strictly_after_trigger(
    decision_as_of: date,
) -> None:
    source = triggered_transition()

    with pytest.raises(DataValidationError, match="must follow source transition"):
        project_risk_decisions_to_transitions(
            (decision(source, as_of=decision_as_of),),
            (source,),
        )


def test_rejects_missing_or_non_triggered_latest_source_transition() -> None:
    source = triggered_transition()
    non_triggered = SignalTransition(
        signal_id=source.signal_id,
        symbol=source.symbol,
        setup_key=source.setup_key,
        watchlist_entry=source.watchlist_entry,
        from_state=SignalState.TRIGGERED,
        to_state=SignalState.RISK_REJECTED,
        as_of=DECISION_AS_OF,
        reason_codes=("risk_off_new_entries_blocked",),
        strategy_version=source.strategy_version,
        config_hash=source.config_hash,
        breakout_level=source.breakout_level,
        relative_volume=source.relative_volume,
        extension_atr=source.extension_atr,
    )
    later_decision = decision(source, as_of=date(2024, 6, 21))

    with pytest.raises(DataValidationError, match="missing source transition"):
        project_risk_decisions_to_transitions((decision(source),), ())
    with pytest.raises(DataValidationError, match="must be triggered"):
        project_risk_decisions_to_transitions(
            (later_decision,),
            (source, non_triggered),
        )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"symbol": "ZZZ"}, "symbol mismatch"),
        ({"strategy_version": "SMM-V1.2.0"}, "strategy version mismatch"),
        ({"config_hash": "c" * 64}, "config hash mismatch"),
    ],
)
def test_rejects_identity_mismatch_with_trigger_source(
    updates: dict[str, str],
    message: str,
) -> None:
    source = triggered_transition()

    with pytest.raises(DataValidationError, match=message):
        project_risk_decisions_to_transitions((decision(source, **updates),), (source,))


def test_reuses_risk_decision_batch_identity_validation() -> None:
    first = triggered_transition("AAA")
    second = triggered_transition("BBB")

    with pytest.raises(DataValidationError, match="cannot repeat signal_id"):
        project_risk_decisions_to_transitions(
            (decision(first), decision(first)),
            (first, second),
        )
    with pytest.raises(DataValidationError, match="batch identity mismatch"):
        project_risk_decisions_to_transitions(
            (decision(first), decision(second, circuit_state_identity="c" * 64)),
            (first, second),
        )
