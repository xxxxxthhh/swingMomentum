"""Pure M6 close-condition exit scheduling.

This module evaluates only a position that survived the same session's stop
assessment.  It schedules a later true-print-open exit but never creates an
order, fill, trade, ledger mutation, or lifecycle transition.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from smm.config.schema import ExitSection
from smm.core.errors import DataValidationError
from smm.domain.views import AdjustedBar, TradeableBar
from smm.paper.stops import OpenPaperPosition, StopAssessmentStatus, StopExitAssessment

_ZERO = Decimal("0")
_EMA20_EXIT = "paper_exit_ema20_close_below"
_TIME_STOP_EXIT = "paper_exit_time_stop"
_HELD = "paper_exit_conditions_not_met"
_SCHEDULED_REASON_PRIORITY = (_EMA20_EXIT, _TIME_STOP_EXIT)


class CloseExitStatus(StrEnum):
    """Whether one evaluated session schedules a next-open paper exit."""

    HELD = "held"
    SCHEDULED = "scheduled"


class CloseExitAssessment(BaseModel):
    """Immutable, auditable close-condition result for one open position."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position_id: str
    symbol: str
    opened_as_of: date
    as_of: date
    scheduled_session: date | None = None
    strategy_version: str
    config_hash: str
    status: CloseExitStatus
    reason_codes: tuple[str, ...] = Field(min_length=1)
    adjusted_close: Decimal = Field(gt=_ZERO)
    ema_20: Decimal = Field(gt=_ZERO)
    tradeable_close: Decimal = Field(gt=_ZERO)
    completed_hold_sessions: int = Field(ge=1)
    mfe_r: Decimal

    @field_validator("position_id", "symbol", "strategy_version", "config_hash")
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("close exit identity fields must be non-empty")
        return value

    @field_validator("adjusted_close", "ema_20", "tradeable_close", "mfe_r")
    @classmethod
    def decimal_facts_are_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("close exit Decimal facts must be finite")
        return value

    @field_validator("reason_codes")
    @classmethod
    def reason_codes_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not code.strip() for code in value):
            raise ValueError("close exit reason codes must be unique and non-empty")
        return value

    @model_validator(mode="after")
    def preserves_close_exit_contract(self) -> CloseExitAssessment:
        if self.as_of < self.opened_as_of:
            raise ValueError("close exit as_of cannot precede position opening")
        if self.status is CloseExitStatus.HELD:
            if self.scheduled_session is not None:
                raise ValueError("held close exit must not schedule a session")
            if self.reason_codes != (_HELD,):
                raise ValueError("held close exit must carry the held reason")
            return self
        if self.scheduled_session is None or self.scheduled_session <= self.as_of:
            raise ValueError("scheduled close exit requires a later session")
        expected = tuple(
            code for code in _SCHEDULED_REASON_PRIORITY if code in self.reason_codes
        )
        if not expected or self.reason_codes != expected:
            raise ValueError("scheduled close exit requires recognized close-exit reasons")
        return self


def assess_close_exit(
    position: OpenPaperPosition,
    stop_assessment: StopExitAssessment,
    adjusted_bar: AdjustedBar,
    tradeable_bar: TradeableBar,
    *,
    expected_exit_session: date,
    ema_20: Decimal,
    completed_hold_sessions: int,
    mfe_r: Decimal,
    exit: ExitSection,
) -> CloseExitAssessment:
    """Schedule an M6 next-open exit from the accepted close conditions.

    Stop evaluation must already have held for this session.  EMA consumes only
    the adjusted feature view, while the time stop compares a true-print close
    with the actual entry fill.  The caller supplies a provider-derived next
    session and completed-session count; this seam does not infer calendars or
    mutate a position.
    """
    _validate_inputs(
        position=position,
        stop_assessment=stop_assessment,
        adjusted_bar=adjusted_bar,
        tradeable_bar=tradeable_bar,
        expected_exit_session=expected_exit_session,
        completed_hold_sessions=completed_hold_sessions,
        exit=exit,
    )
    adjusted_close = _positive_finite_decimal(
        adjusted_bar.adj_close,
        label="AdjustedBar.adj_close",
    )
    ema = _positive_finite_decimal(ema_20, label="EMA20")
    tradeable_close = _positive_finite_decimal(
        tradeable_bar.close,
        label="TradeableBar.close",
    )
    observed_mfe_r = _finite_decimal(mfe_r, label="MFE_R")
    time_stop_min_mfe_r = _non_negative_finite_decimal(
        exit.time_stop_min_mfe_r,
        label="exit.time_stop_min_mfe_r",
    )

    reason_codes = tuple(
        code
        for code, applies in (
            (_EMA20_EXIT, adjusted_close < ema),
            (
                _TIME_STOP_EXIT,
                completed_hold_sessions >= exit.time_stop_days
                and observed_mfe_r < time_stop_min_mfe_r
                and tradeable_close < position.entry_fill,
            ),
        )
        if applies
    )
    common = dict(
        position_id=position.position_id,
        symbol=position.symbol,
        opened_as_of=position.opened_as_of,
        as_of=tradeable_bar.date,
        strategy_version=position.strategy_version,
        config_hash=position.config_hash,
        adjusted_close=adjusted_close,
        ema_20=ema,
        tradeable_close=tradeable_close,
        completed_hold_sessions=completed_hold_sessions,
        mfe_r=observed_mfe_r,
    )
    if reason_codes:
        return CloseExitAssessment(
            **common,
            scheduled_session=expected_exit_session,
            status=CloseExitStatus.SCHEDULED,
            reason_codes=reason_codes,
        )
    return CloseExitAssessment(
        **common,
        status=CloseExitStatus.HELD,
        reason_codes=(_HELD,),
    )


