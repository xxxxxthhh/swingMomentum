"""Pure M6 next-open execution-cost quotes.

This module turns an already-verified :class:`~smm.domain.views.TradeableBar`
open into a Decimal-only cost quote.  It deliberately does not create orders,
fills, positions, ledger records, or any task orchestration.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator

from smm.config.schema import ExecutionSection
from smm.core.errors import DataValidationError
from smm.domain.enums import OrderSide
from smm.domain.views import TradeableBar

_ZERO = Decimal(0)
_ONE = Decimal(1)
_BPS_DENOMINATOR = Decimal(10_000)


class ExecutionQuote(BaseModel):
    """Auditable per-share next-open quote before a Paper fill is created."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    as_of: date
    strategy_version: str
    config_hash: str
    side: OrderSide
    base_price: Decimal = Field(gt=_ZERO)
    half_spread_bps: Decimal = Field(gt=_ZERO)
    slippage_bps: Decimal = Field(gt=_ZERO)
    fill_price: Decimal = Field(gt=_ZERO)
    commission_per_share: Decimal = Field(ge=_ZERO)
    cash_per_share: Decimal = Field(gt=_ZERO)

    @field_validator("symbol", "strategy_version", "config_hash")
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity fields must be non-empty")
        return value


def quote_next_open(
    bar: TradeableBar,
    *,
    side: OrderSide,
    execution: ExecutionSection,
    strategy_version: str,
    config_hash: str,
) -> ExecutionQuote:
    """Quote a next-session-open fill using the frozen M6 cost contract.

    The only price source is ``TradeableBar.open``.  Buy quotes debit
    ``fill_price + commission``; sell quotes credit ``fill_price - commission``.
    Missing or malformed M6 parameters fail closed rather than silently
    assuming zero costs.
    """
    if not isinstance(bar, TradeableBar):
        raise DataValidationError("next-open execution quote requires TradeableBar")
    if not isinstance(side, OrderSide):
        raise DataValidationError("next-open execution quote requires an OrderSide")
    if not strategy_version.strip() or not config_hash.strip():
        raise DataValidationError("execution quote identity fields must be non-empty")
    if not bar.symbol.strip():
        raise DataValidationError("TradeableBar symbol must be non-empty")

    base_price = _positive_finite_decimal(bar.open, label="TradeableBar.open")
    half_spread = _required_cost(execution.half_spread_bps, label="half_spread_bps")
    commission = _required_cost(
        execution.commission_per_share,
        label="commission_per_share",
        allow_zero=True,
    )
    entry_slippage = _required_cost(
        execution.entry_slippage_bps,
        label="entry_slippage_bps",
    )
    exit_slippage = _required_cost(
        execution.exit_slippage_bps,
        label="exit_slippage_bps",
    )
    if side is OrderSide.BUY:
        slippage = entry_slippage
        fill_price = base_price * (_ONE + (half_spread + slippage) / _BPS_DENOMINATOR)
        cash_per_share = fill_price + commission
    else:
        slippage = exit_slippage
        fill_price = base_price * (_ONE - (half_spread + slippage) / _BPS_DENOMINATOR)
        cash_per_share = fill_price - commission

    if fill_price <= _ZERO:
        raise DataValidationError("execution quote has non-positive fill price")
    if cash_per_share <= _ZERO:
        raise DataValidationError("execution quote has non-positive net cash per share")

    return ExecutionQuote(
        symbol=bar.symbol,
        as_of=bar.date,
        strategy_version=strategy_version,
        config_hash=config_hash,
        side=side,
        base_price=base_price,
        half_spread_bps=half_spread,
        slippage_bps=slippage,
        fill_price=fill_price,
        commission_per_share=commission,
        cash_per_share=cash_per_share,
    )


def _positive_finite_decimal(value: object, *, label: str) -> Decimal:
    decimal = _finite_decimal(value, label=label)
    if decimal <= _ZERO:
        raise DataValidationError(f"{label} must be positive")
    return decimal


def _required_cost(value: object, *, label: str, allow_zero: bool = False) -> Decimal:
    if value is None:
        raise DataValidationError(f"missing frozen M6 execution cost: {label}")
    decimal = _finite_decimal(value, label=f"M6 execution cost {label}")
    if decimal < _ZERO or (decimal == _ZERO and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise DataValidationError(f"M6 execution cost {label} must be {qualifier}")
    return decimal


def _finite_decimal(value: object, *, label: str) -> Decimal:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DataValidationError(f"invalid {label}") from exc
    if not decimal.is_finite():
        raise DataValidationError(f"non-finite {label}")
    return decimal
