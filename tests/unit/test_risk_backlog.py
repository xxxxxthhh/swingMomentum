"""Start-of-run M7 trigger-backlog selection contract."""

from __future__ import annotations

from datetime import date

import pytest

from smm.core.errors import DataValidationError
from smm.domain.enums import SignalState
from smm.domain.identity import make_logical_signal_id, make_setup_key
from smm.risk import open_trigger_backlog
from smm.signals.lifecycle import SignalTransition

EVALUATION_AS_OF = date(2024, 6, 20)
WATCHLIST_ENTRY = date(2024, 6, 17)
STRATEGY_VERSION = "SMM-V1.1.0"
CONFIG_HASH = "a" * 64


def transition(
    symbol: str,
    *,
    as_of: date,
    from_state: SignalState = SignalState.DETECTED,
    to_state: SignalState = SignalState.TRIGGERED,
    strategy_version: str = STRATEGY_VERSION,
    config_hash: str = CONFIG_HASH,
) -> SignalTransition:
    setup_key = make_setup_key(
        symbol,
        breakout_window=20,
        watchlist_entry=WATCHLIST_ENTRY,
    )
    return SignalTransition(
        signal_id=make_logical_signal_id(
            symbol=symbol,
            setup_key=setup_key,
            strategy_version=strategy_version,
        ),
        symbol=symbol,
        setup_key=setup_key,
        watchlist_entry=WATCHLIST_ENTRY,
        from_state=from_state,
        to_state=to_state,
        as_of=as_of,
        reason_codes=("breakout_confirmed",),
        strategy_version=strategy_version,
        config_hash=config_hash,
        breakout_level=101.25,
        relative_volume=1.75,
        extension_atr=0.5,
    )


def select(*transitions: SignalTransition) -> tuple[SignalTransition, ...]:
    return open_trigger_backlog(
        transitions,
        evaluation_as_of=EVALUATION_AS_OF,
        strategy_version=STRATEGY_VERSION,
        config_hash=CONFIG_HASH,
    )


def test_empty_input_returns_empty_tuple() -> None:
    assert select() == ()


def test_selects_prior_triggers_in_deterministic_as_of_then_signal_id_order() -> None:
    later = transition("AAA", as_of=date(2024, 6, 19))
    first_tie = transition("BBB", as_of=date(2024, 6, 18))
    second_tie = transition("CCC", as_of=date(2024, 6, 18))

    assert select(second_tie, later, first_tie) == (first_tie, second_tie, later)


def test_owns_signal_id_tie_break_when_multihop_source_was_seen_earlier() -> None:
    earlier_watchlist = transition(
        "ZZZ",
        as_of=date(2024, 6, 18),
        to_state=SignalState.WATCHLISTED,
    )
    later_trigger = transition(
        "ZZZ",
        as_of=date(2024, 6, 19),
        from_state=SignalState.WATCHLISTED,
    )
    tied_single_hop = transition("AAA", as_of=date(2024, 6, 19))

    assert select(earlier_watchlist, later_trigger, tied_single_hop) == (
        tied_single_hop,
        later_trigger,
    )


def test_excludes_trigger_created_on_evaluation_session() -> None:
    prior = transition("AAA", as_of=date(2024, 6, 19))
    same_day = transition("BBB", as_of=EVALUATION_AS_OF)

    assert select(prior, same_day) == (prior,)


def test_excludes_setup_whose_latest_state_is_not_triggered() -> None:
    source = transition("AAA", as_of=date(2024, 6, 18))
    rejected = transition(
        "AAA",
        as_of=date(2024, 6, 19),
        from_state=SignalState.TRIGGERED,
        to_state=SignalState.RISK_REJECTED,
    )

    assert select(source, rejected) == ()


def test_rejects_future_dated_source_transition() -> None:
    future = transition("AAA", as_of=date(2024, 6, 21))

    with pytest.raises(DataValidationError, match="must not follow evaluation_as_of"):
        select(future)


def test_reuses_latest_transition_replay_validation_for_broken_chain() -> None:
    source = transition("AAA", as_of=date(2024, 6, 18))
    broken = transition(
        "AAA",
        as_of=date(2024, 6, 19),
        from_state=SignalState.WATCHLISTED,
        to_state=SignalState.EXPIRED,
    )

    with pytest.raises(DataValidationError, match="broken transition chain"):
        select(source, broken)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"strategy_version": "SMM-V1.2.0"}, "strategy version mismatch"),
        ({"config_hash": "b" * 64}, "config hash mismatch"),
    ],
)
def test_rejects_any_mixed_identity_source(
    updates: dict[str, str],
    message: str,
) -> None:
    matching = transition("AAA", as_of=date(2024, 6, 18))
    mismatched = transition("BBB", as_of=date(2024, 6, 19), **updates)

    with pytest.raises(DataValidationError, match=message):
        select(matching, mismatched)
