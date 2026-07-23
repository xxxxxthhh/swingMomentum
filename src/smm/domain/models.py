"""Core domain models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from smm.core.errors import StateTransitionError
from smm.domain.enums import (
    MarketRegime,
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


class EligibleCandidate(BaseModel):
    """Validated M5 input; no market-bar type can cross this seam."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: str
    symbol: str
    as_of: date
    strategy_version: str
    config_hash: str
    regime: MarketRegime
    sector: str
    risk_cluster: str = "unclassified"
    entry_reference: Decimal = Field(gt=0)
    stop_reference: Decimal = Field(gt=0)
    estimated_entry_cost_per_share: Decimal = Field(gt=0)
    estimated_total_cost_per_share: Decimal = Field(gt=0)
    momentum_score: float | None = Field(default=None, ge=0, le=100)
    relative_strength_score: float | None = Field(default=None, ge=0, le=100)

    @field_validator(
        "signal_id", "symbol", "strategy_version", "config_hash", "sector"
    )
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity and sector fields must be non-empty")
        return value

    @field_validator("risk_cluster", mode="before")
    @classmethod
    def normalize_missing_cluster(cls, value: object) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "unclassified"
        if not isinstance(value, str):
            raise ValueError("risk_cluster must be a string or missing")
        return value

    @model_validator(mode="after")
    def validate_price_and_cost_relationships(self) -> EligibleCandidate:
        if self.entry_reference <= self.stop_reference:
            raise ValueError("entry_reference must be greater than stop_reference")
        if self.estimated_total_cost_per_share < self.estimated_entry_cost_per_share:
            raise ValueError(
                "estimated_total_cost_per_share must be >= estimated_entry_cost_per_share"
            )
        return self

    @property
    def unit_risk(self) -> Decimal:
        return (
            self.entry_reference
            - self.stop_reference
            + self.estimated_total_cost_per_share
        )

    @property
    def capital_per_share(self) -> Decimal:
        return self.entry_reference + self.estimated_entry_cost_per_share


class PortfolioSnapshot(BaseModel):
    """Fail-closed money snapshot consumed by the pure M5 risk engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: date
    account_equity: Decimal = Field(gt=0)
    available_cash: Decimal = Field(ge=0)
    gross_exposure_capital: Decimal = Field(ge=0)
    portfolio_initial_risk: Decimal = Field(ge=0)
    sector_initial_risk: dict[str, Decimal]
    cluster_initial_risk: dict[str, Decimal]
    open_symbols: frozenset[str]
    reserved_signal_ids: frozenset[str]
    strategy_version: str
    config_hash: str

    @field_validator("strategy_version", "config_hash")
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("snapshot identity fields must be non-empty")
        return value

    @field_validator("sector_initial_risk", "cluster_initial_risk")
    @classmethod
    def risk_maps_are_nonnegative(cls, values: dict[str, Decimal]) -> dict[str, Decimal]:
        if any(not key.strip() for key in values):
            raise ValueError("risk map keys must be non-empty")
        if any(value < 0 for value in values.values()):
            raise ValueError("risk map values must be non-negative")
        return values

    @field_validator("open_symbols", "reserved_signal_ids")
    @classmethod
    def identity_sets_are_nonempty(cls, values: frozenset[str]) -> frozenset[str]:
        if any(not value.strip() for value in values):
            raise ValueError("snapshot identity sets cannot contain empty values")
        return values

    @model_validator(mode="after")
    def validate_money_and_reconciliation(self) -> PortfolioSnapshot:
        if self.available_cash > self.account_equity:
            raise ValueError("available_cash cannot exceed account_equity")
        if self.gross_exposure_capital > self.account_equity:
            raise ValueError("gross_exposure_capital cannot exceed account_equity")
        if self.portfolio_initial_risk > self.account_equity:
            raise ValueError("portfolio_initial_risk cannot exceed account_equity")
        if sum(self.sector_initial_risk.values(), Decimal(0)) != self.portfolio_initial_risk:
            raise ValueError("sector_initial_risk does not reconcile to portfolio_initial_risk")
        if sum(self.cluster_initial_risk.values(), Decimal(0)) != self.portfolio_initial_risk:
            raise ValueError("cluster_initial_risk does not reconcile to portfolio_initial_risk")
        return self


class RiskDecision(BaseModel):
    """Deterministic M5 plan decision; never an order or fill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: str
    symbol: str
    as_of: date
    strategy_version: str
    config_hash: str
    verdict: RiskVerdict
    reason_codes: tuple[str, ...] = Field(min_length=1)
    quantity: int = Field(ge=0)
    entry_reference: Decimal = Field(gt=0)
    stop_reference: Decimal = Field(gt=0)
    unit_risk: Decimal = Field(gt=0)
    planned_capital: Decimal = Field(ge=0)
    planned_initial_risk: Decimal = Field(ge=0)
    sector: str
    risk_cluster: str
    regime: MarketRegime

    @model_validator(mode="after")
    def verdict_matches_planned_size(self) -> RiskDecision:
        if any(not code.strip() for code in self.reason_codes):
            raise ValueError("reason_codes must be non-empty strings")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason_codes must be unique")
        if self.verdict == RiskVerdict.ACCEPT:
            if self.quantity < 1 or self.planned_capital <= 0 or self.planned_initial_risk <= 0:
                raise ValueError("accepted RiskDecision must carry a positive plan")
        elif self.quantity != 0 or self.planned_capital != 0 or self.planned_initial_risk != 0:
            raise ValueError("rejected RiskDecision must not carry positive size")
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
        {
            SignalState.ELIGIBLE,
            SignalState.RISK_ACCEPTED,
            SignalState.RISK_REJECTED,
            SignalState.EXPIRED,
            SignalState.CANCELLED,
        }
    ),
    SignalState.ELIGIBLE: frozenset(
        {
            SignalState.RISK_ACCEPTED,
            SignalState.RISK_REJECTED,
            SignalState.EXPIRED,
            SignalState.CANCELLED,
        }
    ),
    SignalState.RISK_ACCEPTED: frozenset(
        {SignalState.ENTERED, SignalState.STOPPED, SignalState.CANCELLED}
    ),
    SignalState.RISK_REJECTED: frozenset({SignalState.EXPIRED}),
    SignalState.ENTERED: frozenset(
        {SignalState.ACTIVE, SignalState.EXITED, SignalState.STOPPED}
    ),
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
