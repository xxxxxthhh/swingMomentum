"""Pure M6 circuit-state contract."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.domain.models import RiskExecutionContext
from smm.paper.circuits import (
    CircuitInputs,
    CircuitState,
    circuit_state_artifact_path,
    circuit_state_identity,
    circuit_state_payload,
    evaluate_circuit_state,
    render_circuit_state_artifact,
    risk_execution_context_for,
    write_circuit_state_artifact,
)

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


def test_circuit_state_identity_uses_the_adr_canonical_payload_and_digest() -> None:
    state = evaluate(inputs=inputs(marked_equity=Decimal("940")))

    payload = circuit_state_payload(state)

    assert payload == {
        "as_of": "2024-06-19",
        "strategy_version": M6_CONFIG.strategy.version,
        "config_hash": LOADED_CONFIG.config_hash,
        "realized_loss_r_for_session": "0.000000",
        "marked_equity": "940.000000",
        "high_water_equity": "1000.000000",
        "drawdown": "0.060000",
        "new_entries_blocked": False,
        "entry_risk_multiplier": "0.500000",
        "reason_codes": ["circuit_drawdown_reduce_risk"],
    }
    expected_json = (
        '{"as_of":"2024-06-19","config_hash":"'
        + LOADED_CONFIG.config_hash
        + '","drawdown":"0.060000","entry_risk_multiplier":"0.500000",'
        '"high_water_equity":"1000.000000","marked_equity":"940.000000",'
        '"new_entries_blocked":false,"realized_loss_r_for_session":"0.000000",'
        '"reason_codes":["circuit_drawdown_reduce_risk"],'
        '"strategy_version":"SMM-V1.1.0"}\n'
    )

    assert circuit_state_identity(state) == hashlib.sha256(
        expected_json.encode("utf-8")
    ).hexdigest()


def test_circuit_state_identity_normalizes_decimal_encodings_but_changes_with_facts() -> None:
    state = evaluate(inputs=inputs(marked_equity=Decimal("940")))
    equivalent_values = state.model_dump()
    equivalent_values["marked_equity"] = Decimal("940.000000")
    equivalent = CircuitState(**equivalent_values)

    changed_values = state.model_dump()
    changed_values["marked_equity"] = Decimal("939.999999")
    changed_values["drawdown"] = Decimal("0.060000001")
    changed = CircuitState(**changed_values)

    assert circuit_state_identity(equivalent) == circuit_state_identity(state)
    assert circuit_state_identity(changed) != circuit_state_identity(state)


def test_circuit_state_payload_uses_fixed_six_place_decimal_text() -> None:
    state = evaluate(
        inputs=inputs(realized_loss_r_for_session=Decimal("-0.1234567"))
    )

    assert circuit_state_payload(state)["realized_loss_r_for_session"] == "-0.123457"


def test_circuit_state_identity_ignores_ambient_decimal_context() -> None:
    state = evaluate(inputs=inputs(marked_equity=Decimal("940")))
    expected = circuit_state_identity(state)

    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        assert circuit_state_identity(state) == expected


def test_circuit_state_payload_preserves_frozen_reason_code_priority() -> None:
    state = evaluate(
        inputs=inputs(
            realized_loss_r_for_session=Decimal("-5"),
            marked_equity=Decimal("900"),
            integrity_halt=True,
        )
    )

    assert circuit_state_payload(state)["reason_codes"] == [
        "circuit_data_or_position_integrity_halt",
        "circuit_drawdown_stop_new_entries",
        "circuit_daily_loss_pause",
    ]


def test_circuit_state_identity_rejects_non_circuit_state_input() -> None:
    with pytest.raises(DataValidationError, match="CircuitState"):
        circuit_state_payload(None)

    with pytest.raises(DataValidationError, match="CircuitState"):
        circuit_state_identity(None)


@pytest.mark.parametrize(
    ("state", "blocked", "multiplier"),
    [
        (
            evaluate(inputs=inputs(realized_loss_r_for_session=Decimal("-4.01"))),
            True,
            Decimal("1"),
        ),
        (
            evaluate(inputs=inputs(marked_equity=Decimal("940"))),
            False,
            Decimal("0.5"),
        ),
        (
            evaluate(inputs=inputs(integrity_halt=True)),
            True,
            Decimal("0"),
        ),
    ],
    ids=("daily-loss-pause", "drawdown-reduce", "integrity-halt"),
)
def test_risk_execution_context_for_projects_all_circuit_tiers(
    state: CircuitState,
    blocked: bool,
    multiplier: Decimal,
) -> None:
    context = risk_execution_context_for(state)

    assert isinstance(context, RiskExecutionContext)
    assert context.as_of == state.as_of
    assert context.strategy_version == state.strategy_version
    assert context.config_hash == state.config_hash
    assert context.new_entries_blocked is blocked
    assert context.entry_risk_multiplier == multiplier
    assert context.circuit_state_identity == circuit_state_identity(state)


def test_risk_execution_context_for_rejects_non_circuit_state_input() -> None:
    with pytest.raises(DataValidationError, match="CircuitState"):
        risk_execution_context_for(None)


def test_circuit_state_artifact_is_canonical_and_idempotent(tmp_path: Path) -> None:
    state = evaluate(inputs=inputs(marked_equity=Decimal("940")))

    target = write_circuit_state_artifact(tmp_path, state)

    assert target == circuit_state_artifact_path(tmp_path, AS_OF)
    assert target == tmp_path / "2024-06-19" / "circuit_state.json"
    text = target.read_text(encoding="utf-8")
    assert text == render_circuit_state_artifact(state)
    assert text.endswith("\n")
    artifact = json.loads(text)
    assert artifact["circuit_state_identity"] == circuit_state_identity(state)
    assert {
        key: value for key, value in artifact.items() if key != "circuit_state_identity"
    } == circuit_state_payload(state)

    before = target.read_bytes()
    (target.parent / "manifest.json").write_text("{}\n", encoding="utf-8")
    assert write_circuit_state_artifact(tmp_path, state) == target
    assert target.read_bytes() == before


def test_circuit_state_artifact_conflict_does_not_overwrite_existing_file(
    tmp_path: Path,
) -> None:
    original = evaluate(inputs=inputs(marked_equity=Decimal("940")))
    target = write_circuit_state_artifact(tmp_path, original)
    before = target.read_bytes()
    changed = evaluate(inputs=inputs(marked_equity=Decimal("930")))

    with pytest.raises(DataValidationError, match="conflicting circuit state artifact"):
        write_circuit_state_artifact(tmp_path, changed)

    assert target.read_bytes() == before


def test_circuit_state_artifact_cannot_be_added_to_completed_session(
    tmp_path: Path,
) -> None:
    state = evaluate()
    day_dir = tmp_path / AS_OF.isoformat()
    day_dir.mkdir()
    (day_dir / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(DataValidationError, match="cannot add CircuitState artifact"):
        write_circuit_state_artifact(tmp_path, state)

    assert not circuit_state_artifact_path(tmp_path, AS_OF).exists()
