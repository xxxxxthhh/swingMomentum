"""Pure M6 split rebase facts for open paper positions.

This module converts pre-action position and excursion facts into the action
date's share unit and returns an append-ready corporate-action record. It does
not persist the record, create an order/fill/trade, or schedule work.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from smm.core.errors import DataValidationError
from smm.paper.excursions import PositionExcursionState
from smm.paper.prints import SplitAction
from smm.paper.stops import OpenPaperPosition

_ZERO = Decimal("0")


class PaperPositionCorporateAction(BaseModel):
    """Append-ready audit fact for one open-position split rebase."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position_id: str
    symbol: str
    action_id: str
    action_date: date
    split_ratio: Decimal = Field(gt=_ZERO)
    strategy_version: str
    config_hash: str
    pre_quantity: int = Field(gt=0)
    post_quantity: int = Field(gt=0)
    pre_entry_fill: Decimal = Field(gt=_ZERO)
    post_entry_fill: Decimal = Field(gt=_ZERO)
    pre_initial_stop: Decimal = Field(gt=_ZERO)
    post_initial_stop: Decimal = Field(gt=_ZERO)
    pre_initial_unit_risk: Decimal = Field(gt=_ZERO)
    post_initial_unit_risk: Decimal = Field(gt=_ZERO)
    pre_max_tradeable_high: Decimal = Field(gt=_ZERO)
    post_max_tradeable_high: Decimal = Field(gt=_ZERO)
    pre_min_tradeable_low: Decimal = Field(gt=_ZERO)
    post_min_tradeable_low: Decimal = Field(gt=_ZERO)
    pre_mfe_r: Decimal
    post_mfe_r: Decimal
    pre_mae_r: Decimal
    post_mae_r: Decimal

    @field_validator(
        "position_id",
        "symbol",
        "action_id",
        "strategy_version",
        "config_hash",
    )
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("paper position corporate-action identity fields must be non-empty")
        return value

    @field_validator(
        "split_ratio",
        "pre_entry_fill",
        "post_entry_fill",
        "pre_initial_stop",
        "post_initial_stop",
        "pre_initial_unit_risk",
        "post_initial_unit_risk",
        "pre_max_tradeable_high",
        "post_max_tradeable_high",
        "pre_min_tradeable_low",
        "post_min_tradeable_low",
        "pre_mfe_r",
        "post_mfe_r",
        "pre_mae_r",
        "post_mae_r",
    )
    @classmethod
    def decimal_facts_are_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("paper position corporate-action Decimal facts must be finite")
        return value

    @model_validator(mode="after")
    def preserves_split_rebase_contract(self) -> PaperPositionCorporateAction:
        expected_quantity = Decimal(self.pre_quantity) * self.split_ratio
        if expected_quantity != expected_quantity.to_integral_value():
            raise ValueError(
                "paper position corporate-action requires integral post-split quantity"
            )
        if self.post_quantity != int(expected_quantity):
            raise ValueError("paper position corporate-action post quantity must match split ratio")
        for label, pre_value, post_value in (
            ("entry fill", self.pre_entry_fill, self.post_entry_fill),
            ("initial stop", self.pre_initial_stop, self.post_initial_stop),
            ("initial unit risk", self.pre_initial_unit_risk, self.post_initial_unit_risk),
            ("max tradeable high", self.pre_max_tradeable_high, self.post_max_tradeable_high),
            ("min tradeable low", self.pre_min_tradeable_low, self.post_min_tradeable_low),
        ):
            if post_value != pre_value / self.split_ratio:
                raise ValueError(
                    f"paper position corporate-action post {label} must match split rebase"
                )
        expected_pre_mfe_r = (
            self.pre_max_tradeable_high - self.pre_entry_fill
        ) / self.pre_initial_unit_risk
        expected_pre_mae_r = (
            self.pre_min_tradeable_low - self.pre_entry_fill
        ) / self.pre_initial_unit_risk
        expected_post_mfe_r = (
            self.post_max_tradeable_high - self.post_entry_fill
        ) / self.post_initial_unit_risk
        expected_post_mae_r = (
            self.post_min_tradeable_low - self.post_entry_fill
        ) / self.post_initial_unit_risk
        if self.pre_mfe_r != expected_pre_mfe_r or self.pre_mae_r != expected_pre_mae_r:
            raise ValueError("paper position corporate-action pre R facts must match anchors")
        if self.post_mfe_r != expected_post_mfe_r or self.post_mae_r != expected_post_mae_r:
            raise ValueError("paper position corporate-action post R facts must match anchors")
        return self


