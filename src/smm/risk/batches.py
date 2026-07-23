"""Shared validation for deterministic RiskDecision batches."""

from __future__ import annotations

from collections.abc import Sequence

from smm.core.errors import DataValidationError
from smm.domain.models import RiskDecision


def validate_risk_decision_batch(
    decisions: Sequence[RiskDecision],
) -> tuple[RiskDecision, ...]:
    """Return one homogeneous RiskDecision batch in supplied evaluation order."""
    batch = tuple(decisions)
    if any(not isinstance(item, RiskDecision) for item in batch):
        raise DataValidationError("risk decision batch requires RiskDecision items")
    if len({item.signal_id for item in batch}) != len(batch):
        raise DataValidationError("risk decision batch cannot repeat signal_id")
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
