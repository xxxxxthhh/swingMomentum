"""Pure M6 true-print MFE/MAE update contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.domain.views import AdjustedBar, TradeableBar
from smm.paper.excursions import PositionExcursionState, update_position_excursion
from smm.paper.stops import OpenPaperPosition

REPO = Path(__file__).resolve().parents[2]
LOADED_CONFIG = load_config(REPO / "configs" / "smm_v1_1_0.yaml")
M6_CONFIG = LOADED_CONFIG.config
ENTRY_SESSION = date(2024, 6, 18)
NEXT_SESSION = date(2024, 6, 19)


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


def tradeable_bar(**updates: object) -> TradeableBar:
    values: dict[str, object] = {
        "symbol": "NVDA",
        "date": ENTRY_SESSION,
        "open": 100.0,
        "high": 106.275,
        "low": 98.525,
        "close": 101.0,
        "volume": 1_000_000.0,
    }
    values.update(updates)
    return TradeableBar(**values)


def update(**updates: object) -> PositionExcursionState:
    values: dict[str, object] = {
        "position": position(),
        "bar": tradeable_bar(),
        "prior_state": None,
    }
    values.update(updates)
    return update_position_excursion(**values)


def test_entry_session_initializes_mfe_and_mae_from_true_print_high_and_low() -> None:
    result = update()

    assert result.as_of == ENTRY_SESSION
    assert result.max_tradeable_high == Decimal("106.275")
    assert result.min_tradeable_low == Decimal("98.525")
    assert result.mfe_r == Decimal("2")
    assert result.mae_r == Decimal("-0.5")


def test_later_session_preserves_mfe_and_extends_mae_from_prior_state() -> None:
    initial = update()
    result = update(
        bar=tradeable_bar(
            date=NEXT_SESSION,
            high=105.0,
            low=95.425,
        ),
        prior_state=initial,
    )

    assert result.max_tradeable_high == Decimal("106.275")
    assert result.min_tradeable_low == Decimal("95.425")
    assert result.mfe_r == Decimal("2")
    assert result.mae_r == Decimal("-1.5")


def test_later_session_without_prior_excursion_state_fails_closed() -> None:
    with pytest.raises(DataValidationError, match="prior excursion state"):
        update(bar=tradeable_bar(date=NEXT_SESSION))


def test_entry_session_rejects_existing_excursion_state() -> None:
    with pytest.raises(DataValidationError, match="entry-session excursion"):
        update(prior_state=update())


def test_excursion_rejects_adjusted_or_cross_symbol_price_input() -> None:
    adjusted = AdjustedBar(
        symbol="NVDA",
        date=ENTRY_SESSION,
        adj_open=100.0,
        adj_high=106.275,
        adj_low=98.525,
        adj_close=101.0,
        volume=1_000_000.0,
    )

    with pytest.raises(DataValidationError, match="TradeableBar"):
        update(bar=adjusted)
    with pytest.raises(DataValidationError, match="symbol mismatch"):
        update(bar=tradeable_bar(symbol="MSFT"))


def test_excursion_rejects_true_print_with_inverted_high_low() -> None:
    with pytest.raises(DataValidationError, match="high must be >="):
        update(bar=tradeable_bar(high=98.0, low=99.0))


def test_excursion_rejects_prior_state_with_mismatched_identity() -> None:
    prior = update().model_copy(update={"initial_unit_risk": Decimal("3.2")})

    with pytest.raises(DataValidationError, match="identity or position facts"):
        update(bar=tradeable_bar(date=NEXT_SESSION), prior_state=prior)


def test_excursion_rejects_prior_state_from_same_or_later_session() -> None:
    prior = update().model_copy(update={"as_of": NEXT_SESSION})

    with pytest.raises(DataValidationError, match="must precede"):
        update(bar=tradeable_bar(date=NEXT_SESSION), prior_state=prior)


def test_excursion_state_rejects_inconsistent_mfe_r() -> None:
    result = update()
    values = result.model_dump()
    values["mfe_r"] = Decimal("1.9")

    with pytest.raises(ValidationError, match="mfe_r must match"):
        PositionExcursionState(**values)


def test_excursion_state_rejects_inconsistent_mae_r() -> None:
    result = update()
    values = result.model_dump()
    values["mae_r"] = Decimal("-0.4")

    with pytest.raises(ValidationError, match="mae_r must match"):
        PositionExcursionState(**values)


def test_excursion_state_rejects_as_of_before_position_opening() -> None:
    result = update()
    values = result.model_dump()
    values["as_of"] = date(2024, 6, 17)

    with pytest.raises(ValidationError, match="cannot precede"):
        PositionExcursionState(**values)


def test_excursion_state_rejects_anchor_order_inconsistent_with_true_print_range() -> None:
    result = update()
    values = result.model_dump()
    values["min_tradeable_low"] = Decimal("107")

    with pytest.raises(ValidationError, match="high must be >= low"):
        PositionExcursionState(**values)
