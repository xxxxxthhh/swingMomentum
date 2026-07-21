"""Core domain models."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from smm.core.errors import StateTransitionError
from smm.domain.enums import (
    OrderSide,
    PositionState,
    RiskVerdict,
    SignalState,
)


class Bar(BaseModel):
    """Single daily OHLCV bar."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def ohlc_consistency(self) -> Bar:
        if self.high < self.low:
            msg = "high must be >= low"
            raise ValueError(msg)
        if self.high < max(self.open, self.close):
            msg = "high must be >= open and close"
            raise ValueError(msg)
        if self.low > min(self.open, self.close):
            msg = "low must be <= open and close"
            raise ValueError(msg)
        return self


class StrategyIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    config_hash: str


class Signal(BaseModel):
    """Signal entity with lifecycle state."""

    model_config = ConfigDict(extra="forbid")

    id: str
    symbol: str
    as_of: date
    state: SignalState
    setup_key: str
    strategy_version: str
    config_hash: str
    reason_codes: list[str] = Field(default_factory=list)
    scores: dict[str, float] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class OrderPlan(BaseModel):
    """Planned order before (or without) fill."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    signal_id: str
    symbol: str
    side: OrderSide
    qty: float | None = Field(default=None, ge=0)
    entry_ref: float | None = Field(default=None, gt=0)
    stop_ref: float | None = Field(default=None, gt=0)
    as_of: date | None = None
    reason_codes: list[str] = Field(default_factory=list)


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    qty: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    state: PositionState = PositionState.OPEN
    signal_id: str | None = None
    opened_as_of: date | None = None


class Trade(BaseModel):
    """Closed round-trip (placeholder fields for Phase 0)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    qty: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    entry_as_of: date | None = None
    exit_as_of: date | None = None
    signal_id: str | None = None
    r_multiple: float | None = None


class RiskDecision(BaseModel):
    """Output of the risk engine for a candidate signal/order plan."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    verdict: RiskVerdict
    reasons: list[str] = Field(default_factory=list)
    size: float | None = Field(default=None, ge=0)
    decided_at: datetime | None = None

    @model_validator(mode="after")
    def reject_has_no_positive_size(self) -> RiskDecision:
        if self.verdict == RiskVerdict.REJECT and self.size is not None and self.size > 0:
            msg = "rejected RiskDecision must not carry positive size"
            raise ValueError(msg)
        return self


# Allowed one-step transitions (Phase 0 table; full engine in Phase 1).
ALLOWED_SIGNAL_TRANSITIONS: dict[SignalState, frozenset[SignalState]] = {
    SignalState.DETECTED: frozenset(
        {SignalState.WATCHLISTED, SignalState.TRIGGERED, SignalState.EXPIRED}
    ),
    SignalState.WATCHLISTED: frozenset(
        {SignalState.TRIGGERED, SignalState.EXPIRED, SignalState.DETECTED}
    ),
    SignalState.TRIGGERED: frozenset(
        {SignalState.ELIGIBLE, SignalState.EXPIRED, SignalState.CANCELLED}
    ),
    SignalState.ELIGIBLE: frozenset(
        {
            SignalState.RISK_ACCEPTED,
            SignalState.RISK_REJECTED,
            SignalState.EXPIRED,
            SignalState.CANCELLED,
        }
    ),
    SignalState.RISK_ACCEPTED: frozenset({SignalState.ENTERED, SignalState.CANCELLED}),
    SignalState.RISK_REJECTED: frozenset({SignalState.EXPIRED}),
    SignalState.ENTERED: frozenset({SignalState.ACTIVE}),
    SignalState.ACTIVE: frozenset(
        {SignalState.EXITED, SignalState.STOPPED, SignalState.EXPIRED}
    ),
    SignalState.CANCELLED: frozenset(),
    SignalState.EXITED: frozenset(),
    SignalState.STOPPED: frozenset(),
    SignalState.EXPIRED: frozenset(),
}


def assert_signal_transition(from_state: SignalState, to_state: SignalState) -> None:
    """Raise StateTransitionError if ``from_state → to_state`` is not allowed."""
    allowed = ALLOWED_SIGNAL_TRANSITIONS.get(from_state, frozenset())
    if to_state not in allowed:
        raise StateTransitionError(
            f"illegal signal transition: {from_state.value} → {to_state.value}"
        )
