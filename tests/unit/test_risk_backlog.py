"""Start-of-run M7 trigger-backlog selection contract."""

from __future__ import annotations

from datetime import date

import pytest

from smm.core.errors import DataValidationError
from smm.domain.enums import SignalState
from smm.domain.identity import make_logical_signal_id, make_setup_key
from smm.risk import open_trigger_backlog, partition_trigger_backlog
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


def partition(
    *transitions: SignalTransition,
    max_age_sessions: int = 3,
):
    return partition_trigger_backlog(
        transitions,
        evaluation_as_of=EVALUATION_AS_OF,
        strategy_version=STRATEGY_VERSION,
        config_hash=CONFIG_HASH,
        sessions=(
            date(2024, 6, 17),
            date(2024, 6, 18),
            date(2024, 6, 19),
            EVALUATION_AS_OF,
        ),
        max_age_sessions=max_age_sessions,
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


def test_partition_keeps_trigger_eligible_at_age_two_under_frozen_n_three() -> None:
    trigger = transition("AAA", as_of=date(2024, 6, 18))

    result = partition(trigger)

    assert result.eligible == (trigger,)
    assert result.expirations == ()


def test_partition_expires_trigger_at_age_three_without_double_routing() -> None:
    trigger = transition("AAA", as_of=date(2024, 6, 17))

    result = partition(trigger)

    assert result.eligible == ()
    assert len(result.expirations) == 1
    expiration = result.expirations[0]
    assert expiration.signal_id == trigger.signal_id
    assert expiration.from_state is SignalState.TRIGGERED
    assert expiration.to_state is SignalState.EXPIRED
    assert expiration.as_of == EVALUATION_AS_OF
    assert expiration.reason_codes == ("trigger_backlog_expired",)
    assert expiration.strategy_version == STRATEGY_VERSION
    assert expiration.config_hash == CONFIG_HASH


def test_partition_rejects_non_positive_age_limit() -> None:
    trigger = transition("AAA", as_of=date(2024, 6, 18))

    with pytest.raises(DataValidationError, match="max_age_sessions must be a positive integer"):
        partition(trigger, max_age_sessions=0)


@pytest.mark.parametrize("max_age_sessions", [True, 3.0, "3"])
def test_partition_rejects_non_integer_age_limit(max_age_sessions: object) -> None:
    trigger = transition("AAA", as_of=date(2024, 6, 18))

    with pytest.raises(DataValidationError, match="max_age_sessions must be a positive integer"):
        partition(trigger, max_age_sessions=max_age_sessions)  # type: ignore[arg-type]


def test_partition_fails_closed_for_non_canonical_provider_calendar() -> None:
    trigger = transition("AAA", as_of=date(2024, 6, 18))

    with pytest.raises(
        DataValidationError,
        match="session calendar must be sorted with unique sessions",
    ):
        partition_trigger_backlog(
            (trigger,),
            evaluation_as_of=EVALUATION_AS_OF,
            strategy_version=STRATEGY_VERSION,
            config_hash=CONFIG_HASH,
            sessions=(
                date(2024, 6, 17),
                date(2024, 6, 19),
                date(2024, 6, 18),
                EVALUATION_AS_OF,
            ),
            max_age_sessions=3,
        )


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
