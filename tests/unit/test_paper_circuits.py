"""Pure M6 circuit-state contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.paper.circuits import CircuitInputs, CircuitState, evaluate_circuit_state

REPO = Path(__file__).resolve().parents[2]
LOADED_CONFIG = load_config(REPO / "configs" / "smm_v1_1_0.yaml")
M6_CONFIG = LOADED_CONFIG.config
V1_CONFIG = load_config(REPO / "configs" / "smm_v1_0_0.yaml").config
AS_OF = date(2024, 6, 19)


def inputs(**updates: object) -> CircuitInputs:
    values: dict[str, object] = {
        "as_of": AS_OF,
        "strategy_version": M6_CONFIG.strategy.version,
        "config_hash": LOADED_CONFIG.config_hash,
        "realized_loss_r_for_session": Decimal("0"),
        "marked_equity": Decimal("1000"),
        "prior_high_water_equity": Decimal("1000"),
        "integrity_halt": False,
    }
    values.update(updates)
    return CircuitInputs(**values)


def evaluate(**updates: object) -> CircuitState:
    values: dict[str, object] = {
        "inputs": inputs(),
        "risk": M6_CONFIG.risk,
    }
    values.update(updates)
    return evaluate_circuit_state(**values)


def test_normal_state_preserves_high_water_and_has_no_reason_code() -> None:
    result = evaluate()

    assert result.marked_equity == Decimal("1000")
    assert result.high_water_equity == Decimal("1000")
    assert result.drawdown == Decimal("0")
    assert result.new_entries_blocked is False
    assert result.entry_risk_multiplier == Decimal("1")
    assert result.reason_codes == ()


def test_daily_loss_equal_to_threshold_does_not_pause_new_entries() -> None:
    result = evaluate(
        inputs=inputs(realized_loss_r_for_session=Decimal("-4")),
    )

    assert result.new_entries_blocked is False
    assert result.reason_codes == ()


def test_daily_loss_below_threshold_pauses_new_entries_next_session() -> None:
    result = evaluate(
        inputs=inputs(realized_loss_r_for_session=Decimal("-4.01")),
    )

    assert result.new_entries_blocked is True
    assert result.entry_risk_multiplier == Decimal("1")
    assert result.reason_codes == ("circuit_daily_loss_pause",)


def test_new_marked_equity_raises_high_water_without_drawdown() -> None:
    result = evaluate(
        inputs=inputs(
            marked_equity=Decimal("1200"),
            prior_high_water_equity=Decimal("1000"),
        ),
    )

    assert result.high_water_equity == Decimal("1200")
    assert result.drawdown == Decimal("0")


def test_drawdown_at_reduce_threshold_halves_entry_risk_without_blocking() -> None:
    result = evaluate(
        inputs=inputs(marked_equity=Decimal("940")),
    )

    assert result.drawdown == Decimal("0.06")
    assert result.new_entries_blocked is False
    assert result.entry_risk_multiplier == Decimal("0.5")
    assert result.reason_codes == ("circuit_drawdown_reduce_risk",)


def test_drawdown_at_stop_threshold_blocks_new_entries_without_reduce_reason() -> None:
    result = evaluate(
        inputs=inputs(marked_equity=Decimal("900")),
    )

    assert result.drawdown == Decimal("0.1")
    assert result.new_entries_blocked is True
    assert result.entry_risk_multiplier == Decimal("0")
    assert result.reason_codes == ("circuit_drawdown_stop_new_entries",)


def test_integrity_halt_has_highest_priority_and_retains_other_applicable_reasons() -> None:
    result = evaluate(
        inputs=inputs(
            realized_loss_r_for_session=Decimal("-5"),
            marked_equity=Decimal("900"),
            integrity_halt=True,
        ),
    )

    assert result.new_entries_blocked is True
    assert result.entry_risk_multiplier == Decimal("0")
    assert result.reason_codes == (
        "circuit_data_or_position_integrity_halt",
        "circuit_drawdown_stop_new_entries",
        "circuit_daily_loss_pause",
    )


def test_integrity_halt_zeroes_risk_even_when_only_reduce_threshold_applies() -> None:
    result = evaluate(
        inputs=inputs(
            marked_equity=Decimal("940"),
            integrity_halt=True,
        ),
    )

    assert result.entry_risk_multiplier == Decimal("0")
    assert result.reason_codes == (
        "circuit_data_or_position_integrity_halt",
        "circuit_drawdown_reduce_risk",
    )


def test_daily_loss_pause_and_drawdown_reduction_retain_both_reasons() -> None:
    result = evaluate(
        inputs=inputs(
            realized_loss_r_for_session=Decimal("-5"),
            marked_equity=Decimal("940"),
        ),
    )

    assert result.new_entries_blocked is True
    assert result.entry_risk_multiplier == Decimal("0.5")
    assert result.reason_codes == (
        "circuit_daily_loss_pause",
        "circuit_drawdown_reduce_risk",
    )


def test_circuit_rejects_v1_config_without_frozen_thresholds() -> None:
    with pytest.raises(DataValidationError, match="missing frozen M6 circuit threshold"):
        evaluate(risk=V1_CONFIG.risk)


def test_circuit_rejects_non_finite_frozen_threshold() -> None:
    risk = M6_CONFIG.risk.model_copy(
        update={"drawdown_reduce_at": Decimal("NaN")}
    )

    with pytest.raises(DataValidationError, match="non-finite M6 circuit threshold"):
        evaluate(risk=risk)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "drawdown_reduce_at",
            Decimal("0.10"),
            "risk.drawdown_reduce_at must be < risk.drawdown_stop_at",
        ),
        (
            "drawdown_stop_at",
            Decimal("1"),
            "drawdown circuit thresholds must be < 1",
        ),
    ],
)
def test_circuit_rejects_invalid_drawdown_threshold_contract(
    field: str, value: Decimal, message: str
) -> None:
    risk = M6_CONFIG.risk.model_copy(update={field: value})

    with pytest.raises(DataValidationError, match=message):
        evaluate(risk=risk)


def test_circuit_rejects_non_risk_section() -> None:
    with pytest.raises(DataValidationError, match="RiskSection"):
        evaluate(risk=None)


def test_circuit_state_rejects_drawdown_inconsistent_with_equity_facts() -> None:
    result = evaluate()
    values = result.model_dump()
    values["drawdown"] = Decimal("0.01")

    with pytest.raises(ValidationError, match="drawdown must match equity facts"):
        CircuitState(**values)


def test_circuit_state_rejects_stop_and_reduce_reasons_together() -> None:
    result = evaluate()
    values = result.model_dump()
    values["reason_codes"] = (
        "circuit_drawdown_stop_new_entries",
        "circuit_drawdown_reduce_risk",
    )

    with pytest.raises(ValidationError, match="stop and reduction cannot both apply"):
        CircuitState(**values)


def test_circuit_state_rejects_reason_codes_out_of_stable_priority_order() -> None:
    result = evaluate()
    values = result.model_dump()
    values["reason_codes"] = (
        "circuit_drawdown_stop_new_entries",
        "circuit_data_or_position_integrity_halt",
    )

    with pytest.raises(ValidationError, match="stable priority order"):
        CircuitState(**values)


def test_circuit_state_rejects_block_flag_inconsistent_with_stop_reason() -> None:
    result = evaluate()
    values = result.model_dump()
    values["new_entries_blocked"] = False
    values["reason_codes"] = ("circuit_drawdown_stop_new_entries",)

    with pytest.raises(ValidationError, match="entry block must match circuit reasons"):
        CircuitState(**values)
