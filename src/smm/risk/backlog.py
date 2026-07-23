"""Pure selection of persisted M7 open-trigger backlog."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from smm.core.errors import DataValidationError
from smm.domain.enums import SignalState
from smm.signals.lifecycle import SignalTransition, latest_transitions, session_age


@dataclass(frozen=True, slots=True)
class TriggerBacklogPartition:
    """Open backlog split before any future Risk Engine consumption."""

    eligible: tuple[SignalTransition, ...]
    expirations: tuple[SignalTransition, ...]


def open_trigger_backlog(
    transitions: Sequence[SignalTransition],
    *,
    evaluation_as_of: date,
    strategy_version: str,
    config_hash: str,
) -> tuple[SignalTransition, ...]:
    """Return pre-evaluation latest-state triggers in deterministic order.

    This is intentionally a read-only M7 precondition: it validates the
    persisted transition log but neither evaluates risk nor writes lifecycle
    events. A source after ``evaluation_as_of`` is a no-lookahead violation;
    a source on that date is valid input but is not start-of-run backlog.
    """
    sources = tuple(transitions)
    if any(not isinstance(item, SignalTransition) for item in sources):
        raise DataValidationError(
            "risk backlog selection requires SignalTransition source items"
        )
    if any(row.as_of > evaluation_as_of for row in sources):
        raise DataValidationError(
            "risk backlog transition as_of must not follow evaluation_as_of"
        )
    if any(row.strategy_version != strategy_version for row in sources):
        raise DataValidationError("risk backlog strategy version mismatch")
    if any(row.config_hash != config_hash for row in sources):
        raise DataValidationError("risk backlog config hash mismatch")

    latest = latest_transitions(sources)
    return tuple(
        sorted(
            (
                row
                for row in latest.values()
                if row.to_state is SignalState.TRIGGERED
                and row.as_of < evaluation_as_of
            ),
            key=lambda row: (row.as_of, row.signal_id),
        )
    )


def partition_trigger_backlog(
    transitions: Sequence[SignalTransition],
    *,
    evaluation_as_of: date,
    strategy_version: str,
    config_hash: str,
    sessions: Sequence[date],
    max_age_sessions: int,
) -> TriggerBacklogPartition:
    """Partition prior triggers into risk-eligible rows and terminal expiry events.

    Age is the shared provider-session index distance from the persisted trigger
    session D to this evaluation session X.  An item at or beyond the frozen
    limit receives exactly one ``TRIGGERED -> EXPIRED`` projection and is never
    returned in ``eligible`` for a caller to hand to the Risk Engine.
    """
    if type(max_age_sessions) is not int or max_age_sessions < 1:
        raise DataValidationError("max_age_sessions must be a positive integer")

    open_triggers = open_trigger_backlog(
        transitions,
        evaluation_as_of=evaluation_as_of,
        strategy_version=strategy_version,
        config_hash=config_hash,
    )
    eligible: list[SignalTransition] = []
    expirations: list[SignalTransition] = []
    for trigger in open_triggers:
        age = session_age(sessions, trigger.as_of, evaluation_as_of)
        if age < max_age_sessions:
            eligible.append(trigger)
            continue
        expirations.append(
            SignalTransition(
                signal_id=trigger.signal_id,
                symbol=trigger.symbol,
                setup_key=trigger.setup_key,
                watchlist_entry=trigger.watchlist_entry,
                from_state=SignalState.TRIGGERED,
                to_state=SignalState.EXPIRED,
                as_of=evaluation_as_of,
                reason_codes=("trigger_backlog_expired",),
                strategy_version=trigger.strategy_version,
                config_hash=trigger.config_hash,
                breakout_level=trigger.breakout_level,
                relative_volume=trigger.relative_volume,
                extension_atr=trigger.extension_atr,
            )
        )
    return TriggerBacklogPartition(
        eligible=tuple(eligible),
        expirations=tuple(expirations),
    )
