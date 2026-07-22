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
    """Provider-native daily bar used by ingestion and feature computation.

    ``open/high/low/close`` retain the provider's primary series. For Yahoo
    that series is split-adjusted and is therefore **not** the historical print
    price required by fills and stops. ``adj_close`` is the total-return series;
    ``adj_factor`` derives the remaining adjusted prices — see
    :mod:`smm.domain.views`.

    Both adjusted fields are **required**. Defaulting ``adj_close`` to ``close``
    would silently substitute a favourable value for missing data, which ADR
    2026-07-22 §3.3 and constitution principle 11 forbid. Synthetic bars with no
    corporate action set ``adj_factor=1.0`` explicitly — that is a known value,
    not a missing one.

    Note: what a provider calls "unadjusted" varies. Yahoo's close is already
    split-adjusted (dividend-unadjusted); see the yfinance provider docstring.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    adj_close: float = Field(gt=0)
    adj_factor: float = Field(gt=0)

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

    @model_validator(mode="after")
    def adj_factor_consistency(self) -> Bar:
        """``close * adj_factor`` must reproduce ``adj_close`` (ADR §3.2)."""
        if abs(self.close * self.adj_factor - self.adj_close) > 1e-6 * self.adj_close:
            msg = (
                f"adj_factor inconsistent: close={self.close} * adj_factor="
                f"{self.adj_factor} != adj_close={self.adj_close}"
            )
            raise ValueError(msg)
        return self


class PrintBar(BaseModel):
    """OHLCV that actually traded in the stated session.

    This is deliberately a separate domain type rather than another view over
    :class:`Bar`. A provider-native split-adjusted bar must not become eligible
    for paper fills merely because it happens to expose the same field names.
    The MVP-B corporate-action adapter will be responsible for producing these
    rows from an independently verified split history.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def ohlc_consistency(self) -> PrintBar:
        if self.high < self.low:
            raise ValueError("high must be >= low")
        if self.high < max(self.open, self.close):
            raise ValueError("high must be >= open and close")
        if self.low > min(self.open, self.close):
            raise ValueError("low must be <= open and close")
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
