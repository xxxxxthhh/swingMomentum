"""Pure M6 next-open entry-assessment contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.domain.enums import MarketRegime, OrderSide, RiskVerdict
from smm.domain.models import PrintBar, RiskDecision
from smm.domain.views import TradeableBar
from smm.paper.entries import EntryAssessment, EntryStatus, assess_next_open_entry

REPO = Path(__file__).resolve().parents[2]
LOADED_CONFIG = load_config(REPO / "configs" / "smm_v1_1_0.yaml")
M6_CONFIG = LOADED_CONFIG.config
SIGNAL_AS_OF = date(2024, 6, 17)
NEXT_SESSION = date(2024, 6, 18)


def risk_decision(**updates: object) -> RiskDecision:
    values: dict[str, object] = {
        "signal_id": "signal-nvda-2024-06-17",
        "symbol": "NVDA",
        "as_of": SIGNAL_AS_OF,
        "strategy_version": M6_CONFIG.strategy.version,
        "config_hash": LOADED_CONFIG.config_hash,
        "entry_risk_multiplier": Decimal("1"),
        "circuit_state_identity": "circuit-2024-06-17",
        "verdict": RiskVerdict.ACCEPT,
        "reason_codes": ("risk_sized_by_per_trade",),
        "quantity": 10,
        "entry_reference": Decimal("100"),
        "stop_reference": Decimal("97"),
        "unit_risk": Decimal("3.1"),
        "planned_capital": Decimal("1000"),
        "planned_initial_risk": Decimal("31"),
        "sector": "information_technology",
        "risk_cluster": "semiconductors",
        "regime": MarketRegime.RISK_ON,
    }
    values.update(updates)
    return RiskDecision(**values)


def tradeable_bar(**updates: object) -> TradeableBar:
    values: dict[str, object] = {
        "symbol": "NVDA",
        "date": NEXT_SESSION,
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 1_000_000.0,
    }
    values.update(updates)
    return TradeableBar(**values)


def assess(**updates: object):
    values: dict[str, object] = {
        "decision": risk_decision(),
        "bar": tradeable_bar(),
        "expected_session": NEXT_SESSION,
        "atr_20": Decimal("2"),
        "execution": M6_CONFIG.execution,
        "stop": M6_CONFIG.stop,
    }
    values.update(updates)
    return assess_next_open_entry(**values)


def test_next_open_entry_assessment_is_fillable_without_changing_m5_quantity() -> None:
    result = assess()

    assert result.status is EntryStatus.FILLABLE
    assert result.reason_codes == ("paper_entry_ready",)
    assert result.planned_quantity == 10
    assert result.executable_quantity == 10
    assert result.actual_open == Decimal("100.0")
    assert result.gap_atr == Decimal("0.0")
    assert result.stop_distance_atr == Decimal("1.53750")
    assert result.execution_quote.fill_price == Decimal("100.07500")
    assert result.execution_quote.cash_per_share == Decimal("100.08000")
    assert result.as_of == NEXT_SESSION
    assert result.signal_as_of == SIGNAL_AS_OF
    assert result.strategy_version == M6_CONFIG.strategy.version
    assert result.config_hash == LOADED_CONFIG.config_hash


def test_next_open_entry_cancels_gap_exceeding_frozen_limit_without_quantity() -> None:
    result = assess(bar=tradeable_bar(open=103.0))

    assert result.status is EntryStatus.CANCELLED
    assert result.reason_codes == ("paper_entry_gap_exceeds_limit",)
    assert result.executable_quantity == 0
    assert result.gap_atr == Decimal("1.5")
    assert result.stop_distance_atr is None
    assert result.execution_quote.fill_price == Decimal("103.077250")


def test_next_open_entry_cancels_open_at_or_below_stop_after_gap_check() -> None:
    result = assess(bar=tradeable_bar(open=97.0), atr_20=Decimal("10"))

    assert result.status is EntryStatus.CANCELLED
    assert result.reason_codes == ("paper_entry_open_at_or_below_stop",)
    assert result.executable_quantity == 0
    assert result.stop_distance_atr is None


def test_next_open_entry_cancels_actual_stop_distance_outside_frozen_band() -> None:
    result = assess(bar=tradeable_bar(open=102.0))

    assert result.status is EntryStatus.CANCELLED
    assert result.reason_codes == ("paper_entry_stop_distance_out_of_bounds",)
    assert result.executable_quantity == 0
    assert result.gap_atr == Decimal("1.0")
    assert result.stop_distance_atr == Decimal("2.538250")


def test_gap_at_exact_frozen_limit_is_allowed_before_stop_distance_check() -> None:
    result = assess(
        bar=tradeable_bar(open=102.0),
        decision=risk_decision(stop_reference=Decimal("97.1")),
    )

    assert result.status is EntryStatus.FILLABLE
    assert result.gap_atr == Decimal("1.0")
    assert result.stop_distance_atr == Decimal("2.488250")


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"executable_quantity": 11}, "cannot increase the M5 planned quantity"),
        (
            {
                "status": EntryStatus.CANCELLED,
                "reason_codes": ("paper_entry_gap_exceeds_limit",),
                "executable_quantity": 1,
            },
            "cancelled entry assessment must have zero executable quantity",
        ),
    ],
)
def test_entry_assessment_rejects_quantity_contract_violations(
    updates: dict[str, object], message: str
) -> None:
    values = assess().model_dump()
    values.update(updates)

    with pytest.raises(ValidationError, match=message):
        EntryAssessment(**values)


def test_entry_assessment_rejects_non_buy_or_mismatched_quote() -> None:
    result = assess()
    sell_quote = result.execution_quote.model_copy(update={"side": OrderSide.SELL})
    values = result.model_dump()
    values["execution_quote"] = sell_quote

    with pytest.raises(ValidationError, match="must be a BUY quote"):
        EntryAssessment(**values)

    values = result.model_dump()
    values["actual_open"] = Decimal("101")

    with pytest.raises(ValidationError, match="execution quote identity mismatch"):
        EntryAssessment(**values)


def test_next_open_entry_rejects_non_accepted_risk_decision() -> None:
    rejected = risk_decision(
        verdict=RiskVerdict.REJECT,
        reason_codes=("risk_off_new_entries_blocked",),
        quantity=0,
        planned_capital=Decimal("0"),
        planned_initial_risk=Decimal("0"),
    )

    with pytest.raises(DataValidationError, match="RiskDecision ACCEPT"):
        assess(decision=rejected)


def test_next_open_entry_rejects_non_risk_decision_input() -> None:
    with pytest.raises(DataValidationError, match="requires RiskDecision"):
        assess(decision=object())


def test_next_open_entry_rejects_invalid_risk_decision_price_relationship() -> None:
    with pytest.raises(DataValidationError, match="entry_reference must exceed stop_reference"):
        assess(decision=risk_decision(stop_reference=Decimal("100")))


def test_next_open_entry_rejects_blank_risk_decision_identity() -> None:
    with pytest.raises(DataValidationError, match="identity fields"):
        assess(decision=risk_decision(signal_id=""))


def test_next_open_entry_rejects_non_tradeable_bar() -> None:
    print_bar = PrintBar(
        symbol="NVDA",
        date=NEXT_SESSION,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1_000_000.0,
    )

    with pytest.raises(DataValidationError, match="TradeableBar"):
        assess(bar=print_bar)


@pytest.mark.parametrize(
    ("expected_session", "bar_value", "message"),
    [
        (SIGNAL_AS_OF, tradeable_bar(date=SIGNAL_AS_OF), "must be after"),
        (date(2024, 6, 19), tradeable_bar(), "expected provider session"),
    ],
)
def test_next_open_entry_rejects_invalid_session_boundary(
    expected_session: date, bar_value: TradeableBar, message: str
) -> None:
    with pytest.raises(DataValidationError, match=message):
        assess(expected_session=expected_session, bar=bar_value)


@pytest.mark.parametrize(
    ("atr_20", "message"),
    [(Decimal("0"), "ATR20 must be positive"), (Decimal("NaN"), "non-finite ATR20")],
)
def test_next_open_entry_rejects_invalid_atr(atr_20: Decimal, message: str) -> None:
    with pytest.raises(DataValidationError, match=message):
        assess(atr_20=atr_20)


def test_next_open_entry_rejects_symbol_mismatch() -> None:
    with pytest.raises(DataValidationError, match="symbol mismatch"):
        assess(bar=tradeable_bar(symbol="MSFT"))


def test_next_open_entry_rejects_invalid_stop_band() -> None:
    stop = M6_CONFIG.stop.model_copy(
        update={"min_stop_distance_atr": 3.0, "max_stop_distance_atr": 2.5}
    )

    with pytest.raises(DataValidationError, match="min_stop_distance_atr"):
        assess(stop=stop)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_open_gap_atr", 0.0, "execution.max_open_gap_atr must be positive"),
        ("min_stop_distance_atr", 0.0, "stop.min_stop_distance_atr must be positive"),
    ],
)
def test_next_open_entry_rejects_non_positive_frozen_guard_config(
    field: str, value: float, message: str
) -> None:
    if field == "max_open_gap_atr":
        execution = M6_CONFIG.execution.model_copy(update={field: value})
        kwargs = {"execution": execution}
    else:
        stop = M6_CONFIG.stop.model_copy(update={field: value})
        kwargs = {"stop": stop}

    with pytest.raises(DataValidationError, match=message):
        assess(**kwargs)
