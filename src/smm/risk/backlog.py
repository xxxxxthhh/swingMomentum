"""Pure selection of persisted M7 open-trigger backlog."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from smm.core.errors import DataValidationError
from smm.domain.enums import SignalState
from smm.signals.lifecycle import SignalTransition, latest_transitions


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