class PositionSplitRebase(BaseModel):
    """Rebased M6 facts plus their append-ready corporate-action record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rebased_position: OpenPaperPosition
    rebased_excursion: PositionExcursionState
    record: PaperPositionCorporateAction

    @model_validator(mode="after")
    def matches_record_post_state(self) -> PositionSplitRebase:
        position = self.rebased_position
        excursion = self.rebased_excursion
        record = self.record
        if (
            position.position_id != record.position_id
            or position.symbol != record.symbol
            or position.strategy_version != record.strategy_version
            or position.config_hash != record.config_hash
            or position.quantity != record.post_quantity
            or position.entry_fill != record.post_entry_fill
            or position.initial_stop != record.post_initial_stop
            or position.initial_unit_risk != record.post_initial_unit_risk
        ):
            raise ValueError("split rebase position does not match corporate-action record")
        if (
            excursion.position_id != record.position_id
            or excursion.symbol != record.symbol
            or excursion.opened_as_of != position.opened_as_of
            or excursion.strategy_version != record.strategy_version
            or excursion.config_hash != record.config_hash
            or excursion.entry_fill != record.post_entry_fill
            or excursion.initial_unit_risk != record.post_initial_unit_risk
            or excursion.max_tradeable_high != record.post_max_tradeable_high
            or excursion.min_tradeable_low != record.post_min_tradeable_low
            or excursion.mfe_r != record.post_mfe_r
            or excursion.mae_r != record.post_mae_r
        ):
            raise ValueError("split rebase excursion does not match corporate-action record")
        return self


def rebase_open_position_for_split(
    position: OpenPaperPosition,
    action: SplitAction,
    *,
    excursion_state: PositionExcursionState | None,
) -> PositionSplitRebase:
    """Rebase an open position and its MFE/MAE anchors into a split share unit.

    The action must follow the opening session and follow the latest recorded
    excursion session. Missing or mismatched prior state fails closed rather
    than comparing a post-split true print with pre-split prices.
    """
    if not isinstance(position, OpenPaperPosition):
        raise DataValidationError("split rebase requires OpenPaperPosition")
    if not isinstance(action, SplitAction):
        raise DataValidationError("split rebase requires SplitAction")
    if action.symbol != position.symbol:
        raise DataValidationError("SplitAction and OpenPaperPosition symbol mismatch")
    if action.action_date <= position.opened_as_of:
        raise DataValidationError("SplitAction action date must follow position opening")
    if excursion_state is None:
        raise DataValidationError("split rebase requires prior PositionExcursionState")
    if not isinstance(excursion_state, PositionExcursionState):
        raise DataValidationError("split rebase requires prior PositionExcursionState")
    if excursion_state.as_of >= action.action_date:
        raise DataValidationError("prior PositionExcursionState must precede action session")

    ratio = _positive_finite_decimal(action.split_ratio, label="SplitAction.split_ratio")
    _validate_excursion_identity(excursion_state, position=position)
    post_quantity = _rebased_quantity(position.quantity, ratio=ratio)
    pre_entry_fill = _positive_finite_decimal(
        position.entry_fill,
        label="OpenPaperPosition.entry_fill",
    )
    pre_initial_stop = _positive_finite_decimal(
        position.initial_stop,
        label="OpenPaperPosition.initial_stop",
    )
    pre_initial_unit_risk = _positive_finite_decimal(
        position.initial_unit_risk,
        label="OpenPaperPosition.initial_unit_risk",
    )
    pre_max_tradeable_high = _positive_finite_decimal(
        excursion_state.max_tradeable_high,
        label="PositionExcursionState.max_tradeable_high",
    )
    pre_min_tradeable_low = _positive_finite_decimal(
        excursion_state.min_tradeable_low,
        label="PositionExcursionState.min_tradeable_low",
    )
    pre_mfe_r = _finite_decimal(excursion_state.mfe_r, label="PositionExcursionState.mfe_r")
    pre_mae_r = _finite_decimal(excursion_state.mae_r, label="PositionExcursionState.mae_r")

    rebased_position = OpenPaperPosition(
        position_id=position.position_id,
        symbol=position.symbol,
        opened_as_of=position.opened_as_of,
        strategy_version=position.strategy_version,
        config_hash=position.config_hash,
        quantity=post_quantity,
        entry_fill=pre_entry_fill / ratio,
        initial_stop=pre_initial_stop / ratio,
        initial_unit_risk=pre_initial_unit_risk / ratio,
    )
    rebased_excursion = PositionExcursionState(
        position_id=position.position_id,
        symbol=position.symbol,
        opened_as_of=position.opened_as_of,
        as_of=excursion_state.as_of,
        strategy_version=position.strategy_version,
        config_hash=position.config_hash,
        entry_fill=rebased_position.entry_fill,
        initial_unit_risk=rebased_position.initial_unit_risk,
        max_tradeable_high=pre_max_tradeable_high / ratio,
        min_tradeable_low=pre_min_tradeable_low / ratio,
        mfe_r=(pre_max_tradeable_high / ratio - rebased_position.entry_fill)
        / rebased_position.initial_unit_risk,
        mae_r=(pre_min_tradeable_low / ratio - rebased_position.entry_fill)
        / rebased_position.initial_unit_risk,
    )
    record = PaperPositionCorporateAction(
        position_id=position.position_id,
        symbol=position.symbol,
        action_id=action.action_id,
        action_date=action.action_date,
        split_ratio=ratio,
        strategy_version=position.strategy_version,
        config_hash=position.config_hash,
        pre_quantity=position.quantity,
        post_quantity=rebased_position.quantity,
        pre_entry_fill=pre_entry_fill,
        post_entry_fill=rebased_position.entry_fill,
        pre_initial_stop=pre_initial_stop,
        post_initial_stop=rebased_position.initial_stop,
        pre_initial_unit_risk=pre_initial_unit_risk,
        post_initial_unit_risk=rebased_position.initial_unit_risk,
        pre_max_tradeable_high=pre_max_tradeable_high,
        post_max_tradeable_high=rebased_excursion.max_tradeable_high,
        pre_min_tradeable_low=pre_min_tradeable_low,
        post_min_tradeable_low=rebased_excursion.min_tradeable_low,
        pre_mfe_r=pre_mfe_r,
        post_mfe_r=rebased_excursion.mfe_r,
        pre_mae_r=pre_mae_r,
        post_mae_r=rebased_excursion.mae_r,
    )
    return PositionSplitRebase(
        rebased_position=rebased_position,
        rebased_excursion=rebased_excursion,
        record=record,
    )


def _validate_excursion_identity(
    excursion_state: PositionExcursionState,
    *,
    position: OpenPaperPosition,
) -> None:
    if (
        excursion_state.position_id != position.position_id
        or excursion_state.symbol != position.symbol
        or excursion_state.opened_as_of != position.opened_as_of
        or excursion_state.strategy_version != position.strategy_version
        or excursion_state.config_hash != position.config_hash
        or excursion_state.entry_fill != position.entry_fill
        or excursion_state.initial_unit_risk != position.initial_unit_risk
    ):
        raise DataValidationError(
            "prior PositionExcursionState identity or position facts mismatch"
        )


def _rebased_quantity(quantity: int, *, ratio: Decimal) -> int:
    rebased = Decimal(quantity) * ratio
    if not rebased.is_finite() or rebased != rebased.to_integral_value():
        raise DataValidationError("split rebase requires integral post-split quantity")
    rebased_quantity = int(rebased)
    if rebased_quantity <= 0:
        raise DataValidationError("split rebase requires positive post-split quantity")
    return rebased_quantity


def _positive_finite_decimal(value: object, *, label: str) -> Decimal:
    decimal = _finite_decimal(value, label=label)
    if decimal <= _ZERO:
        raise DataValidationError(f"{label} must be positive")
    return decimal


def _finite_decimal(value: object, *, label: str) -> Decimal:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DataValidationError(f"invalid {label}") from exc
    if not decimal.is_finite():
        raise DataValidationError(f"non-finite {label}")
    return decimal
