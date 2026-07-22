"""Pure M6 circuit-state assessment.

This module derives an auditable operational state from already-computed equity,
realized-R, and integrity facts. It does not edit frozen config, mutate a risk
decision, write a ledger, or orchestrate a daily task.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from smm.config.schema import RiskSection
from smm.core.errors import DataValidationError

_ZERO = Decimal(0)
_ONE = Decimal(1)
_HALF = Decimal("0.5")

_INTEGRITY_HALT = "circuit_data_or_position_integrity_halt"
_DRAWDOWN_STOP = "circuit_drawdown_stop_new_entries"
_DAILY_LOSS_PAUSE = "circuit_daily_loss_pause"
_DRAWDOWN_REDUCE = "circuit_drawdown_reduce_risk"
_REASON_PRIORITY = (
    _INTEGRITY_HALT,
    _DRAWDOWN_STOP,
    _DAILY_LOSS_PAUSE,
    _DRAWDOWN_REDUCE,
)


class CircuitInputs(BaseModel):
    """Immutable facts needed to derive one M6 operational circuit state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: date
    strategy_version: str
    config_hash: str
    realized_loss_r_for_session: Decimal
    marked_equity: Decimal = Field(ge=_ZERO)
    prior_high_water_equity: Decimal = Field(gt=_ZERO)
    integrity_halt: StrictBool

    @field_validator("strategy_version", "config_hash")
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("circuit input identity fields must be non-empty")
        return value

    @field_validator(
        "realized_loss_r_for_session",
        "marked_equity",
        "prior_high_water_equity",
    )
    @classmethod
    def decimal_facts_are_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("circuit input Decimal facts must be finite")
        return value


