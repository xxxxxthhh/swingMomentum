"""Pure M6 next-open entry assessment.

This module determines whether an already accepted M5 plan may proceed to a
future Paper fill. It creates neither an order nor a fill, position, ledger
record, cash mutation, or lifecycle transition.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from smm.config.schema import ExecutionSection, StopSection
from smm.core.errors import DataValidationError
from smm.domain.enums import OrderSide, RiskVerdict
from smm.domain.models import RiskDecision
from smm.domain.views import TradeableBar
from smm.paper.costs import ExecutionQuote, quote_next_open

_ZERO = Decimal(0)


class EntryStatus(StrEnum):
    """Whether the M5 quantity can progress to the still-future fill seam."""

    FILLABLE = "fillable"
    CANCELLED = "cancelled"


class EntryAssessment(BaseModel):
    """Auditable actual-open gate for one accepted M5 risk plan.

    ``execution_quote`` is a price-and-cash quote, never evidence of a fill.
    A cancelled assessment always exposes zero executable quantity, so it
    cannot consume cash before the later order/fill and portfolio re-risk seams.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: str
    symbol: str
    signal_as_of: date
    as_of: date
    strategy_version: str
    config_hash: str
    status: EntryStatus
    reason_codes: tuple[str, ...] = Field(min_length=1)
    planned_quantity: int = Field(gt=0)
    executable_quantity: int = Field(ge=0)
    entry_reference: Decimal = Field(gt=_ZERO)
    stop_reference: Decimal = Field(gt=_ZERO)
    actual_open: Decimal = Field(gt=_ZERO)
    gap_atr: Decimal = Field(ge=_ZERO)
    stop_distance_atr: Decimal | None = None
    execution_quote: ExecutionQuote

    @field_validator("signal_id", "symbol", "strategy_version", "config_hash")
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("entry assessment identity fields must be non-empty")
        return value

    @field_validator("reason_codes")
    @classmethod
    def reason_codes_are_unique_and_nonempty(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(not code.strip() for code in value) or len(set(value)) != len(value):
            raise ValueError("entry assessment reason codes must be unique and non-empty")
        return value

    @model_validator(mode="after")
    def preserves_the_pure_entry_gate_contract(self) -> EntryAssessment:
        if self.as_of <= self.signal_as_of:
            raise ValueError("entry assessment as_of must be after signal_as_of")
        if self.stop_reference >= self.entry_reference:
            raise ValueError("entry assessment entry_reference must exceed stop_reference")
        if self.executable_quantity > self.planned_quantity:
            raise ValueError("entry assessment cannot increase the M5 planned quantity")
        if self.execution_quote.side is not OrderSide.BUY:
            raise ValueError("entry assessment execution quote must be a BUY quote")
        if (
            self.execution_quote.symbol != self.symbol
            or self.execution_quote.as_of != self.as_of
            or self.execution_quote.strategy_version != self.strategy_version
            or self.execution_quote.config_hash != self.config_hash
            or self.execution_quote.base_price != self.actual_open
        ):
            raise ValueError("entry assessment execution quote identity mismatch")
        if self.status is EntryStatus.FILLABLE:
            if self.executable_quantity != self.planned_quantity:
                raise ValueError("fillable entry assessment must retain M5 planned quantity")
            if self.stop_distance_atr is None or self.stop_distance_atr <= _ZERO:
                raise ValueError("fillable entry assessment requires positive stop distance")
        elif self.executable_quantity != 0:
            raise ValueError("cancelled entry assessment must have zero executable quantity")
        return self


def assess_next_open_entry(
    decision: RiskDecision,
    bar: TradeableBar,
    *,
    expected_session: date,
    atr_20: Decimal,
    execution: ExecutionSection,
    stop: StopSection,
) -> EntryAssessment:
    """Apply accepted M6 next-open entry cancellation guards.

    The gap is measured from the M5 signal-day entry reference using the actual
    true-print open.  The fill quote then uses frozen costs before testing the
    frozen ATR stop-distance band. This function never up-sizes: a fillable
    result retains exactly the M5 planned quantity; a cancellation exposes zero.
    Portfolio re-risk, order persistence, and fills belong to later seams.
    """
    _validate_decision(decision)
    if not isinstance(bar, TradeableBar):
        raise DataValidationError("next-open entry assessment requires TradeableBar")
    if not isinstance(expected_session, date):
        raise DataValidationError("expected provider session must be a date")
    if expected_session <= decision.as_of:
        raise DataValidationError("expected provider session must be after risk decision as_of")
    if bar.date != expected_session:
        raise DataValidationError("TradeableBar date does not match expected provider session")
    if bar.symbol != decision.symbol:
        raise DataValidationError("TradeableBar and RiskDecision symbol mismatch")

    atr = _positive_finite_decimal(atr_20, label="ATR20")
    max_gap_atr = _positive_finite_decimal(
        execution.max_open_gap_atr,
        label="execution.max_open_gap_atr",
    )
    min_stop_distance = _positive_finite_decimal(
        stop.min_stop_distance_atr,
        label="stop.min_stop_distance_atr",
    )
    max_stop_distance = _positive_finite_decimal(
        stop.max_stop_distance_atr,
        label="stop.max_stop_distance_atr",
    )
    if min_stop_distance > max_stop_distance:
        raise DataValidationError("stop.min_stop_distance_atr must be <= max_stop_distance_atr")

    quote = quote_next_open(
        bar,
        side=OrderSide.BUY,
        execution=execution,
        strategy_version=decision.strategy_version,
        config_hash=decision.config_hash,
    )
    actual_open = quote.base_price
    gap_atr = abs(actual_open - decision.entry_reference) / atr
    common = dict(
        signal_id=decision.signal_id,
        symbol=decision.symbol,
        signal_as_of=decision.as_of,
        as_of=bar.date,
        strategy_version=decision.strategy_version,
        config_hash=decision.config_hash,
        planned_quantity=decision.quantity,
        entry_reference=decision.entry_reference,
        stop_reference=decision.stop_reference,
        actual_open=actual_open,
        gap_atr=gap_atr,
        execution_quote=quote,
    )
    if gap_atr > max_gap_atr:
        return EntryAssessment(
            **common,
            status=EntryStatus.CANCELLED,
            reason_codes=("paper_entry_gap_exceeds_limit",),
            executable_quantity=0,
        )
    if actual_open <= decision.stop_reference:
        return EntryAssessment(
            **common,
            status=EntryStatus.CANCELLED,
            reason_codes=("paper_entry_open_at_or_below_stop",),
            executable_quantity=0,
        )

    stop_distance_atr = (quote.fill_price - decision.stop_reference) / atr
    if not min_stop_distance <= stop_distance_atr <= max_stop_distance:
        return EntryAssessment(
            **common,
            status=EntryStatus.CANCELLED,
            reason_codes=("paper_entry_stop_distance_out_of_bounds",),
            executable_quantity=0,
            stop_distance_atr=stop_distance_atr,
        )
    return EntryAssessment(
        **common,
        status=EntryStatus.FILLABLE,
        reason_codes=("paper_entry_ready",),
        executable_quantity=decision.quantity,
        stop_distance_atr=stop_distance_atr,
    )


def _validate_decision(decision: RiskDecision) -> None:
    if not isinstance(decision, RiskDecision):
        raise DataValidationError("next-open entry assessment requires RiskDecision")
    if decision.verdict is not RiskVerdict.ACCEPT:
        raise DataValidationError("next-open entry assessment requires RiskDecision ACCEPT")
    if decision.entry_reference <= decision.stop_reference:
        raise DataValidationError("RiskDecision entry_reference must exceed stop_reference")
    identity = (
        decision.signal_id,
        decision.symbol,
        decision.strategy_version,
        decision.config_hash,
    )
    if any(not value.strip() for value in identity):
        raise DataValidationError("RiskDecision identity fields must be non-empty")


def _positive_finite_decimal(value: object, *, label: str) -> Decimal:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DataValidationError(f"invalid {label}") from exc
    if not decimal.is_finite():
        raise DataValidationError(f"non-finite {label}")
    if decimal <= _ZERO:
        raise DataValidationError(f"{label} must be positive")
    return decimal
