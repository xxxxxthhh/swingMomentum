"""Pure M6 true-print long-stop assessment.

The module determines a stop outcome and exit quote for an open paper position.
It does not create an order, fill, trade, cash mutation, or lifecycle event.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from smm.config.schema import ExecutionSection
from smm.core.errors import DataValidationError
from smm.domain.enums import OrderSide
from smm.domain.views import TradeableBar
from smm.paper.costs import ExecutionQuote, _quote_for_base_price

_ZERO = Decimal(0)


class StopAssessmentStatus(StrEnum):
    """Daily result of checking a long position's fixed initial stop."""

    HELD = "held"
    STOPPED = "stopped"


class OpenPaperPosition(BaseModel):
    """Minimum immutable position facts required for the pure stop seam."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position_id: str
    symbol: str
    opened_as_of: date
    strategy_version: str
    config_hash: str
    quantity: int = Field(gt=0)
    entry_fill: Decimal = Field(gt=_ZERO)
    initial_stop: Decimal = Field(gt=_ZERO)
    initial_unit_risk: Decimal = Field(gt=_ZERO)

    @field_validator("position_id", "symbol", "strategy_version", "config_hash")
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("open paper position identity fields must be non-empty")
        return value

    @model_validator(mode="after")
    def initial_stop_is_not_at_or_above_entry(self) -> OpenPaperPosition:
        if self.entry_fill <= self.initial_stop:
            raise ValueError("open paper position entry_fill must exceed initial_stop")
        return self


class StopExitAssessment(BaseModel):
    """Auditable stop outcome; an exit quote remains distinct from a fill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position_id: str
    symbol: str
    opened_as_of: date
    as_of: date
    strategy_version: str
    config_hash: str
    quantity: int = Field(gt=0)
    initial_stop: Decimal = Field(gt=_ZERO)
    status: StopAssessmentStatus
    reason_codes: tuple[str, ...] = Field(min_length=1)
    base_exit_price: Decimal | None = None
    execution_quote: ExecutionQuote | None = None

    @field_validator("position_id", "symbol", "strategy_version", "config_hash")
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("stop assessment identity fields must be non-empty")
        return value

    @field_validator("reason_codes")
    @classmethod
    def reason_codes_are_unique_and_nonempty(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(not code.strip() for code in value) or len(set(value)) != len(value):
            raise ValueError("stop assessment reason codes must be unique and non-empty")
        return value

    @model_validator(mode="after")
    def preserves_stop_assessment_contract(self) -> StopExitAssessment:
        if self.as_of < self.opened_as_of:
            raise ValueError("stop assessment as_of cannot precede position opening")
        if self.status is StopAssessmentStatus.HELD:
            if self.base_exit_price is not None or self.execution_quote is not None:
                raise ValueError("held stop assessment must not carry exit price or quote")
            if self.reason_codes != ("paper_stop_not_triggered",):
                raise ValueError("held stop assessment must carry the held reason")
            return self
        if self.base_exit_price is None or self.execution_quote is None:
            raise ValueError("stopped stop assessment requires exit price and quote")
        if self.reason_codes not in (
            ("paper_stop_gap_open",),
            ("paper_stop_triggered",),
        ):
            raise ValueError("stopped stop assessment must carry a recognized stop reason")
        if self.execution_quote.side is not OrderSide.SELL:
            raise ValueError("stop assessment execution quote must be a SELL quote")
        if (
            self.execution_quote.symbol != self.symbol
            or self.execution_quote.as_of != self.as_of
            or self.execution_quote.strategy_version != self.strategy_version
            or self.execution_quote.config_hash != self.config_hash
            or self.execution_quote.base_price != self.base_exit_price
        ):
            raise ValueError("stop assessment execution quote identity mismatch")
        return self


def assess_long_stop(
    position: OpenPaperPosition,
    bar: TradeableBar,
    *,
    execution: ExecutionSection,
) -> StopExitAssessment:
    """Apply the accepted M6 stop ordering to one open long position.

    ``open <= initial_stop`` is gap-through-stop and quotes the true-print open.
    Only when the open is above the stop does ``low <= initial_stop`` quote the
    stop itself. No branch moves the stop or creates a fill.
    """
    if not isinstance(position, OpenPaperPosition):
        raise DataValidationError("long stop assessment requires OpenPaperPosition")
    if not isinstance(bar, TradeableBar):
        raise DataValidationError("long stop assessment requires TradeableBar")
    if bar.date < position.opened_as_of:
        raise DataValidationError("TradeableBar session precedes position opening")
    if bar.symbol != position.symbol:
        raise DataValidationError("TradeableBar and OpenPaperPosition symbol mismatch")

    actual_open = _positive_finite_decimal(bar.open, label="TradeableBar.open")
    actual_low = _positive_finite_decimal(bar.low, label="TradeableBar.low")
    common = dict(
        position_id=position.position_id,
        symbol=position.symbol,
        opened_as_of=position.opened_as_of,
        as_of=bar.date,
        strategy_version=position.strategy_version,
        config_hash=position.config_hash,
        quantity=position.quantity,
        initial_stop=position.initial_stop,
    )
    if actual_open <= position.initial_stop:
        return _stopped(
            common,
            bar=bar,
            base_exit_price=actual_open,
            reason_code="paper_stop_gap_open",
            execution=execution,
        )
    if actual_low <= position.initial_stop:
        return _stopped(
            common,
            bar=bar,
            base_exit_price=position.initial_stop,
            reason_code="paper_stop_triggered",
            execution=execution,
        )
    return StopExitAssessment(
        **common,
        status=StopAssessmentStatus.HELD,
        reason_codes=("paper_stop_not_triggered",),
    )


def _stopped(
    values: dict[str, object],
    *,
    bar: TradeableBar,
    base_exit_price: Decimal,
    reason_code: str,
    execution: ExecutionSection,
) -> StopExitAssessment:
    quote = _quote_for_base_price(
        bar,
        base_price=base_exit_price,
        base_price_label="initial stop or TradeableBar.open",
        side=OrderSide.SELL,
        execution=execution,
        strategy_version=str(values["strategy_version"]),
        config_hash=str(values["config_hash"]),
    )
    return StopExitAssessment(
        **values,
        status=StopAssessmentStatus.STOPPED,
        reason_codes=(reason_code,),
        base_exit_price=base_exit_price,
        execution_quote=quote,
    )


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