class CircuitState(BaseModel):
    """Replayable M6 state that affects only later new-entry decisions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: date
    strategy_version: str
    config_hash: str
    realized_loss_r_for_session: Decimal
    marked_equity: Decimal = Field(ge=_ZERO)
    high_water_equity: Decimal = Field(gt=_ZERO)
    drawdown: Decimal = Field(ge=_ZERO, le=_ONE)
    new_entries_blocked: StrictBool
    entry_risk_multiplier: Decimal = Field(ge=_ZERO, le=_ONE)
    reason_codes: tuple[str, ...] = ()

    @field_validator("strategy_version", "config_hash")
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("circuit state identity fields must be non-empty")
        return value

    @field_validator(
        "realized_loss_r_for_session",
        "marked_equity",
        "high_water_equity",
        "drawdown",
        "entry_risk_multiplier",
    )
    @classmethod
    def decimal_facts_are_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("circuit state Decimal facts must be finite")
        return value

    @field_validator("reason_codes")
    @classmethod
    def reason_codes_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("circuit reason codes must be unique")
        if any(code not in _REASON_PRIORITY for code in value):
            raise ValueError("circuit reason codes must be recognized")
        expected = tuple(code for code in _REASON_PRIORITY if code in value)
        if value != expected:
            raise ValueError("circuit reason codes must use stable priority order")
        if _DRAWDOWN_STOP in value and _DRAWDOWN_REDUCE in value:
            raise ValueError("drawdown stop and reduction cannot both apply")
        return value

    @model_validator(mode="after")
    def preserves_circuit_contract(self) -> CircuitState:
        if self.marked_equity > self.high_water_equity:
            raise ValueError("circuit high water must not trail marked equity")
        expected_drawdown = (
            self.high_water_equity - self.marked_equity
        ) / self.high_water_equity
        if self.drawdown != expected_drawdown:
            raise ValueError("circuit drawdown must match equity facts")

        blocking_reasons = {
            _INTEGRITY_HALT,
            _DRAWDOWN_STOP,
            _DAILY_LOSS_PAUSE,
        }
        if self.new_entries_blocked != bool(blocking_reasons & set(self.reason_codes)):
            raise ValueError("circuit entry block must match circuit reasons")
        if _INTEGRITY_HALT in self.reason_codes or _DRAWDOWN_STOP in self.reason_codes:
            if self.entry_risk_multiplier != _ZERO:
                raise ValueError("integrity halt or drawdown stop must zero entry risk")
        elif _DRAWDOWN_REDUCE in self.reason_codes:
            if self.entry_risk_multiplier != _HALF:
                raise ValueError("drawdown reduction must halve entry risk")
        elif self.entry_risk_multiplier != _ONE:
            raise ValueError("normal or daily-loss-only circuit must retain entry risk")
        return self


def evaluate_circuit_state(
    inputs: CircuitInputs,
    *,
    risk: RiskSection,
) -> CircuitState:
    """Derive the accepted M6 circuit state without mutating frozen config.

    The caller supplies end-of-session realized loss and marked-equity facts.
    A resulting daily-loss pause is consumed by the later next-session entry
    orchestration seam; this function neither decides quantity nor writes state.
    """
    if not isinstance(inputs, CircuitInputs):
        raise DataValidationError("circuit assessment requires CircuitInputs")
    if not isinstance(risk, RiskSection):
        raise DataValidationError("circuit assessment requires RiskSection")

    daily_loss_pause = _required_threshold(
        risk.daily_loss_pause_r,
        label="risk.daily_loss_pause_r",
    )
    drawdown_reduce = _required_threshold(
        risk.drawdown_reduce_at,
        label="risk.drawdown_reduce_at",
    )
    drawdown_stop = _required_threshold(
        risk.drawdown_stop_at,
        label="risk.drawdown_stop_at",
    )
    if drawdown_reduce >= _ONE or drawdown_stop >= _ONE:
        raise DataValidationError("drawdown circuit thresholds must be < 1")
    if drawdown_reduce >= drawdown_stop:
        raise DataValidationError(
            "risk.drawdown_reduce_at must be < risk.drawdown_stop_at"
        )

    high_water_equity = max(inputs.prior_high_water_equity, inputs.marked_equity)
    drawdown = (high_water_equity - inputs.marked_equity) / high_water_equity
    drawdown_stop_applies = drawdown >= drawdown_stop
    drawdown_reduce_applies = (
        drawdown >= drawdown_reduce and not drawdown_stop_applies
    )
    daily_loss_applies = inputs.realized_loss_r_for_session < -daily_loss_pause

    reason_codes = tuple(
        code
        for code, applies in (
            (_INTEGRITY_HALT, inputs.integrity_halt),
            (_DRAWDOWN_STOP, drawdown_stop_applies),
            (_DAILY_LOSS_PAUSE, daily_loss_applies),
            (_DRAWDOWN_REDUCE, drawdown_reduce_applies),
        )
        if applies
    )
    new_entries_blocked = any(
        code in reason_codes
        for code in (_INTEGRITY_HALT, _DRAWDOWN_STOP, _DAILY_LOSS_PAUSE)
    )
    if _INTEGRITY_HALT in reason_codes or _DRAWDOWN_STOP in reason_codes:
        entry_risk_multiplier = _ZERO
    elif _DRAWDOWN_REDUCE in reason_codes:
        entry_risk_multiplier = _HALF
    else:
        entry_risk_multiplier = _ONE

    return CircuitState(
        as_of=inputs.as_of,
        strategy_version=inputs.strategy_version,
        config_hash=inputs.config_hash,
        realized_loss_r_for_session=inputs.realized_loss_r_for_session,
        marked_equity=inputs.marked_equity,
        high_water_equity=high_water_equity,
        drawdown=drawdown,
        new_entries_blocked=new_entries_blocked,
        entry_risk_multiplier=entry_risk_multiplier,
        reason_codes=reason_codes,
    )


def _required_threshold(value: object, *, label: str) -> Decimal:
    if value is None:
        raise DataValidationError(f"missing frozen M6 circuit threshold: {label}")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DataValidationError(f"invalid M6 circuit threshold: {label}") from exc
    if not decimal.is_finite():
        raise DataValidationError(f"non-finite M6 circuit threshold: {label}")
    if decimal <= _ZERO:
        raise DataValidationError(f"M6 circuit threshold must be positive: {label}")
    return decimal
