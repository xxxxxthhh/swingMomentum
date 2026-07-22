"""Append-only Parquet transition log with fail-closed conflict detection."""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from smm.core.errors import DataValidationError
from smm.signals.lifecycle import SignalTransition, latest_transitions

_SCHEMA = pa.schema(
    [
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


def transition_path(root: Path | str) -> Path:
    return Path(root) / "signal_transitions.parquet"


def _row(transition: SignalTransition) -> dict[str, object]:
    values = transition.model_dump()
    values["from_state"] = transition.from_state.value
    values["to_state"] = transition.to_state.value
    return values


def read_transitions(root: Path | str) -> list[SignalTransition]:
    target = transition_path(root)
    if not target.exists():
        return []
    rows = [SignalTransition(**row) for row in pq.read_table(target, schema=_SCHEMA).to_pylist()]
    latest_transitions(rows)  # validate uniqueness and chain before returning data
    return sorted(rows, key=lambda row: (row.as_of, row.signal_id))


def append_transitions(
    root: Path | str, transitions: Sequence[SignalTransition]
) -> Path:
    """Append unseen events; exact repeats are no-op, disagreements stop the run."""
    target = transition_path(root)
    existing = read_transitions(root)
    by_key: dict[tuple[str, date], SignalTransition] = {
        (row.signal_id, row.as_of): row for row in existing
    }
    additions: list[SignalTransition] = []
    for row in transitions:
        key = (row.signal_id, row.as_of)
        previous = by_key.get(key)
        if previous is not None:
            if previous != row:
                raise DataValidationError(
                    f"conflicting transition for signal_id={row.signal_id} as_of={row.as_of}"
                )
            continue
        by_key[key] = row
        additions.append(row)

    if not additions:
        # A valid daily scan may have no qualifying symbols (constitution:
        # cash is a valid position). No event means no file, not a failed run.
        return target

    merged = sorted([*existing, *additions], key=lambda row: (row.as_of, row.signal_id))
    latest_transitions(merged)
    table = pa.Table.from_pylist([_row(row) for row in merged], schema=_SCHEMA)
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
    return target
