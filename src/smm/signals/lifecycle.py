"""Signal transition records and deterministic replay."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from smm.core.errors import DataValidationError
from smm.domain.enums import SignalState
from smm.domain.identity import make_logical_signal_id
from smm.domain.models import assert_signal_transition

_TERMINAL = {
    SignalState.CANCELLED,
    SignalState.EXITED,
    SignalState.STOPPED,
    SignalState.EXPIRED,
}


class SignalTransition(BaseModel):
    """One immutable lifecycle event; current state is derived by replay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: str
    symbol: str
    setup_key: str
    watchlist_entry: date
    from_state: SignalState
    to_state: SignalState
    as_of: date
    reason_codes: tuple[str, ...] = Field(min_length=1)
    strategy_version: str
    config_hash: str
    breakout_level: float | None = Field(default=None, gt=0)
    relative_volume: float | None = Field(default=None, ge=0)
    extension_atr: float | None = None

    @model_validator(mode="after")
    def valid_transition(self) -> SignalTransition:
        assert_signal_transition(self.from_state, self.to_state)
        if self.symbol != self.symbol.upper():
            raise ValueError("transition symbol must be uppercase")
        prefix = f"{self.symbol}|bw"
        suffix = f"|w{self.watchlist_entry.isoformat()}"
        if not self.setup_key.startswith(prefix) or not self.setup_key.endswith(suffix):
            raise ValueError("setup_key does not match symbol and watchlist_entry")
        window = self.setup_key[len(prefix) : -len(suffix)]
        if not window.isdigit() or int(window) < 1:
            raise ValueError("setup_key breakout window must be a positive integer")
        expected_id = make_logical_signal_id(
            symbol=self.symbol,
            setup_key=self.setup_key,
            strategy_version=self.strategy_version,
        )
        if self.signal_id != expected_id:
            raise ValueError("signal_id does not match symbol, setup_key, and strategy_version")
        if self.watchlist_entry > self.as_of:
            raise ValueError("watchlist_entry must not follow as_of")
        if not all(code.strip() for code in self.reason_codes):
            raise ValueError("reason_codes must be non-empty strings")
        return self


def latest_transitions(
    transitions: Sequence[SignalTransition],
) -> dict[str, SignalTransition]:
    """Replay the log, validating continuity and returning each latest event."""
    ordered = sorted(transitions, key=lambda row: (row.as_of, row.signal_id))
    seen_keys: dict[tuple[str, date], SignalTransition] = {}
    latest: dict[str, SignalTransition] = {}
    for row in ordered:
        key = (row.signal_id, row.as_of)
        existing = seen_keys.get(key)
        if existing is not None:
            if existing != row:
                raise DataValidationError(
                    f"conflicting transition for signal_id={row.signal_id} as_of={row.as_of}"
                )
            continue
        previous = latest.get(row.signal_id)
        if previous is None and row.from_state is not SignalState.DETECTED:
            raise DataValidationError(
                f"first transition for {row.signal_id} must start at detected, "
                f"got {row.from_state.value}"
            )
        if previous is not None and previous.to_state is not row.from_state:
            raise DataValidationError(
                f"broken transition chain for {row.signal_id}: "
                f"{previous.to_state.value} -> {row.from_state.value}"
            )
        seen_keys[key] = row
        latest[row.signal_id] = row
    return latest


def current_states(transitions: Sequence[SignalTransition]) -> dict[str, SignalState]:
    return {signal_id: row.to_state for signal_id, row in latest_transitions(transitions).items()}


def active_transitions_by_symbol(
    transitions: Sequence[SignalTransition],
) -> dict[str, SignalTransition]:
    """Return at most one non-terminal logical signal per symbol."""
    active: dict[str, SignalTransition] = {}
    for row in latest_transitions(transitions).values():
        if row.to_state in _TERMINAL:
            continue
        existing = active.get(row.symbol)
        if existing is not None and existing.signal_id != row.signal_id:
            raise DataValidationError(
                f"multiple active signals for {row.symbol}: "
                f"{existing.signal_id}, {row.signal_id}"
            )
        active[row.symbol] = row
    return active
