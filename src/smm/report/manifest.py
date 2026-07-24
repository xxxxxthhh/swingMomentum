"""Completion manifest assembly (M4 ADR §3/§6/§8).

Pure data assembly only -- computing artifact SHA-256s and discovering the
git commit both need the actual files/repo on disk, which is the daily-task
orchestrator's job (§6: manifest is written last, after the bundle is
already on disk). This module only fixes the manifest's *shape* and
guarantees nothing here can smuggle in a wall-clock value, a random run id,
or an absolute temp path -- the whole point of writing this before the
orchestrator exists.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from typing import Any

from smm.core.errors import DataValidationError
from smm.domain.enums import MarketRegime
from smm.report.format import dump_json_deterministic


class ExecutionMode(StrEnum):
    """Explicit M7 operation choice recorded in a session manifest."""

    MVP_A_SIGNAL = "mvp_a_signal"
    SHADOW = "shadow"
    PAPER = "paper"

# §8: git_commit may be excluded from a byte-comparison only when the two
# compared roots are legitimately at different tree SHAs -- and only the
# comparison logic (not this manifest) may decide that. Declaring it here
# is the explicit opt-in §8 requires; comparing manifests at equal tree SHA
# must ignore this list and include git_commit anyway.
CONDITIONALLY_EXCLUDED_FIELDS = ("git_commit",)
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SHADOW_ARTIFACT_KEYS = (
    "report_csv",
    "report_markdown",
    "features_snapshot",
    "portfolio_snapshot",
    "circuit_state",
    "risk_decisions",
    "market_data_verifications",
)


def build_manifest(
    *,
    as_of: date,
    strategy_version: str,
    config_hash: str,
    regime: MarketRegime,
    provider_source: str,
    universe_snapshot_id: str,
    git_commit: str,
    transition_batch: dict[str, Any],
    artifact_hashes: dict[str, str],
    market_event_snapshot: Mapping[str, str] | None = None,
    market_data_snapshots: Mapping[str, Mapping[str, str]] | None = None,
    execution_mode: ExecutionMode | str = ExecutionMode.MVP_A_SIGNAL,
) -> dict[str, Any]:
    """Assemble the manifest payload. No wall-clock, run id, or temp path
    field exists anywhere in this shape -- that is enforced by omission,
    not by a runtime check, so there is nothing here to accidentally leak.
    """
    selected_mode = _execution_mode(execution_mode)
    manifest = {
        "as_of": as_of.isoformat(),
        "strategy_version": strategy_version,
        "config_hash": config_hash,
        "execution_mode": selected_mode.value,
        "regime": regime.value,
        "provider_source": provider_source,
        "universe_snapshot_id": universe_snapshot_id,
        "git_commit": git_commit,
        "transition_batch": transition_batch,
        "artifacts": artifact_hashes,
        "market_event_snapshot": (
            _validated_market_event_snapshot(market_event_snapshot)
            if market_event_snapshot is not None
            else None
        ),
        "reproduction_contract": {
            "conditionally_excluded_fields": list(CONDITIONALLY_EXCLUDED_FIELDS),
        },
    }
    if market_data_snapshots:
        manifest["market_data_snapshots"] = _validated_market_data_snapshots(
            market_data_snapshots
        )
    return manifest


def build_shadow_manifest(
    *,
    as_of: date,
    strategy_version: str,
    config_hash: str,
    regime: MarketRegime,
    provider_source: str,
    universe_snapshot_id: str,
    git_commit: str,
    transition_batch: dict[str, Any],
    artifact_hashes: Mapping[str, object],
    circuit_state_identity: object,
    market_event_snapshot: Mapping[str, str] | None = None,
    market_data_snapshots: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Assemble the strict, pure M7 shadow-manifest payload.

    This keeps the byte-stable M4 builder unchanged while fixing the complete
    shadow replay shape. Artifact writers own byte creation; this seam only
    requires their resulting SHA-256 values and the circuit identity.
    """
    manifest = build_manifest(
        as_of=as_of,
        strategy_version=strategy_version,
        config_hash=config_hash,
        regime=regime,
        provider_source=provider_source,
        universe_snapshot_id=universe_snapshot_id,
        git_commit=git_commit,
        transition_batch=transition_batch,
        artifact_hashes=_validated_shadow_artifact_hashes(artifact_hashes),
        execution_mode=ExecutionMode.SHADOW,
        market_event_snapshot=market_event_snapshot,
        market_data_snapshots=market_data_snapshots,
    )
    manifest["circuit_state_identity"] = _validated_sha256(
        circuit_state_identity,
        label="circuit_state_identity",
    )
    return manifest


def render_manifest(manifest: dict[str, Any]) -> str:
    return dump_json_deterministic(manifest)


def _execution_mode(value: object) -> ExecutionMode:
    if not isinstance(value, str):
        raise DataValidationError("execution mode must be a supported string")
    try:
        return ExecutionMode(value)
    except ValueError as exc:
        raise DataValidationError(f"unsupported execution mode: {value!r}") from exc


def _validated_shadow_artifact_hashes(
    artifact_hashes: Mapping[str, object],
) -> dict[str, str]:
    if not isinstance(artifact_hashes, Mapping):
        raise DataValidationError("shadow manifest artifact hashes must be a mapping")
    if set(artifact_hashes) != set(_SHADOW_ARTIFACT_KEYS):
        raise DataValidationError(
            "shadow manifest artifact keys must be exactly "
            f"{', '.join(_SHADOW_ARTIFACT_KEYS)}"
        )
    return {
        key: _validated_sha256(artifact_hashes[key], label=f"artifact hash for {key}")
        for key in _SHADOW_ARTIFACT_KEYS
    }


def _validated_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise DataValidationError(f"{label} must be a 64-character lowercase SHA-256 hex")
    return value


def _validated_market_event_snapshot(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"id", "sha256"}:
        raise DataValidationError(
            "market_event_snapshot must contain exactly id and sha256"
        )
    snapshot_id = value["id"]
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise DataValidationError("market_event_snapshot id must be non-empty")
    return {
        "id": snapshot_id,
        "sha256": _validated_sha256(
            value["sha256"],
            label="market_event_snapshot sha256",
        ),
    }


def _validated_market_data_snapshots(
    value: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping) or not value:
        raise DataValidationError("market_data_snapshots must be a non-empty mapping")
    allowed = {"price_event", "security_identity", "volume_event"}
    if not set(value).issubset(allowed):
        raise DataValidationError(
            "market_data_snapshots contains an unsupported snapshot kind"
        )
    return {
        kind: _validated_market_event_snapshot(identity)
        for kind, identity in sorted(value.items())
    }
