"""Immutable M7 RiskDecision audit-artifact contract."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from smm.core.errors import DataValidationError
from smm.domain.enums import MarketRegime, RiskVerdict
from smm.domain.models import RiskDecision
from smm.risk.artifacts import (
    render_risk_decisions_artifact,
    risk_decision_artifact_path,
    risk_decision_payload,
    write_risk_decisions_artifact,
)

AS_OF = date(2024, 6, 19)
STRATEGY_VERSION = "SMM-V1.1.0"
CONFIG_HASH = "a" * 64
CIRCUIT_STATE_IDENTITY = "b" * 64


def decision(**updates: object) -> RiskDecision:
    values: dict[str, object] = {
        "signal_id": "signal-001",
        "symbol": "AAA",
        "as_of": AS_OF,
        "strategy_version": STRATEGY_VERSION,
        "config_hash": CONFIG_HASH,
        "entry_risk_multiplier": Decimal("0.5000004"),
        "circuit_state_identity": CIRCUIT_STATE_IDENTITY,
        "verdict": RiskVerdict.ACCEPT,
        "reason_codes": ("risk_accept",),
        "quantity": 9,
        "entry_reference": Decimal("101.1234567"),
        "stop_reference": Decimal("95.5000004"),
        "unit_risk": Decimal("5.6234567"),
        "planned_capital": Decimal("910.1111117"),
        "planned_initial_risk": Decimal("50.6111107"),
        "sector": "technology",
        "risk_cluster": "software",
        "regime": MarketRegime.RISK_ON,
    }
    values.update(updates)
    return RiskDecision(**values)


def test_render_preserves_evaluation_order_and_all_canonical_fields() -> None:
    first = decision(signal_id="signal-002", symbol="BBB")
    second = decision(signal_id="signal-001", symbol="AAA")

    text = render_risk_decisions_artifact((first, second))
    payload = json.loads(text)

    assert isinstance(payload, list)
    assert [row["signal_id"] for row in payload] == ["signal-002", "signal-001"]
    assert payload[0] == {
        "signal_id": "signal-002",
        "symbol": "BBB",
        "as_of": "2024-06-19",
        "strategy_version": STRATEGY_VERSION,
        "config_hash": CONFIG_HASH,
        "entry_risk_multiplier": "0.500000",
        "circuit_state_identity": CIRCUIT_STATE_IDENTITY,
        "verdict": "accept",
        "reason_codes": ["risk_accept"],
        "quantity": 9,
        "entry_reference": "101.123457",
        "stop_reference": "95.500000",
        "unit_risk": "5.623457",
        "planned_capital": "910.111112",
        "planned_initial_risk": "50.611111",
        "sector": "technology",
        "risk_cluster": "software",
        "regime": "risk_on",
    }


def test_payload_preserves_rejection_reason_codes_and_plain_integer_quantity() -> None:
    rejected = decision(
        verdict=RiskVerdict.REJECT,
        reason_codes=("circuit_blocks_entries", "risk_budget_exhausted"),
        quantity=0,
        planned_capital=Decimal("0"),
        planned_initial_risk=Decimal("0"),
        regime=MarketRegime.RISK_OFF,
    )

    payload = risk_decision_payload(rejected)

    assert payload["verdict"] == "reject"
    assert payload["regime"] == "risk_off"
    assert payload["reason_codes"] == [
        "circuit_blocks_entries",
        "risk_budget_exhausted",
    ]
    assert payload["quantity"] == 0
    assert isinstance(payload["quantity"], int)


def test_writes_empty_batch_as_canonical_bare_array(tmp_path: Path) -> None:
    target = write_risk_decisions_artifact(tmp_path, AS_OF, ())

    assert target == risk_decision_artifact_path(tmp_path, AS_OF)
    assert target.read_text(encoding="utf-8") == "[]\n"


def test_exact_rerun_is_idempotent_even_after_manifest_exists(tmp_path: Path) -> None:
    decisions = (decision(),)
    target = write_risk_decisions_artifact(tmp_path, AS_OF, decisions)
    original = target.read_bytes()
    (target.parent / "manifest.json").write_text("{}\n", encoding="utf-8")

    assert write_risk_decisions_artifact(tmp_path, AS_OF, decisions) == target
    assert target.read_bytes() == original


def test_conflicting_batch_does_not_overwrite_existing_artifact(tmp_path: Path) -> None:
    target = write_risk_decisions_artifact(tmp_path, AS_OF, (decision(),))
    original = target.read_bytes()

    with pytest.raises(DataValidationError, match="conflicting risk decision artifact"):
        write_risk_decisions_artifact(
            tmp_path,
            AS_OF,
            (decision(symbol="ZZZ"),),
        )

    assert target.read_bytes() == original


def test_cannot_add_artifact_to_completed_session(tmp_path: Path) -> None:
    target = risk_decision_artifact_path(tmp_path, AS_OF)
    target.parent.mkdir(parents=True)
    (target.parent / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(DataValidationError, match="completed session"):
        write_risk_decisions_artifact(tmp_path, AS_OF, (decision(),))

    assert not target.exists()


def test_batch_rejects_a_decision_from_another_session(tmp_path: Path) -> None:
    later = decision(as_of=date(2024, 6, 20))

    with pytest.raises(DataValidationError, match="as_of must match artifact session"):
        write_risk_decisions_artifact(tmp_path, AS_OF, (later,))


def test_render_rejects_duplicate_signal_ids_within_one_batch() -> None:
    duplicate = decision(symbol="BBB")

    with pytest.raises(DataValidationError, match="cannot repeat signal_id"):
        render_risk_decisions_artifact((decision(), duplicate))


def test_render_rejects_mixed_circuit_state_identity_within_one_batch() -> None:
    different_circuit = decision(
        signal_id="signal-002",
        circuit_state_identity="c" * 64,
    )

    with pytest.raises(DataValidationError, match="batch identity mismatch"):
        render_risk_decisions_artifact((decision(), different_circuit))
