"""M6 circuit-state assessment with narrow M7 artifact persistence.

This module derives an auditable operational state from already-computed equity,
realized-R, and integrity facts. Its M7 helper can persist only the canonical,
immutable per-session audit artifact. It does not edit frozen config, mutate a
risk decision, write a ledger, or orchestrate a daily task.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from smm.config.schema import RiskSection
from smm.core.errors import DataValidationError
from smm.domain.models import RiskExecutionContext
from smm.report.format import dump_json_deterministic, format_decimal

_ZERO = Decimal(0)
_ONE = Decimal(1)
_HALF = Decimal("0.5")
_CIRCUIT_STATE_ARTIFACT_NAME = "circuit_state.json"
_MANIFEST_NAME = "manifest.json"

_INTEGRITY_HALT = "circuit_data_or_position_integrity_halt"
_DRAWDOWN_STOP = "circuit_drawdown_stop_new_entries"
_DAILY_LOSS_PAUSE = "circuit_daily_loss_pause"
_DRAWDOWN_REDUCE = "circuit_drawdown_reduce_risk"
_REASON_PRIORITY = (
    _INTEGRITY_HALT,
    _DRAWDOWN_STOP,
    _DAILY_LOSS_PAUSE,
    _DRAWDOWN_REDUCE,
)


class CircuitInputs(BaseModel):
    """Immutable facts needed to derive one M6 operational circuit state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: date
    strategy_version: str
    config_hash: str
    realized_loss_r_for_session: Decimal
    marked_equity: Decimal = Field(ge=_ZERO)
    prior_high_water_equity: Decimal = Field(gt=_ZERO)
    integrity_halt: StrictBool

    @field_validator("strategy_version", "config_hash")
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("circuit input identity fields must be non-empty")
        return value

    @field_validator(
        "realized_loss_r_for_session",
        "marked_equity",
        "prior_high_water_equity",
    )
    @classmethod
    def decimal_facts_are_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("circuit input Decimal facts must be finite")
        return value


