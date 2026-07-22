"""Pure M6 close-condition exit scheduling contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.domain.views import AdjustedBar, TradeableBar
from smm.paper.exits import (
    CloseExitAssessment,
    CloseExitStatus,
    assess_close_exit,
)
from smm.paper.stops import OpenPaperPosition, assess_long_stop

REPO = Path(__file__).resolve().parents[2]
LOADED_CONFIG = load_config(REPO / "configs" / "smm_v1_1_0.yaml")
M6_CONFIG = LOADED_CONFIG.config
ENTRY_SESSION = date(2024, 6, 18)
SESSION = date(2024, 7, 2)
NEXT_SESSION = date(2024, 7, 3)


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


def adjusted_bar(**updates: object) -> AdjustedBar:
    values: dict[str, object] = {
        "symbol": "NVDA",
        "date": SESSION,
        "adj_open": 101.0,
        "adj_high": 103.0,
        "adj_low": 99.0,
        "adj_close": 101.0,
        "volume": 1_000_000.0,
    }
    values.update(updates)
    return AdjustedBar(**values)


def tradeable_bar(**updates: object) -> TradeableBar:
    values: dict[str, object] = {
        "symbol": "NVDA",
        "date": SESSION,
        "open": 100.0,
        "high": 102.0,
        "low": 98.0,
        "close": 100.0,
        "volume": 1_000_000.0,
    }
    values.update(updates)
    return TradeableBar(**values)


def assess(**updates: object) -> CloseExitAssessment:
    values: dict[str, object] = {
        "position": position(),
        "adjusted_bar": adjusted_bar(),
        "tradeable_bar": tradeable_bar(),
        "expected_exit_session": NEXT_SESSION,
        "ema_20": Decimal("100"),
        "completed_hold_sessions": 1,
        "mfe_r": Decimal("1"),
        "exit": M6_CONFIG.exit,
    }
    values.update(updates)
    if "stop_assessment" not in values:
        values["stop_assessment"] = assess_long_stop(
            values["position"],
            values["tradeable_bar"],
            execution=M6_CONFIG.execution,
        )
    return assess_close_exit(**values)


def test_adjusted_close_below_ema_schedules_next_true_print_open_exit() -> None:
    result = assess(adjusted_bar=adjusted_bar(adj_close=99.99))

    assert result.status is CloseExitStatus.SCHEDULED
    assert result.scheduled_session == NEXT_SESSION
    assert result.reason_codes == ("paper_exit_ema20_close_below",)
    assert result.adjusted_close == Decimal("99.99")
    assert result.tradeable_close == Decimal("100.0")


def test_adjusted_close_equal_to_ema_does_not_schedule_exit() -> None:
    result = assess(adjusted_bar=adjusted_bar(adj_close=100.0))

    assert result.status is CloseExitStatus.HELD
    assert result.scheduled_session is None
    assert result.reason_codes == ("paper_exit_conditions_not_met",)


def test_time_stop_schedules_only_after_all_frozen_conjuncts_hold() -> None:
    result = assess(
        completed_hold_sessions=10,
        mfe_r=Decimal("0.49"),
        adjusted_bar=adjusted_bar(adj_close=101.0),
    )

    assert result.status is CloseExitStatus.SCHEDULED
    assert result.reason_codes == ("paper_exit_time_stop",)
    assert result.scheduled_session == NEXT_SESSION


@pytest.mark.parametrize(
    ("completed_hold_sessions", "mfe_r", "close"),
    [
        (9, Decimal("0.49"), 100.0),
        (10, Decimal("0.5"), 100.0),
        (10, Decimal("0.49"), 100.075),
    ],
)
def test_time_stop_does_not_schedule_when_any_conjunct_is_not_met(
    completed_hold_sessions: int,
    mfe_r: Decimal,
    close: float,
) -> None:
    result = assess(
        completed_hold_sessions=completed_hold_sessions,
        mfe_r=mfe_r,
        adjusted_bar=adjusted_bar(adj_close=101.0),
        tradeable_bar=tradeable_bar(close=close),
    )

    assert result.status is CloseExitStatus.HELD
    assert result.reason_codes == ("paper_exit_conditions_not_met",)


def test_multiple_close_conditions_retain_stable_reason_order() -> None:
    result = assess(
        adjusted_bar=adjusted_bar(adj_close=99.0),
        completed_hold_sessions=10,
        mfe_r=Decimal("0.49"),
    )

    assert result.status is CloseExitStatus.SCHEDULED
    assert result.reason_codes == (
        "paper_exit_ema20_close_below",
        "paper_exit_time_stop",
    )


def test_same_session_stop_prevents_close_exit_scheduling() -> None:
    stopped_bar = tradeable_bar(low=96.0)
    stopped = assess_long_stop(
        position(),
        stopped_bar,
        execution=M6_CONFIG.execution,
    )

    with pytest.raises(DataValidationError, match="held StopExitAssessment"):
        assess(tradeable_bar=stopped_bar, stop_assessment=stopped)


def test_close_exit_rejects_held_stop_with_mismatched_position_facts() -> None:
    held_stop = assess_long_stop(
        position(),
        tradeable_bar(),
        execution=M6_CONFIG.execution,
    ).model_copy(update={"quantity": 9})

    with pytest.raises(DataValidationError, match="identity or position facts"):
        assess(stop_assessment=held_stop)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (adjusted_bar(symbol="MSFT"), "symbol mismatch"),
        (adjusted_bar(date=date(2024, 7, 1)), "session mismatch"),
    ],
)
def test_close_exit_rejects_cross_series_identity_mismatch(
    value: AdjustedBar,
    message: str,
) -> None:
    with pytest.raises(DataValidationError, match=message):
        assess(adjusted_bar=value)


def test_close_exit_rejects_unknown_frozen_trailing_rule() -> None:
    unsupported_exit = M6_CONFIG.exit.model_copy(
        update={"trailing_exit": "close_below_sma_50"}
    )

    with pytest.raises(DataValidationError, match="trailing_exit"):
        assess(exit=unsupported_exit)


def test_close_exit_rejects_fixed_profit_target_config() -> None:
    unsupported_exit = M6_CONFIG.exit.model_copy(
        update={"fixed_profit_target": True}
    )

    with pytest.raises(DataValidationError, match="fixed_profit_target"):
        assess(exit=unsupported_exit)


def test_close_exit_model_rejects_held_state_with_scheduled_session() -> None:
    result = assess()
    values = result.model_dump()
    values["scheduled_session"] = NEXT_SESSION

    with pytest.raises(ValidationError, match="held close exit must not schedule"):
        CloseExitAssessment(**values)


def test_close_exit_model_rejects_unscheduled_exit_reason() -> None:
    result = assess(adjusted_bar=adjusted_bar(adj_close=99.0))
    values = result.model_dump()
    values["reason_codes"] = ("paper_exit_conditions_not_met",)

    with pytest.raises(ValidationError, match="recognized close-exit reasons"):
        CloseExitAssessment(**values)


def test_close_exit_model_rejects_schedule_on_or_before_close_session() -> None:
    result = assess(adjusted_bar=adjusted_bar(adj_close=99.0))
    values = result.model_dump()
    values["scheduled_session"] = SESSION

    with pytest.raises(ValidationError, match="requires a later session"):
        CloseExitAssessment(**values)
