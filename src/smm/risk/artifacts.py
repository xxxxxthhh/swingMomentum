"""Canonical immutable M7 audit artifacts for M5 RiskDecision batches.

This is deliberately a pure render/write seam. It neither evaluates risk nor
changes the daily runtime, manifest assembly, paper ledger, or transitions.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from smm.core.errors import DataValidationError
from smm.domain.models import RiskDecision
from smm.report.format import dump_json_deterministic, format_decimal

_RISK_DECISIONS_ARTIFACT_NAME = "risk_decisions.json"
_MANIFEST_NAME = "manifest.json"


def risk_decision_payload(
    decision: RiskDecision,
) -> dict[str, str | int | list[str]]:
    """Return all RiskDecision facts in the canonical audit encoding."""
    if not isinstance(decision, RiskDecision):
        raise DataValidationError("risk decision artifact requires RiskDecision")
    return {
        "signal_id": decision.signal_id,
        "symbol": decision.symbol,
        "as_of": decision.as_of.isoformat(),
        "strategy_version": decision.strategy_version,
        "config_hash": decision.config_hash,
        "entry_risk_multiplier": format_decimal(decision.entry_risk_multiplier),
        "circuit_state_identity": decision.circuit_state_identity,
        "verdict": decision.verdict.value,
        "reason_codes": list(decision.reason_codes),
        "quantity": decision.quantity,
        "entry_reference": format_decimal(decision.entry_reference),
        "stop_reference": format_decimal(decision.stop_reference),
        "unit_risk": format_decimal(decision.unit_risk),
        "planned_capital": format_decimal(decision.planned_capital),
        "planned_initial_risk": format_decimal(decision.planned_initial_risk),
        "sector": decision.sector,
        "risk_cluster": decision.risk_cluster,
        "regime": decision.regime.value,
    }


def risk_decision_artifact_path(root: Path | str, as_of: date) -> Path:
    """Return the fixed per-session RiskDecision audit-artifact path."""
    if not isinstance(as_of, date):
        raise DataValidationError("risk decision artifact as_of requires a date")
    return Path(root) / as_of.isoformat() / _RISK_DECISIONS_ARTIFACT_NAME


def render_risk_decisions_artifact(decisions: Sequence[RiskDecision]) -> str:
    """Render one bare JSON array in the supplied risk-evaluation order."""
    batch = _validated_batch(decisions)
    return dump_json_deterministic([risk_decision_payload(item) for item in batch])


def write_risk_decisions_artifact(
    root: Path | str,
    as_of: date,
    decisions: Sequence[RiskDecision],
) -> Path:
    """Create the immutable RiskDecision artifact for one completed session.

    An exact rerun is a no-op. A different payload or an attempt to append an
    absent artifact after ``manifest.json`` exists fails closed, so a completed
    session cannot silently change shape.
    """
    if not isinstance(as_of, date):
        raise DataValidationError("risk decision artifact as_of requires a date")

    batch = _validated_batch(decisions)
    if any(item.as_of != as_of for item in batch):
        raise DataValidationError("risk decision as_of must match artifact session")

    target = risk_decision_artifact_path(root, as_of)
    text = dump_json_deterministic([risk_decision_payload(item) for item in batch])
    if target.exists():
        _accept_or_reject_existing_risk_artifact(target, text)
        return target

    manifest_file = target.parent / _MANIFEST_NAME
    if manifest_file.exists():
        raise DataValidationError(
            "cannot add RiskDecision artifact to completed session "
            f"{as_of.isoformat()}; reruns must preserve manifest shape"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    _create_risk_artifact(target, text)
    return target


def _validated_batch(decisions: Sequence[RiskDecision]) -> tuple[RiskDecision, ...]:
    batch = tuple(decisions)
    if any(not isinstance(item, RiskDecision) for item in batch):
        raise DataValidationError("risk decision artifact requires RiskDecision items")
    if len({item.signal_id for item in batch}) != len(batch):
        raise DataValidationError("risk decision artifact cannot repeat signal_id")
    if not batch:
        return batch

    first = batch[0]
    expected_identity = (
        first.as_of,
        first.strategy_version,
        first.config_hash,
        first.circuit_state_identity,
    )
    if any(
        (
            item.as_of,
            item.strategy_version,
            item.config_hash,
            item.circuit_state_identity,
        )
        != expected_identity
        for item in batch[1:]
    ):
        raise DataValidationError("risk decision batch identity mismatch")
    return batch


def _create_risk_artifact(target: Path, text: str) -> None:
    """Atomically create a new target without replacing a concurrent artifact."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temporary, target)
        except FileExistsError:
            _accept_or_reject_existing_risk_artifact(target, text)
    finally:
        temporary.unlink(missing_ok=True)


def _accept_or_reject_existing_risk_artifact(target: Path, text: str) -> None:
    if target.read_text(encoding="utf-8") != text:
        raise DataValidationError(
            f"conflicting risk decision artifact already exists for {target.parent.name}"
        )
