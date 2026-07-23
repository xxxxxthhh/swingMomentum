"""M6 manual-SKIP audit ledger contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from smm.core.errors import DataValidationError
from smm.domain.enums import MarketRegime, RiskVerdict
from smm.domain.models import RiskDecision
from smm.paper.manual_decisions import (
    ManualDecisionType,
    ManualSkipRequest,
    append_manual_skips,
    manual_decision_path,
    read_manual_decisions,
)


def risk_decision(**updates: object) -> RiskDecision:
    values: dict[str, object] = {
        "signal_id": "signal-nvda-2024-06-18",
        "symbol": "NVDA",
        "as_of": date(2024, 6, 18),
        "strategy_version": "SMM-V1.1.0",
        "config_hash": "frozen-config-hash",
        "entry_risk_multiplier": Decimal("1"),
        "circuit_state_identity": "circuit-2024-06-18",
        "verdict": RiskVerdict.ACCEPT,
        "reason_codes": ("risk_sized_by_per_trade",),
        "quantity": 10,
        "entry_reference": Decimal("100"),
        "stop_reference": Decimal("90"),
        "unit_risk": Decimal("11"),
        "planned_capital": Decimal("1000"),
        "planned_initial_risk": Decimal("110"),
        "sector": "information_technology",
        "risk_cluster": "semiconductors",
        "regime": MarketRegime.RISK_ON,
    }
    values.update(updates)
    return RiskDecision(**values)


def manual_skip(**updates: object) -> ManualSkipRequest:
    values: dict[str, object] = {
        "risk_decision": risk_decision(),
        "reason_code": "manual_skip_event_risk",
        "note": "operator observed an event risk",
        "actor": "operator@example.com",
    }
    values.update(updates)
    return ManualSkipRequest(**values)


def test_append_manual_skip_is_idempotent_by_adr_business_key(tmp_path) -> None:
    request = manual_skip()

    target = append_manual_skips(tmp_path, [request])
    first_bytes = target.read_bytes()
    second_target = append_manual_skips(tmp_path, [request])
    decisions = read_manual_decisions(tmp_path)

    assert target == manual_decision_path(tmp_path)
    assert second_target == target
    assert target.read_bytes() == first_bytes
    assert len(decisions) == 1
    assert decisions[0].target_id == request.risk_decision.signal_id
    assert decisions[0].decision is ManualDecisionType.SKIP
    assert decisions[0].as_of == request.risk_decision.as_of
    assert decisions[0].business_key == (
        "signal-nvda-2024-06-18",
        ManualDecisionType.SKIP,
        date(2024, 6, 18),
        "frozen-config-hash",
    )


def test_same_manual_skip_business_key_with_different_payload_fails_closed(tmp_path) -> None:
    append_manual_skips(tmp_path, [manual_skip()])

    with pytest.raises(DataValidationError, match="conflicting manual decision"):
        append_manual_skips(tmp_path, [manual_skip(note="different operator note")])


def test_same_manual_skip_batch_conflict_fails_before_writing_any_decision(tmp_path) -> None:
    with pytest.raises(DataValidationError, match="conflicting manual decision"):
        append_manual_skips(
            tmp_path,
            [manual_skip(), manual_skip(actor="another-operator@example.com")],
        )

    assert not manual_decision_path(tmp_path).exists()


def test_manual_skip_requires_an_accepted_risk_decision() -> None:
    rejected = risk_decision(
        verdict=RiskVerdict.REJECT,
        quantity=0,
        planned_capital=Decimal("0"),
        planned_initial_risk=Decimal("0"),
    )

    with pytest.raises(ValidationError, match="accepted RiskDecision"):
        manual_skip(risk_decision=rejected)


def test_unknown_manual_decision_ledger_column_fails_closed(tmp_path) -> None:
    target = append_manual_skips(tmp_path, [manual_skip()])
    tampered = pq.read_table(target).append_column("unexpected", pa.array(["value"]))
    pq.write_table(tampered, target)

    with pytest.raises(DataValidationError, match="unexpected manual decision ledger schema"):
        read_manual_decisions(tmp_path)


def test_duplicate_stored_manual_decision_business_key_fails_closed(tmp_path) -> None:
    target = append_manual_skips(tmp_path, [manual_skip()])
    stored = pq.read_table(target)
    pq.write_table(pa.concat_tables([stored, stored]), target)

    with pytest.raises(
        DataValidationError, match="duplicate manual decision business key in ledger"
    ):
        read_manual_decisions(tmp_path)


def test_malformed_stored_manual_decision_fails_closed(tmp_path) -> None:
    target = append_manual_skips(tmp_path, [manual_skip()])
    stored = pq.read_table(target)
    tampered = stored.set_column(
        stored.schema.get_field_index("actor"),
        "actor",
        pa.array([""], type=pa.string()),
    )
    pq.write_table(tampered, target)

    with pytest.raises(DataValidationError, match="invalid manual decision ledger record"):
        read_manual_decisions(tmp_path)
