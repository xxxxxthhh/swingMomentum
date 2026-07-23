"""Pure M6 true-print position-excursion updates.

The caller supplies an already corporate-action-rebased open position. This
module advances only its MFE/MAE facts from verified true-print bars; it does
not move stops, create fills, persist state, or schedule an exit.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from smm.core.errors import DataValidationError
from smm.domain.views import TradeableBar
from smm.paper.stops import OpenPaperPosition

_ZERO = Decimal("0")


class PositionExcursionState(BaseModel):
    """Immutable M6 MFE/MAE facts for one position through one session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position_id: str
    symbol: str
    opened_as_of: date
    as_of: date
    strategy_version: str
    config_hash: str
    entry_fill: Decimal = Field(gt=_ZERO)
    initial_unit_risk: Decimal = Field(gt=_ZERO)
    max_tradeable_high: Decimal = Field(gt=_ZERO)
    min_tradeable_low: Decimal = Field(gt=_ZERO)
    mfe_r: Decimal
    mae_r: Decimal

    @field_validator("position_id", "symbol", "strategy_version", "config_hash")
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("position excursion identity fields must be non-empty")
        return value

    @field_validator(
        "entry_fill",
        "initial_unit_risk",
        "max_tradeable_high",
        "min_tradeable_low",
        "mfe_r",
        "mae_r",
    )
    @classmethod
    def decimal_facts_are_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("position excursion Decimal facts must be finite")
        return value

    @model_validator(mode="after")
    def preserves_excursion_contract(self) -> PositionExcursionState:
        if self.as_of < self.opened_as_of:
            raise ValueError("position excursion as_of cannot precede position opening")
        if self.max_tradeable_high < self.min_tradeable_low:
            raise ValueError("position excursion high must be >= low")
        expected_mfe_r = (
            self.max_tradeable_high - self.entry_fill
        ) / self.initial_unit_risk
        expected_mae_r = (
            self.min_tradeable_low - self.entry_fill
        ) / self.initial_unit_risk
        if self.mfe_r != expected_mfe_r:
            raise ValueError("position excursion mfe_r must match true-print facts")
        if self.mae_r != expected_mae_r:
            raise ValueError("position excursion mae_r must match true-print facts")
        return self


def update_position_excursion(
    position: OpenPaperPosition,
    bar: TradeableBar,
    *,
    prior_state: PositionExcursionState | None,
) -> PositionExcursionState:
    """Advance a position's MFE/MAE from one true-print daily bar.

    On the entry session, no prior excursion state is allowed. Every later
    session requires an earlier, identity-matching state so missing history
    cannot silently reset MFE/MAE. Corporate-action rebase belongs to its own
    seam and must have already normalized both the position and prior state.
    """
    if not isinstance(position, OpenPaperPosition):
        raise DataValidationError("position excursion requires OpenPaperPosition")
    if not isinstance(bar, TradeableBar):
        raise DataValidationError("position excursion requires TradeableBar")
    if bar.symbol != position.symbol:
        raise DataValidationError("TradeableBar and OpenPaperPosition symbol mismatch")
    if bar.date < position.opened_as_of:
        raise DataValidationError("TradeableBar session precedes position opening")

    entry_fill = _positive_finite_decimal(
        position.entry_fill,
        label="OpenPaperPosition.entry_fill",
    )
    initial_unit_risk = _positive_finite_decimal(
        position.initial_unit_risk,
        label="OpenPaperPosition.initial_unit_risk",
    )
    current_high = _positive_finite_decimal(
        bar.high,
        label="TradeableBar.high",
    )
    current_low = _positive_finite_decimal(
        bar.low,
        label="TradeableBar.low",
    )
    if current_high < current_low:
        raise DataValidationError("TradeableBar.high must be >= TradeableBar.low")

    if bar.date == position.opened_as_of:
        if prior_state is not None:
            raise DataValidationError("entry-session excursion must not have prior state")
        max_tradeable_high = current_high
        min_tradeable_low = current_low
    else:
        if prior_state is None:
            raise DataValidationError("later-session excursion requires prior excursion state")
        _validate_prior_state(
            prior_state,
            position=position,
            entry_fill=entry_fill,
            initial_unit_risk=initial_unit_risk,
            current_session=bar.date,
        )
        max_tradeable_high = max(prior_state.max_tradeable_high, current_high)
        min_tradeable_low = min(prior_state.min_tradeable_low, current_low)

    return PositionExcursionState(
        position_id=position.position_id,
        symbol=position.symbol,
        opened_as_of=position.opened_as_of,
        as_of=bar.date,
        strategy_version=position.strategy_version,
        config_hash=position.config_hash,
        entry_fill=entry_fill,
        initial_unit_risk=initial_unit_risk,
        max_tradeable_high=max_tradeable_high,
        min_tradeable_low=min_tradeable_low,
        mfe_r=(max_tradeable_high - entry_fill) / initial_unit_risk,
        mae_r=(min_tradeable_low - entry_fill) / initial_unit_risk,
    )


def _validate_prior_state(
    prior_state: PositionExcursionState,
    *,
    position: OpenPaperPosition,
    entry_fill: Decimal,
    initial_unit_risk: Decimal,
    current_session: date,
) -> None:
    if not isinstance(prior_state, PositionExcursionState):
        raise DataValidationError("prior excursion state must be PositionExcursionState")
    if prior_state.as_of >= current_session:
        raise DataValidationError("prior excursion state must precede current session")
    if (
        prior_state.position_id != position.position_id
        or prior_state.symbol != position.symbol
        or prior_state.opened_as_of != position.opened_as_of
        or prior_state.strategy_version != position.strategy_version
        or prior_state.config_hash != position.config_hash
        or prior_state.entry_fill != entry_fill
        or prior_state.initial_unit_risk != initial_unit_risk
    ):
        raise DataValidationError("prior excursion state identity or position facts mismatch")


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
