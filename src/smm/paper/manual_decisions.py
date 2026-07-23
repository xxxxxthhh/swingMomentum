"""Append-only M6 manual-SKIP audit facts.

This module records an operator veto derived from an accepted M5 risk decision.
It does not cancel a paper order, create a fill, mutate a position or balance,
or connect to task orchestration; M7 owns those integrations.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from smm.core.errors import DataValidationError
from smm.domain.enums import RiskVerdict
from smm.domain.models import RiskDecision


class ManualDecisionType(StrEnum):
    """The only V1 operator action: veto a risk-accepted entry."""

    SKIP = "skip"


class ManualDecision(BaseModel):
    """Immutable audit fact with the accepted ADR manual-decision business key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_id: str
    decision: ManualDecisionType
    as_of: date
    reason_code: str
    note: str | None = None
    actor: str
    strategy_version: str
    config_hash: str

    @field_validator("target_id", "reason_code", "actor", "strategy_version", "config_hash")
    @classmethod
    def required_text_is_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("manual decision required text fields must be non-empty")
        return value

    @field_validator("note")
    @classmethod
    def note_is_meaningful_when_present(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("manual decision note must be non-empty when present")
        return value

    @property
    def business_key(self) -> tuple[str, ManualDecisionType, date, str]:
        """ADR §6 key; identical audit replays are no-ops."""

        return (self.target_id, self.decision, self.as_of, self.config_hash)


class ManualSkipRequest(BaseModel):
    """Validated source fact for the only V1 manual action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    risk_decision: RiskDecision
    reason_code: str
    note: str | None = None
    actor: str

    @field_validator("reason_code", "actor")
    @classmethod
    def required_text_is_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("manual skip required text fields must be non-empty")
        return value

    @field_validator("note")
    @classmethod
    def note_is_meaningful_when_present(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("manual skip note must be non-empty when present")
        return value

    @model_validator(mode="after")
    def vetoes_only_an_accepted_risk_decision(self) -> ManualSkipRequest:
        if self.risk_decision.verdict is not RiskVerdict.ACCEPT:
            raise ValueError("manual skip requires an accepted RiskDecision")
        return self


_SCHEMA = pa.schema(
    [
        ("target_id", pa.string()),
        ("decision", pa.string()),
        ("as_of", pa.date32()),
        ("reason_code", pa.string()),
        ("note", pa.string()),
        ("actor", pa.string()),
        ("strategy_version", pa.string()),
        ("config_hash", pa.string()),
    ]
)
_DECISION_FIELDS = tuple(ManualDecision.model_fields)


def manual_decision_path(root: Path | str) -> Path:
    """Return the sole persistence location owned by this audit slice."""

    return Path(root) / "manual_decisions.parquet"


def read_manual_decisions(root: Path | str) -> list[ManualDecision]:
    """Read manual audit facts, rejecting corruption and duplicate business keys."""

    target = manual_decision_path(root)
    if not target.exists():
        return []
    try:
        table = pq.read_table(target)
    except Exception as exc:  # pyarrow error types depend on corruption shape.
        raise DataValidationError("cannot read manual decision ledger") from exc
    if table.schema != _SCHEMA:
        raise DataValidationError("unexpected manual decision ledger schema")

    decisions: list[ManualDecision] = []
    seen_keys: set[tuple[str, ManualDecisionType, date, str]] = set()
    for raw in table.to_pylist():
        try:
            decision = ManualDecision(
                **{name: raw.get(name) for name in _DECISION_FIELDS}
            )
        except (TypeError, ValidationError) as exc:
            raise DataValidationError("invalid manual decision ledger record") from exc
        if decision.business_key in seen_keys:
            raise DataValidationError("duplicate manual decision business key in ledger")
        seen_keys.add(decision.business_key)
        decisions.append(decision)
    return sorted(decisions, key=_decision_sort_key)


def append_manual_skips(
    root: Path | str, requests: Sequence[ManualSkipRequest]
) -> Path:
    """Atomically append accepted-risk SKIP facts; exact replays are no-ops."""

    target = manual_decision_path(root)
    incoming = _deduplicate_requests(requests)
    existing = read_manual_decisions(root)
    existing_by_key = {decision.business_key: decision for decision in existing}

    new_decisions: list[ManualDecision] = []
    for decision in incoming:
        previous = existing_by_key.get(decision.business_key)
        if previous is None:
            new_decisions.append(decision)
            continue
        if previous != decision:
            raise DataValidationError("conflicting manual decision business key")

    if new_decisions:
        _write_manual_decisions(target, [*existing, *new_decisions])
    return target


def _deduplicate_requests(
    requests: Sequence[ManualSkipRequest],
) -> list[ManualDecision]:
    by_key: dict[tuple[str, ManualDecisionType, date, str], ManualDecision] = {}
    for request in requests:
        if not isinstance(request, ManualSkipRequest):
            raise DataValidationError(
                "manual skip append requires ManualSkipRequest records"
            )
        decision = _decision_from_request(request)
        previous = by_key.get(decision.business_key)
        if previous is not None and previous != decision:
            raise DataValidationError("conflicting manual decision business key")
        by_key[decision.business_key] = decision
    return sorted(by_key.values(), key=_decision_sort_key)


def _decision_from_request(request: ManualSkipRequest) -> ManualDecision:
    risk = request.risk_decision
    return ManualDecision(
        target_id=risk.signal_id,
        decision=ManualDecisionType.SKIP,
        as_of=risk.as_of,
        reason_code=request.reason_code,
        note=request.note,
        actor=request.actor,
        strategy_version=risk.strategy_version,
        config_hash=risk.config_hash,
    )


def _write_manual_decisions(target: Path, decisions: Sequence[ManualDecision]) -> None:
    table = pa.Table.from_pylist(
        [_decision_row(decision) for decision in sorted(decisions, key=_decision_sort_key)],
        schema=_SCHEMA,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        pq.write_table(table, temporary, compression="snappy")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _decision_row(decision: ManualDecision) -> dict[str, object]:
    values = decision.model_dump()
    values["decision"] = decision.decision.value
    return values


def _decision_sort_key(decision: ManualDecision) -> tuple[date, str, str, str]:
    return (
        decision.as_of,
        decision.target_id,
        decision.decision.value,
        decision.config_hash,
    )