def _validate_inputs(
    *,
    position: OpenPaperPosition,
    stop_assessment: StopExitAssessment,
    adjusted_bar: AdjustedBar,
    tradeable_bar: TradeableBar,
    expected_exit_session: date,
    completed_hold_sessions: int,
    exit: ExitSection,
) -> None:
    if not isinstance(position, OpenPaperPosition):
        raise DataValidationError("close exit assessment requires OpenPaperPosition")
    if not isinstance(stop_assessment, StopExitAssessment):
        raise DataValidationError("close exit assessment requires StopExitAssessment")
    if stop_assessment.status is not StopAssessmentStatus.HELD:
        raise DataValidationError("close exit assessment requires a held StopExitAssessment")
    if not isinstance(adjusted_bar, AdjustedBar):
        raise DataValidationError("close exit assessment requires AdjustedBar")
    if not isinstance(tradeable_bar, TradeableBar):
        raise DataValidationError("close exit assessment requires TradeableBar")
    if adjusted_bar.symbol != tradeable_bar.symbol:
        raise DataValidationError("AdjustedBar and TradeableBar symbol mismatch")
    if adjusted_bar.date != tradeable_bar.date:
        raise DataValidationError("AdjustedBar and TradeableBar session mismatch")
    if tradeable_bar.date < position.opened_as_of:
        raise DataValidationError("close exit session precedes position opening")
    if tradeable_bar.symbol != position.symbol:
        raise DataValidationError("TradeableBar and OpenPaperPosition symbol mismatch")
    if (
        stop_assessment.position_id != position.position_id
        or stop_assessment.symbol != position.symbol
        or stop_assessment.opened_as_of != position.opened_as_of
        or stop_assessment.as_of != tradeable_bar.date
        or stop_assessment.strategy_version != position.strategy_version
        or stop_assessment.config_hash != position.config_hash
        or stop_assessment.quantity != position.quantity
        or stop_assessment.initial_stop != position.initial_stop
    ):
        raise DataValidationError("StopExitAssessment identity or position facts mismatch")
    if not isinstance(expected_exit_session, date):
        raise DataValidationError("expected exit session must be a date")
    if expected_exit_session <= tradeable_bar.date:
        raise DataValidationError("expected exit session must follow close session")
    if (
        isinstance(completed_hold_sessions, bool)
        or not isinstance(completed_hold_sessions, int)
        or completed_hold_sessions < 1
    ):
        raise DataValidationError("completed hold sessions must be a positive integer")
    if not isinstance(exit, ExitSection):
        raise DataValidationError("close exit assessment requires ExitSection")
    if exit.fixed_profit_target:
        raise DataValidationError("M6 close exit does not support fixed_profit_target")
    if exit.trailing_exit != "close_below_ema_20":
        raise DataValidationError("unsupported frozen exit.trailing_exit")


def _positive_finite_decimal(value: object, *, label: str) -> Decimal:
    decimal = _finite_decimal(value, label=label)
    if decimal <= _ZERO:
        raise DataValidationError(f"{label} must be positive")
    return decimal


def _non_negative_finite_decimal(value: object, *, label: str) -> Decimal:
    decimal = _finite_decimal(value, label=label)
    if decimal < _ZERO:
        raise DataValidationError(f"{label} must be non-negative")
    return decimal


def _finite_decimal(value: object, *, label: str) -> Decimal:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DataValidationError(f"invalid {label}") from exc
    if not decimal.is_finite():
        raise DataValidationError(f"non-finite {label}")
    return decimal