class CircuitState(BaseModel):
    """Replayable M6 state that affects only later new-entry decisions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: date
    strategy_version: str
    config_hash: str
    realized_loss_r_for_session: Decimal
    marked_equity: Decimal = Field(ge=_ZERO)
    high_water_equity: Decimal = Field(gt=_ZERO)
    drawdown: Decimal = Field(ge=_ZERO, le=_ONE)
    new_entries_blocked: StrictBool
    entry_risk_multiplier: Decimal = Field(ge=_ZERO, le=_ONE)
    reason_codes: tuple[str, ...] = ()

    @field_validator("strategy_version", "config_hash")
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("circuit state identity fields must be non-empty")
        return value

    @field_validator(
        "realized_loss_r_for_session",
        "marked_equity",
        "high_water_equity",
        "drawdown",
        "entry_risk_multiplier",
    )
    @classmethod
    def decimal_facts_are_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("circuit state Decimal facts must be finite")
        return value

    @field_validator("reason_codes")
    @classmethod
    def reason_codes_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("circuit reason codes must be unique")
        if any(code not in _REASON_PRIORITY for code in value):
            raise ValueError("circuit reason codes must be recognized")
        expected = tuple(code for code in _REASON_PRIORITY if code in value)
        if value != expected:
            raise ValueError("circuit reason codes must use stable priority order")
        if _DRAWDOWN_STOP in value and _DRAWDOWN_REDUCE in value:
            raise ValueError("drawdown stop and reduction cannot both apply")
        return value

    @model_validator(mode="after")
    def preserves_circuit_contract(self) -> CircuitState:
        if self.marked_equity > self.high_water_equity:
            raise ValueError("circuit high water must not trail marked equity")
        expected_drawdown = (
            self.high_water_equity - self.marked_equity
        ) / self.high_water_equity
        if self.drawdown != expected_drawdown:
            raise ValueError("circuit drawdown must match equity facts")

        blocking_reasons = {
            _INTEGRITY_HALT,
            _DRAWDOWN_STOP,
            _DAILY_LOSS_PAUSE,
        }
        if self.new_entries_blocked != bool(blocking_reasons & set(self.reason_codes)):
            raise ValueError("circuit entry block must match circuit reasons")
        if _INTEGRITY_HALT in self.reason_codes or _DRAWDOWN_STOP in self.reason_codes:
            if self.entry_risk_multiplier != _ZERO:
                raise ValueError("integrity halt or drawdown stop must zero entry risk")
        elif _DRAWDOWN_REDUCE in self.reason_codes:
            if self.entry_risk_multiplier != _HALF:
                raise ValueError("drawdown reduction must halve entry risk")
        elif self.entry_risk_multiplier != _ONE:
            raise ValueError("normal or daily-loss-only circuit must retain entry risk")
        return self


def evaluate_circuit_state(
    inputs: CircuitInputs,
    *,
    risk: RiskSection,
) -> CircuitState:
    """Derive the accepted M6 circuit state without mutating frozen config.

    The caller supplies end-of-session realized loss and marked-equity facts.
    A resulting daily-loss pause is consumed by the later next-session entry
    orchestration seam; this function neither decides quantity nor writes state.
    """
    if not isinstance(inputs, CircuitInputs):
        raise DataValidationError("circuit assessment requires CircuitInputs")
    if not isinstance(risk, RiskSection):
        raise DataValidationError("circuit assessment requires RiskSection")

    daily_loss_pause = _required_threshold(
        risk.daily_loss_pause_r,
        label="risk.daily_loss_pause_r",
    )
    drawdown_reduce = _required_threshold(
        risk.drawdown_reduce_at,
        label="risk.drawdown_reduce_at",
    )
    drawdown_stop = _required_threshold(
        risk.drawdown_stop_at,
        label="risk.drawdown_stop_at",
    )
    if drawdown_reduce >= _ONE or drawdown_stop >= _ONE:
        raise DataValidationError("drawdown circuit thresholds must be < 1")
    if drawdown_reduce >= drawdown_stop:
        raise DataValidationError(
            "risk.drawdown_reduce_at must be < risk.drawdown_stop_at"
        )

    high_water_equity = max(inputs.prior_high_water_equity, inputs.marked_equity)
    drawdown = (high_water_equity - inputs.marked_equity) / high_water_equity
    drawdown_stop_applies = drawdown >= drawdown_stop
    drawdown_reduce_applies = (
        drawdown >= drawdown_reduce and not drawdown_stop_applies
    )
    daily_loss_applies = inputs.realized_loss_r_for_session < -daily_loss_pause

    reason_codes = tuple(
        code
        for code, applies in (
            (_INTEGRITY_HALT, inputs.integrity_halt),
            (_DRAWDOWN_STOP, drawdown_stop_applies),
            (_DAILY_LOSS_PAUSE, daily_loss_applies),
            (_DRAWDOWN_REDUCE, drawdown_reduce_applies),
        )
        if applies
    )
    new_entries_blocked = any(
        code in reason_codes
        for code in (_INTEGRITY_HALT, _DRAWDOWN_STOP, _DAILY_LOSS_PAUSE)
    )
    if _INTEGRITY_HALT in reason_codes or _DRAWDOWN_STOP in reason_codes:
        entry_risk_multiplier = _ZERO
    elif _DRAWDOWN_REDUCE in reason_codes:
        entry_risk_multiplier = _HALF
    else:
        entry_risk_multiplier = _ONE

    return CircuitState(
        as_of=inputs.as_of,
        strategy_version=inputs.strategy_version,
        config_hash=inputs.config_hash,
        realized_loss_r_for_session=inputs.realized_loss_r_for_session,
        marked_equity=inputs.marked_equity,
        high_water_equity=high_water_equity,
        drawdown=drawdown,
        new_entries_blocked=new_entries_blocked,
        entry_risk_multiplier=entry_risk_multiplier,
        reason_codes=reason_codes,
    )


def circuit_state_payload(state: CircuitState) -> dict[str, str | bool | list[str]]:
    """Return M7's complete, canonical audit payload for one CircuitState.

    The payload intentionally contains no source path, wall clock, or mutable
    config object. Callers can persist it next to its digest and later recompute
    both to prove that the circuit facts have not changed.
    """
    if not isinstance(state, CircuitState):
        raise DataValidationError("circuit identity requires CircuitState")
    return {
        "as_of": state.as_of.isoformat(),
        "strategy_version": state.strategy_version,
        "config_hash": state.config_hash,
        "realized_loss_r_for_session": format_decimal(
            state.realized_loss_r_for_session
        ),
        "marked_equity": format_decimal(state.marked_equity),
        "high_water_equity": format_decimal(state.high_water_equity),
        "drawdown": format_decimal(state.drawdown),
        "new_entries_blocked": state.new_entries_blocked,
        "entry_risk_multiplier": format_decimal(
            state.entry_risk_multiplier
        ),
        "reason_codes": list(state.reason_codes),
    }


def circuit_state_identity(state: CircuitState) -> str:
    """Return the SHA-256 identity of M7's canonical CircuitState payload."""
    payload_text = dump_json_deterministic(circuit_state_payload(state))
    return hashlib.sha256(payload_text.encode("utf-8")).hexdigest()


