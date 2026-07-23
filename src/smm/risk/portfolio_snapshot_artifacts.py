"""M7 shadow-only external :class:`PortfolioSnapshot` audit seam.

This module deliberately does not select an execution mode, call the Risk
Engine, append a ledger, or write a completion manifest. It only turns an
operator-supplied snapshot into a validated, normalized per-session artifact
that a later M7 orchestrator can hash into its shadow manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from smm.core.errors import DataValidationError
from smm.domain.models import PortfolioSnapshot
from smm.report.format import dump_json_deterministic

_PORTFOLIO_SNAPSHOT_ARTIFACT_NAME = "portfolio_snapshot.json"
_MANIFEST_NAME = "manifest.json"


def load_shadow_portfolio_snapshot(
    source: Path | str,
    *,
    as_of: date,
    strategy_version: str,
    config_hash: str,
) -> PortfolioSnapshot:
    """Load one explicit external shadow snapshot and verify its X identity.

    Raw source bytes are transport only: this function never preserves a path
    or accepts an implicit account default. A later artifact write uses the
    parsed snapshot's deterministic representation as the replay fact.
    """
    _validate_expected_identity(
        as_of=as_of,
        strategy_version=strategy_version,
        config_hash=config_hash,
    )
    if not isinstance(source, (Path, str)):
        raise DataValidationError("portfolio snapshot source must be a path")

    try:
        raw = Path(source).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DataValidationError("cannot read external portfolio snapshot") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DataValidationError("invalid portfolio snapshot JSON") from exc
    if not isinstance(payload, dict):
        raise DataValidationError("invalid portfolio snapshot JSON")
    try:
        snapshot = PortfolioSnapshot.model_validate(payload)
    except ValidationError as exc:
        raise DataValidationError("invalid portfolio snapshot") from exc

    identity = (snapshot.as_of, snapshot.strategy_version, snapshot.config_hash)
    expected = (as_of, strategy_version, config_hash)
    if identity != expected:
        raise DataValidationError("portfolio snapshot identity does not match shadow evaluation")
    return snapshot


def portfolio_snapshot_payload(snapshot: PortfolioSnapshot) -> dict[str, Any]:
    """Return the normalized, path-free session payload for one snapshot."""
    if not isinstance(snapshot, PortfolioSnapshot):
        raise DataValidationError("portfolio snapshot artifact requires PortfolioSnapshot")
    payload = snapshot.model_dump(mode="json")
    # Sort set-backed fields before deterministic JSON serializes the rest, so
    # equivalent facts cannot inherit a process-dependent set order.
    payload["open_symbols"] = sorted(snapshot.open_symbols)
    payload["reserved_signal_ids"] = sorted(snapshot.reserved_signal_ids)
    return payload


def render_portfolio_snapshot_artifact(snapshot: PortfolioSnapshot) -> str:
    """Render the only canonical bytes used for snapshot replay comparison."""
    return dump_json_deterministic(portfolio_snapshot_payload(snapshot))


def portfolio_snapshot_artifact_sha256(snapshot: PortfolioSnapshot) -> str:
    """Return the digest a future shadow manifest must bind to this artifact."""
    text = render_portfolio_snapshot_artifact(snapshot)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def portfolio_snapshot_artifact_path(root: Path | str, as_of: date) -> Path:
    """Return the fixed per-session external-snapshot artifact path."""
    if not isinstance(as_of, date):
        raise DataValidationError("portfolio snapshot artifact as_of requires a date")
    return Path(root) / as_of.isoformat() / _PORTFOLIO_SNAPSHOT_ARTIFACT_NAME


def write_portfolio_snapshot_artifact(
    root: Path | str,
    snapshot: PortfolioSnapshot,
) -> Path:
    """Create one immutable normalized snapshot artifact.

    Exact reruns compare canonical artifact bytes rather than raw operator
    input bytes. Therefore insignificant JSON key/whitespace differences are
    a no-op, while a changed parsed snapshot conflicts. An absent artifact
    cannot be introduced after a session manifest exists.
    """
    if not isinstance(snapshot, PortfolioSnapshot):
        raise DataValidationError("portfolio snapshot artifact requires PortfolioSnapshot")

    target = portfolio_snapshot_artifact_path(root, snapshot.as_of)
    text = render_portfolio_snapshot_artifact(snapshot)
    if target.exists():
        _accept_or_reject_existing_snapshot_artifact(target, text)
        return target

    manifest_file = target.parent / _MANIFEST_NAME
    if manifest_file.exists():
        raise DataValidationError(
            "cannot add PortfolioSnapshot artifact to completed session "
            f"{snapshot.as_of.isoformat()}; reruns must preserve manifest shape"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    _create_snapshot_artifact(target, text)
    return target


def _validate_expected_identity(
    *,
    as_of: date,
    strategy_version: str,
    config_hash: str,
) -> None:
    if not isinstance(as_of, date):
        raise DataValidationError("shadow evaluation as_of requires a date")
    if not isinstance(strategy_version, str) or not strategy_version.strip():
        raise DataValidationError("shadow evaluation strategy_version must be non-empty")
    if not isinstance(config_hash, str) or not config_hash.strip():
        raise DataValidationError("shadow evaluation config_hash must be non-empty")


def _create_snapshot_artifact(target: Path, text: str) -> None:
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
            _accept_or_reject_existing_snapshot_artifact(target, text)
    finally:
        temporary.unlink(missing_ok=True)


def _accept_or_reject_existing_snapshot_artifact(target: Path, text: str) -> None:
    if target.read_text(encoding="utf-8") != text:
        raise DataValidationError(
            "conflicting portfolio snapshot artifact already exists for "
            f"{target.parent.name}"
        )
