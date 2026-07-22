"""Rebuild true-print bars from a split-adjusted provider series.

The M6 ADR forbids provider-native :class:`~smm.domain.models.Bar` objects
from entering fills or stops.  This module is the explicit, fail-closed bridge
from a verified split history to the separate :class:`~smm.domain.models.PrintBar`
type; it does not fetch data or create orders.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator

from smm.core.errors import DataValidationError
from smm.domain.models import Bar, PrintBar


class SplitAction(BaseModel):
    """One provider-identified split action in a verified history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    symbol: str
    action_date: date
    split_ratio: Decimal = Field(gt=Decimal("0"))

    @field_validator("action_id", "symbol")
    @classmethod
    def identity_is_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("split action identity fields must be non-empty")
        return value

    @field_validator("split_ratio")
    @classmethod
    def split_ratio_is_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("split_ratio must be finite")
        return value


class SplitActionHistory(BaseModel):
    """Action-history provenance supplied to the true-print adapter.

    ``coverage_start``/``coverage_end`` are the provider's declared history
    coverage, not inferred from non-empty actions.  An empty, covered history
    is therefore distinguishable from a missing action response.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    requested_start: date
    requested_end: date
    coverage_start: date
    coverage_end: date
    observation_cutoff: date
    actions: tuple[SplitAction, ...]

    @field_validator("symbol")
    @classmethod
    def symbol_is_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("split history symbol must be non-empty")
        return value


def rebuild_print_bars(
    bars: Sequence[Bar],
    *,
    history: SplitActionHistory,
) -> tuple[PrintBar, ...]:
    """Return true-print rows using only an explicitly verified split history.

    A split effective on ``action_date`` is already expressed in that session's
    share unit.  Earlier provider rows are therefore multiplied by every ratio
    with ``action_date > bar.date``; volume is divided by the same factor.
    Any uncertainty in the request interval, history coverage, identities or
    row ordering raises :class:`DataValidationError` rather than producing a
    plausible-but-wrong fill price.
    """

    _validate_history(history)
    source = tuple(bars)
    if not source:
        raise DataValidationError("cannot rebuild PrintBar series from empty bars")

    previous_date: date | None = None
    for bar in source:
        if not isinstance(bar, Bar):
            raise DataValidationError("PrintBar reconstruction requires provider Bar rows")
        if bar.symbol != history.symbol:
            raise DataValidationError(
                f"split history symbol {history.symbol!r} does not match bar symbol {bar.symbol!r}"
            )
        if bar.date < history.requested_start or bar.date > history.requested_end:
            raise DataValidationError(
                f"bar session {bar.date} is outside requested interval "
                f"{history.requested_start}..{history.requested_end}"
            )
        if previous_date is not None and bar.date <= previous_date:
            raise DataValidationError("provider bars must be sorted with unique sessions")
        previous_date = bar.date

    prints: list[PrintBar] = []
    for bar in source:
        factor = _split_factor_for(bar.date, history.actions)
        try:
            open_ = float(Decimal(str(bar.open)) * factor)
            high = float(Decimal(str(bar.high)) * factor)
            low = float(Decimal(str(bar.low)) * factor)
            close = float(Decimal(str(bar.close)) * factor)
            volume = float(Decimal(str(bar.volume)) / factor)
        except (ArithmeticError, ValueError) as exc:
            raise DataValidationError(
                f"{bar.symbol}: invalid split reconstruction at {bar.date}"
            ) from exc
        if not all(isfinite(value) for value in (open_, high, low, close, volume)):
            raise DataValidationError(
                f"{bar.symbol}: non-finite split reconstruction at {bar.date}"
            )
        prints.append(
            PrintBar(
                symbol=bar.symbol,
                date=bar.date,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
    return tuple(prints)


def _validate_history(history: SplitActionHistory) -> None:
    if history.requested_start > history.requested_end:
        raise DataValidationError("requested interval start must not be after end")
    if history.observation_cutoff < history.requested_end:
        raise DataValidationError("observation cutoff must cover requested interval")
    if (
        history.coverage_start > history.requested_start
        or history.coverage_end < history.observation_cutoff
    ):
        raise DataValidationError(
            "split action history coverage is incomplete for requested interval"
        )

    action_ids: set[str] = set()
    for action in history.actions:
        if action.symbol != history.symbol:
            raise DataValidationError(
                f"split action symbol {action.symbol!r} does not match "
                f"history symbol {history.symbol!r}"
            )
        if action.action_id in action_ids:
            raise DataValidationError(f"duplicate split action identity {action.action_id!r}")
        action_ids.add(action.action_id)
        if action.action_date < history.coverage_start or action.action_date > history.coverage_end:
            raise DataValidationError(
                f"split action {action.action_id!r} is outside declared coverage"
            )
        if action.action_date > history.observation_cutoff:
            raise DataValidationError(
                f"split action {action.action_id!r} is after observation cutoff"
            )


def _split_factor_for(session: date, actions: Sequence[SplitAction]) -> Decimal:
    factor = Decimal("1")
    for action in actions:
        if action.action_date > session:
            factor *= action.split_ratio
    return factor