def risk_execution_context_for(state: CircuitState) -> RiskExecutionContext:
    """Project one canonical CircuitState into M7's immutable risk input.

    This pure seam permits no caller overrides, so every mapped fact and the
    canonical digest remain bound to the same validated CircuitState.
    """
    if not isinstance(state, CircuitState):
        raise DataValidationError("risk execution context requires CircuitState")
    return RiskExecutionContext(
        as_of=state.as_of,
        strategy_version=state.strategy_version,
        config_hash=state.config_hash,
        entry_risk_multiplier=state.entry_risk_multiplier,
        circuit_state_identity=circuit_state_identity(state),
        new_entries_blocked=state.new_entries_blocked,
    )


def circuit_state_artifact_path(root: Path | str, as_of: date) -> Path:
    """Return the canonical M7 session-artifact location for ``as_of``.

    ``root`` is the already-selected strategy-version/config-hash artifact
    root. The M7 orchestrator owns mode selection; this pure seam only fixes
    the per-session filename so shadow-mode assembly cannot invent variants.
    """
    if not isinstance(as_of, date):
        raise DataValidationError("circuit artifact as_of requires a date")
    return Path(root) / as_of.isoformat() / _CIRCUIT_STATE_ARTIFACT_NAME


def render_circuit_state_artifact(state: CircuitState) -> str:
    """Render a complete, deterministic CircuitState audit artifact.

    The digest is intentionally outside the payload it authenticates: removing
    ``circuit_state_identity`` and serializing the remaining keys with the
    shared formatter reproduces the M7 identity exactly.
    """
    payload = circuit_state_payload(state)
    return dump_json_deterministic(
        {
            **payload,
            "circuit_state_identity": circuit_state_identity(state),
        }
    )


def write_circuit_state_artifact(root: Path | str, state: CircuitState) -> Path:
    """Create one immutable canonical CircuitState artifact for a session.

    An exact rerun is a no-op. A changed payload at the same session location,
    or an attempt to add the artifact after a session manifest exists, fails
    closed. The latter prevents an already-completed M4 bundle from silently
    growing into a shadow-mode result.
    """
    if not isinstance(state, CircuitState):
        raise DataValidationError("circuit artifact requires CircuitState")

    target = circuit_state_artifact_path(root, state.as_of)
    text = render_circuit_state_artifact(state)
    if target.exists():
        _accept_or_reject_existing_circuit_artifact(target, text)
        return target

    manifest_file = target.parent / _MANIFEST_NAME
    if manifest_file.exists():
        raise DataValidationError(
            "cannot add CircuitState artifact to completed session "
            f"{state.as_of.isoformat()}; reruns must preserve manifest shape"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    _create_circuit_artifact(target, text)
    return target


def _create_circuit_artifact(target: Path, text: str) -> None:
    """Atomically create ``target`` without replacing a concurrent artifact."""
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
            _accept_or_reject_existing_circuit_artifact(target, text)
    finally:
        temporary.unlink(missing_ok=True)


def _accept_or_reject_existing_circuit_artifact(target: Path, text: str) -> None:
    if target.read_text(encoding="utf-8") != text:
        raise DataValidationError(
            f"conflicting circuit state artifact already exists for {target.parent.name}"
        )


def _required_threshold(value: object, *, label: str) -> Decimal:
    if value is None:
        raise DataValidationError(f"missing frozen M6 circuit threshold: {label}")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DataValidationError(f"invalid M6 circuit threshold: {label}") from exc
    if not decimal.is_finite():
        raise DataValidationError(f"non-finite M6 circuit threshold: {label}")
    if decimal <= _ZERO:
        raise DataValidationError(f"M6 circuit threshold must be positive: {label}")
    return decimal
