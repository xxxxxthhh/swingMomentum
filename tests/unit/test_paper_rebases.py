"""Pure M6 split rebase contract for open paper positions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.domain.views import TradeableBar
from smm.paper.excursions import PositionExcursionState, update_position_excursion
from smm.paper.prints import SplitAction
from smm.paper.rebases import (
    PaperPositionCorporateAction,
    PositionSplitRebase,
    rebase_open_position_for_split,
)
from smm.paper.stops import OpenPaperPosition

REPO = Path(__file__).resolve().parents[2]
LOADED_CONFIG = load_config(REPO / "configs" / "smm_v1_1_0.yaml")
M6_CONFIG = LOADED_CONFIG.config
ENTRY_SESSION = date(2024, 6, 18)
ACTION_SESSION = date(2024, 6, 20)


def position(**updates: object) -> OpenPaperPosition:
    values: dict[str, object] = {
        "position_id": "paper-order-signal-nvda-2024-06-17",
        "symbol": "NVDA",
        "opened_as_of": ENTRY_SESSION,
        "strategy_version": M6_CONFIG.strategy.version,
        "config_hash": LOADED_CONFIG.config_hash,
        "quantity": 10,
        "entry_fill": Decimal("100.075"),
        "initial_stop": Decimal("97"),
        "initial_unit_risk": Decimal("3.1"),
    }
    values.update(updates)
    return OpenPaperPosition(**values)


def excursion(
    position_: OpenPaperPosition | None = None,
    **updates: object,
) -> PositionExcursionState:
    open_position = position_ or position()
    bar = TradeableBar(
        symbol=open_position.symbol,
        date=ENTRY_SESSION,
        open=100.0,
        high=106.275,
        low=98.525,
        close=101.0,
        volume=1_000_000.0,
    )
    state = update_position_excursion(open_position, bar, prior_state=None)
    return state.model_copy(update=updates)


def split_action(**updates: object) -> SplitAction:
    values: dict[str, object] = {
        "action_id": "nvda-split-2024-06-20-2-for-1",
        "symbol": "NVDA",
        "action_date": ACTION_SESSION,
        "split_ratio": Decimal("2"),
    }
    values.update(updates)
    return SplitAction(**values)


def unchecked_rebase_result(
    result: PositionSplitRebase,
    *,
    rebased_position: OpenPaperPosition | None = None,
    rebased_excursion: PositionExcursionState | None = None,
) -> PositionSplitRebase:
    """Build a wrapper solely to isolate its post-validation comparisons.

    The nested position/excursion models have their own invariants. This helper
    lets each outer record comparison be regression-tested independently.
    """
    return PositionSplitRebase.model_construct(
        rebased_position=(
            result.rebased_position if rebased_position is None else rebased_position
        ),
        rebased_excursion=(
            result.rebased_excursion if rebased_excursion is None else rebased_excursion
        ),
        record=result.record,
    )


def test_split_rebase_preserves_economic_position_and_excursion_r() -> None:
    result = rebase_open_position_for_split(
        position(),
        split_action(),
        excursion_state=excursion(),
    )

    assert result.rebased_position.quantity == 20
    assert result.rebased_position.entry_fill == Decimal("50.0375")
    assert result.rebased_position.initial_stop == Decimal("48.5")
    assert result.rebased_position.initial_unit_risk == Decimal("1.55")
    assert result.rebased_excursion.max_tradeable_high == Decimal("53.1375")
    assert result.rebased_excursion.min_tradeable_low == Decimal("49.2625")
    assert result.rebased_excursion.mfe_r == Decimal("2")
    assert result.rebased_excursion.mae_r == Decimal("-0.5")
    assert result.record.action_id == "nvda-split-2024-06-20-2-for-1"
    assert result.record.action_date == ACTION_SESSION
    assert result.record.pre_quantity == 10
    assert result.record.post_quantity == 20
    assert result.record.pre_entry_fill * result.record.pre_quantity == (
        result.record.post_entry_fill * result.record.post_quantity
    )
    assert result.record.pre_initial_unit_risk * result.record.pre_quantity == (
        result.record.post_initial_unit_risk * result.record.post_quantity
    )


def test_split_rebase_supports_integral_three_for_two_quantity() -> None:
    result = rebase_open_position_for_split(
        position(),
        split_action(split_ratio=Decimal("1.5")),
        excursion_state=excursion(),
    )

    assert result.rebased_position.quantity == 15
    assert result.rebased_position.entry_fill == Decimal("66.71666666666666666666666667")
    assert result.rebased_excursion.mfe_r == (
        (result.rebased_excursion.max_tradeable_high - result.rebased_position.entry_fill)
        / result.rebased_position.initial_unit_risk
    )
    assert result.rebased_excursion.mae_r == (
        (result.rebased_excursion.min_tradeable_low - result.rebased_position.entry_fill)
        / result.rebased_position.initial_unit_risk
    )


def test_split_rebase_rejects_non_integral_post_split_quantity() -> None:
    with pytest.raises(DataValidationError, match="integral post-split quantity"):
        rebase_open_position_for_split(
            position(quantity=1),
            split_action(split_ratio=Decimal("1.5")),
            excursion_state=excursion(position(quantity=1)),
        )


def test_split_rebase_requires_prior_excursion_before_action_session() -> None:
    with pytest.raises(DataValidationError, match="prior PositionExcursionState"):
        rebase_open_position_for_split(
            position(),
            split_action(),
            excursion_state=None,
        )
    with pytest.raises(DataValidationError, match="must precede action session"):
        rebase_open_position_for_split(
            position(),
            split_action(),
            excursion_state=excursion(as_of=ACTION_SESSION),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("position_id", "other-position"),
        ("symbol", "MSFT"),
        ("opened_as_of", date(2024, 6, 17)),
        ("strategy_version", "SMM-V9.9.9"),
        ("config_hash", "other-config"),
        ("entry_fill", Decimal("100.076")),
        ("initial_unit_risk", Decimal("3.2")),
    ],
)
def test_split_rebase_rejects_excursion_identity_mismatch(
    field: str,
    value: object,
) -> None:
    with pytest.raises(DataValidationError, match="identity or position facts"):
        rebase_open_position_for_split(
            position(),
            split_action(),
            excursion_state=excursion(**{field: value}),
        )


def test_split_rebase_rejects_cross_symbol_or_non_crossing_action() -> None:
    with pytest.raises(DataValidationError, match="symbol mismatch"):
        rebase_open_position_for_split(
            position(),
            split_action(symbol="MSFT"),
            excursion_state=excursion(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("post_entry_fill", Decimal("50")),
        ("post_initial_stop", Decimal("48")),
        ("post_initial_unit_risk", Decimal("1.5")),
        ("post_max_tradeable_high", Decimal("53")),
        ("post_min_tradeable_low", Decimal("49")),
    ],
)
def test_corporate_action_record_rejects_each_inconsistent_split_anchor(
    field: str,
    value: Decimal,
) -> None:
    result = rebase_open_position_for_split(
        position(),
        split_action(),
        excursion_state=excursion(),
    )
    values = result.record.model_dump()
    values[field] = value

    with pytest.raises(ValidationError, match="post .* match split rebase"):
        PaperPositionCorporateAction(**values)


def test_corporate_action_record_rejects_inconsistent_post_quantity() -> None:
    result = rebase_open_position_for_split(
        position(),
        split_action(),
        excursion_state=excursion(),
    )
    values = result.record.model_dump()
    values["post_quantity"] = 19

    with pytest.raises(ValidationError, match="post quantity must match"):
        PaperPositionCorporateAction(**values)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("pre_mfe_r", Decimal("1.9"), "pre R facts"),
        ("pre_mae_r", Decimal("-0.4"), "pre R facts"),
        ("post_mfe_r", Decimal("1.9"), "post R facts"),
        ("post_mae_r", Decimal("-0.4"), "post R facts"),
    ],
)
def test_corporate_action_record_rejects_each_inconsistent_r_fact(
    field: str,
    value: Decimal,
    match: str,
) -> None:
    result = rebase_open_position_for_split(
        position(),
        split_action(),
        excursion_state=excursion(),
    )
    values = result.record.model_dump()
    values[field] = value

    with pytest.raises(ValidationError, match=match):
        PaperPositionCorporateAction(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("position_id", "other-position"),
        ("symbol", "MSFT"),
        ("strategy_version", "SMM-V9.9.9"),
        ("config_hash", "other-config"),
        ("quantity", 19),
        ("entry_fill", Decimal("50")),
        ("initial_stop", Decimal("48")),
        ("initial_unit_risk", Decimal("1.5")),
    ],
)
def test_split_rebase_result_rejects_each_position_drift_from_record(
    field: str,
    value: object,
) -> None:
    result = rebase_open_position_for_split(
        position(),
        split_action(),
        excursion_state=excursion(),
    )

    candidate = unchecked_rebase_result(
        result,
        rebased_position=result.rebased_position.model_copy(update={field: value}),
    )

    with pytest.raises(ValueError, match="position does not match"):
        candidate.matches_record_post_state()


def test_split_rebase_result_constructs_with_record_contract() -> None:
    result = rebase_open_position_for_split(
        position(),
        split_action(),
        excursion_state=excursion(),
    )

    with pytest.raises(ValidationError, match="position does not match"):
        PositionSplitRebase(
            rebased_position=result.rebased_position.model_copy(update={"quantity": 19}),
            rebased_excursion=result.rebased_excursion,
            record=result.record,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("position_id", "other-position"),
        ("symbol", "MSFT"),
        ("opened_as_of", date(2024, 6, 17)),
        ("strategy_version", "SMM-V9.9.9"),
        ("config_hash", "other-config"),
        ("entry_fill", Decimal("50")),
        ("initial_unit_risk", Decimal("1.5")),
        ("max_tradeable_high", Decimal("53")),
        ("min_tradeable_low", Decimal("49")),
        ("mfe_r", Decimal("1.9")),
        ("mae_r", Decimal("-0.4")),
    ],
)
def test_split_rebase_result_rejects_each_excursion_drift_from_record(
    field: str,
    value: object,
) -> None:
    result = rebase_open_position_for_split(
        position(),
        split_action(),
        excursion_state=excursion(),
    )

    candidate = unchecked_rebase_result(
        result,
        rebased_excursion=result.rebased_excursion.model_copy(update={field: value}),
    )

    with pytest.raises(ValueError, match="excursion does not match"):
        candidate.matches_record_post_state()


def test_split_rebase_rejects_action_on_position_opening_session() -> None:
    with pytest.raises(DataValidationError, match="must follow position opening"):
        rebase_open_position_for_split(
            position(),
            split_action(action_date=ENTRY_SESSION),
            excursion_state=excursion(),
        )
