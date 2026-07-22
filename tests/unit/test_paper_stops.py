"""Pure M6 true-print stop-exit assessment contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.domain.models import PrintBar
from smm.domain.views import TradeableBar
from smm.paper.stops import (
    OpenPaperPosition,
    StopAssessmentStatus,
    StopExitAssessment,
    assess_long_stop,
)

REPO = Path(__file__).resolve().parents[2]
LOADED_CONFIG = load_config(REPO / "configs" / "smm_v1_1_0.yaml")
M6_CONFIG = LOADED_CONFIG.config
ENTRY_SESSION = date(2024, 6, 18)
SESSION = date(2024, 6, 19)


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
        "date": SESSION,
        "open": 100.0,
        "high": 102.0,
        "low": 98.0,
        "close": 101.0,
        "volume": 1_000_000.0,
    }
    values.update(updates)
    return TradeableBar(**values)


def assess(**updates: object):
    values: dict[str, object] = {
        "position": position(),
        "bar": tradeable_bar(),
        "execution": M6_CONFIG.execution,
    }
    values.update(updates)
    return assess_long_stop(**values)


def test_intraday_low_through_stop_uses_stop_as_base_exit_and_sell_cost() -> None:
    result = assess(bar=tradeable_bar(low=96.0))

    assert result.status is StopAssessmentStatus.STOPPED
    assert result.reason_codes == ("paper_stop_triggered",)
    assert result.base_exit_price == Decimal("97")
    assert result.execution_quote is not None
    assert result.execution_quote.fill_price == Decimal("96.92725")
    assert result.execution_quote.cash_per_share == Decimal("96.92225")
    assert result.execution_quote.side.value == "sell"
    assert result.position_id == position().position_id
    assert result.as_of == SESSION


def test_gap_through_stop_uses_true_print_open_not_stop() -> None:
    result = assess(bar=tradeable_bar(open=96.0, low=95.0))

    assert result.status is StopAssessmentStatus.STOPPED
    assert result.reason_codes == ("paper_stop_gap_open",)
    assert result.base_exit_price == Decimal("96.0")
    assert result.execution_quote is not None
    assert result.execution_quote.fill_price == Decimal("95.928000")
    assert result.execution_quote.cash_per_share == Decimal("95.923000")


def test_open_exactly_at_stop_is_gap_through_stop() -> None:
    result = assess(bar=tradeable_bar(open=97.0, low=96.0))

    assert result.reason_codes == ("paper_stop_gap_open",)
    assert result.base_exit_price == Decimal("97.0")


def test_low_exactly_at_stop_is_intraday_stop_not_gap() -> None:
    result = assess(bar=tradeable_bar(open=100.0, low=97.0))

    assert result.reason_codes == ("paper_stop_triggered",)
    assert result.base_exit_price == Decimal("97")


def test_no_stop_keeps_position_without_an_exit_quote() -> None:
    result = assess()

    assert result.status is StopAssessmentStatus.HELD
    assert result.reason_codes == ("paper_stop_not_triggered",)
    assert result.base_exit_price is None
    assert result.execution_quote is None


def test_stop_assessment_allows_entry_session_low_stop() -> None:
    result = assess(
        position=position(opened_as_of=SESSION),
        bar=tradeable_bar(low=96.0),
    )

    assert result.status is StopAssessmentStatus.STOPPED


def test_stop_assessment_rejects_non_tradeable_bar() -> None:
    print_bar = PrintBar(
        symbol="NVDA",
        date=SESSION,
        open=100.0,
        high=102.0,
        low=96.0,
        close=101.0,
        volume=1_000_000.0,
    )

    with pytest.raises(DataValidationError, match="TradeableBar"):
        assess(bar=print_bar)


@pytest.mark.parametrize(
    ("bar_value", "message"),
    [
        (tradeable_bar(date=date(2024, 6, 17)), "precedes position opening"),
        (tradeable_bar(symbol="MSFT"), "symbol mismatch"),
        (tradeable_bar(open=float("nan")), "non-finite TradeableBar.open"),
        (tradeable_bar(low=float("nan")), "non-finite TradeableBar.low"),
    ],
)
def test_stop_assessment_rejects_invalid_true_print_boundary(
    bar_value: TradeableBar, message: str
) -> None:
    with pytest.raises(DataValidationError, match=message):
        assess(bar=bar_value)


def test_open_paper_position_rejects_stop_at_or_above_entry_fill() -> None:
    with pytest.raises(ValidationError, match="entry_fill must exceed initial_stop"):
        position(initial_stop=Decimal("100.075"))


def test_stop_assessment_model_rejects_quote_or_status_inconsistency() -> None:
    stopped = assess(bar=tradeable_bar(low=96.0))
    values = stopped.model_dump()
    values["status"] = StopAssessmentStatus.HELD
    values["reason_codes"] = ("paper_stop_not_triggered",)

    with pytest.raises(ValidationError, match="held stop assessment must not carry exit"):
        StopExitAssessment(**values)


def test_stop_assessment_model_rejects_unrecognized_stopped_reason() -> None:
    stopped = assess(bar=tradeable_bar(low=96.0))
    values = stopped.model_dump()
    values["reason_codes"] = ("paper_stop_not_triggered",)

    with pytest.raises(ValidationError, match="recognized stop reason"):
        StopExitAssessment(**values)
