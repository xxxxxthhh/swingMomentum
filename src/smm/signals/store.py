"""Atomic Parquet transition log with fail-closed daily batch seals."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from smm.core.errors import DataValidationError
from smm.signals.lifecycle import SignalTransition, latest_transitions

_BATCH = "batch"
_TRANSITION = "transition"
_SCHEMA = pa.schema(
    [
        ("record_type", pa.string()),
        ("transition_count", pa.int64()),
        ("batch_digest", pa.string()),
        ("signal_id", pa.string()),
        ("symbol", pa.string()),
        ("setup_key", pa.string()),
        ("watchlist_entry", pa.date32()),
        ("from_state", pa.string()),
        ("to_state", pa.string()),
        ("as_of", pa.date32()),
        ("reason_codes", pa.list_(pa.string())),
        ("strategy_version", pa.string()),
        ("config_hash", pa.string()),
        ("breakout_level", pa.float64()),
        ("relative_volume", pa.float64()),
        ("extension_atr", pa.float64()),
    ]
)
_TRANSITION_FIELDS = tuple(SignalTransition.model_fields)


@dataclass(frozen=True, slots=True)
class BatchSeal:
    """Read-only batch-seal metadata: the fact-of-record for which ``as_of``
    dates have been processed, including sealed-empty days (M4 ADR §2)."""

    as_of: date
    strategy_version: str
    config_hash: str
    transition_count: int
    batch_digest: str


def transition_path(root: Path | str) -> Path:
    return Path(root) / "signal_transitions.parquet"


def _transition_row(transition: SignalTransition) -> dict[str, object]:
    values = {name: None for name in _SCHEMA.names}
    values.update(transition.model_dump())
    values.update(
        record_type=_TRANSITION,
        from_state=transition.from_state.value,
        to_state=transition.to_state.value,
    )
    return values


def _seal_row(seal: BatchSeal) -> dict[str, object]:
    values = {name: None for name in _SCHEMA.names}
    values.update(
        record_type=_BATCH,
        transition_count=seal.transition_count,
        batch_digest=seal.batch_digest,
        as_of=seal.as_of,
        strategy_version=seal.strategy_version,
        config_hash=seal.config_hash,
    )
    return values


def _batch_digest(transitions: Sequence[SignalTransition]) -> str:
    canonical = [
        row.model_dump(mode="json")
        for row in sorted(transitions, key=lambda row: row.signal_id)
    ]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_store(root: Path | str) -> tuple[list[SignalTransition], dict[date, BatchSeal]]:
    target = transition_path(root)
    if not target.exists():
        return [], {}

    transitions: list[SignalTransition] = []
    seals: dict[date, BatchSeal] = {}
    for raw in pq.read_table(target).to_pylist():
        record_type = raw.get("record_type") or _TRANSITION
        if record_type == _TRANSITION:
            transitions.append(
                SignalTransition(**{name: raw.get(name) for name in _TRANSITION_FIELDS})
            )
            continue
        if record_type != _BATCH:
            raise DataValidationError(f"unknown signal-store record type {record_type!r}")
        try:
            seal = BatchSeal(
                as_of=raw["as_of"],
                strategy_version=raw["strategy_version"],
                config_hash=raw["config_hash"],
                transition_count=raw["transition_count"],
                batch_digest=raw["batch_digest"],
            )
        except (KeyError, TypeError) as exc:
            raise DataValidationError("incomplete transition batch seal") from exc
        if (
            not isinstance(seal.as_of, date)
            or not isinstance(seal.strategy_version, str)
            or not seal.strategy_version
            or not isinstance(seal.config_hash, str)
            or not seal.config_hash
            or not isinstance(seal.transition_count, int)
            or seal.transition_count < 0
            or not isinstance(seal.batch_digest, str)
            or not seal.batch_digest
        ):
            raise DataValidationError(f"invalid transition batch seal for as_of={seal.as_of}")
        if seal.as_of in seals:
            raise DataValidationError(f"duplicate transition batch seal for as_of={seal.as_of}")
        seals[seal.as_of] = seal

    transitions.sort(key=lambda row: (row.as_of, row.signal_id))
    latest_transitions(transitions)
    for as_of, seal in seals.items():
        batch = [row for row in transitions if row.as_of == as_of]
        if len(batch) != seal.transition_count or _batch_digest(batch) != seal.batch_digest:
            raise DataValidationError(f"corrupt transition batch for as_of={as_of}")
        if any(
            row.strategy_version != seal.strategy_version or row.config_hash != seal.config_hash
            for row in batch
        ):
            raise DataValidationError(
                f"mixed config identity in transition batch for as_of={as_of}"
            )
    return transitions, seals


def read_transitions(root: Path | str) -> list[SignalTransition]:
    transitions, _ = _read_store(root)
    return transitions


def read_batch_seals(root: Path | str) -> dict[date, BatchSeal]:
    """Read-only batch-seal metadata, keyed by ``as_of``.

    Callers must use this -- not "does this as_of have any transition rows"
    -- to ask whether a day was processed. A sealed day with zero
    transitions is a first-class processed state (M4 ADR §2); inferring
    "unprocessed" from an empty row set would let a caller re-run and
    silently re-decide an already-sealed empty day.
    """
    _, seals = _read_store(root)
    return seals


def latest_sealed_as_of(root: Path | str) -> date | None:
    """The most recent sealed ``as_of``, or ``None`` if the store is empty."""
    seals = read_batch_seals(root)
    return max(seals) if seals else None


def assert_session_continuity(
    root: Path | str,
    *,
    as_of: date,
    sessions: Sequence[date],
) -> None:
    """M4 ADR §2: fail closed on anything but an exact rerun or the next
    provider-calendar session after the latest seal.

    ``sessions`` is the provider calendar, not a local window -- it must
    include the latest sealed ``as_of`` or this cannot tell "the next
    session" from "a gap". A skipped session could hide a
    ``WATCHLISTED -> TRIGGERED`` transition or a hard-filter loss that
    becomes unrecoverable once the day is gone, so jumps and backfills both
    fail closed rather than silently advancing.
    """
    ordered = list(sessions)
    if ordered != sorted(set(ordered)):
        raise DataValidationError("session calendar must be sorted with unique sessions")
    if as_of not in ordered:
        raise DataValidationError(f"as_of {as_of} is not a provider session")

    latest = latest_sealed_as_of(root)
    if latest is None:
        return  # no prior seal: any valid session starts the observation window

    if as_of == latest:
        return  # exact rerun

    if latest not in ordered:
        raise DataValidationError(
            f"provider calendar does not cover the latest sealed batch {latest}"
        )
    latest_index = ordered.index(latest)
    if latest_index == len(ordered) - 1:
        raise DataValidationError(f"no provider session follows the latest seal {latest}")
    expected_next = ordered[latest_index + 1]
    if as_of == expected_next:
        return
    if as_of < latest:
        raise DataValidationError(
            f"as_of {as_of} precedes the latest sealed batch {latest}; backfill is forbidden"
        )
    raise DataValidationError(
        f"as_of {as_of} skips one or more sessions after the latest seal {latest}; "
        f"expected {expected_next}"
    )


def _resolve_batch_metadata(
    transitions: Sequence[SignalTransition],
    *,
    as_of: date | None,
    strategy_version: str | None,
    config_hash: str | None,
) -> tuple[date, str, str]:
    dates = {row.as_of for row in transitions}
    versions = {row.strategy_version for row in transitions}
    hashes = {row.config_hash for row in transitions}
    if len(dates) > 1 or len(versions) > 1 or len(hashes) > 1:
        raise DataValidationError("one transition append must contain exactly one daily batch")
    resolved_as_of = as_of if as_of is not None else next(iter(dates), None)
    resolved_version = (
        strategy_version if strategy_version is not None else next(iter(versions), None)
    )
    resolved_hash = config_hash if config_hash is not None else next(iter(hashes), None)
    if (
        not isinstance(resolved_as_of, date)
        or not isinstance(resolved_version, str)
        or not resolved_version
        or not isinstance(resolved_hash, str)
        or not resolved_hash
    ):
        raise DataValidationError(
            "transition batches require valid as_of, strategy_version, and config_hash"
        )
    if dates and dates != {resolved_as_of}:
        raise DataValidationError("transition batch as_of does not match its rows")
    if versions and versions != {resolved_version}:
        raise DataValidationError("transition batch strategy_version does not match its rows")
    if hashes and hashes != {resolved_hash}:
        raise DataValidationError("transition batch config_hash does not match its rows")
    return resolved_as_of, resolved_version, resolved_hash


def _write_store(
    target: Path,
    transitions: Sequence[SignalTransition],
    seals: Sequence[BatchSeal],
) -> None:
    records = [
        *(_seal_row(seal) for seal in sorted(seals, key=lambda seal: seal.as_of)),
        *(
            _transition_row(row)
            for row in sorted(transitions, key=lambda row: (row.as_of, row.signal_id))
        ),
    ]
    records.sort(
        key=lambda row: (
            row["as_of"],
            0 if row["record_type"] == _BATCH else 1,
            row["signal_id"] or "",
        )
    )
    table = pa.Table.from_pylist(records, schema=_SCHEMA)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        pq.write_table(table, temporary, compression="snappy")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def append_transitions(
    root: Path | str,
    transitions: Sequence[SignalTransition],
    *,
    as_of: date | None = None,
    strategy_version: str | None = None,
    config_hash: str | None = None,
) -> Path:
    """Atomically seal one daily transition multiset; exact reruns are no-op."""
    batch = sorted(transitions, key=lambda row: row.signal_id)
    batch_as_of, batch_version, batch_hash = _resolve_batch_metadata(
        batch,
        as_of=as_of,
        strategy_version=strategy_version,
        config_hash=config_hash,
    )
    keys = {(row.signal_id, row.as_of) for row in batch}
    if len(keys) != len(batch):
        raise DataValidationError(f"duplicate transition in batch for as_of={batch_as_of}")

    target = transition_path(root)
    existing, seals = _read_store(root)
    existing_batch = [row for row in existing if row.as_of == batch_as_of]
    seal = seals.get(batch_as_of)
    if seal is not None:
        same_identity = (
            seal.strategy_version == batch_version and seal.config_hash == batch_hash
        )
        if not same_identity or existing_batch != batch:
            raise DataValidationError(
                f"conflicting transition batch for as_of={batch_as_of}"
            )
        return target

    # Files written before batch seals are accepted only when the caller
    # reproduces their complete as_of multiset; the rewrite then seals it.
    if existing_batch and existing_batch != batch:
        raise DataValidationError(f"conflicting transition batch for as_of={batch_as_of}")

    merged = [row for row in existing if row.as_of != batch_as_of]
    merged.extend(batch)
    merged.sort(key=lambda row: (row.as_of, row.signal_id))
    latest_transitions(merged)
    new_seal = BatchSeal(
        as_of=batch_as_of,
        strategy_version=batch_version,
        config_hash=batch_hash,
        transition_count=len(batch),
        batch_digest=_batch_digest(batch),
    )
    _write_store(target, merged, [*seals.values(), new_seal])
    return target
