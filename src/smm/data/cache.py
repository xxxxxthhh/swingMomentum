"""Parquet bar cache (Plan v1.1 M1).

One file per symbol. The cache is a *store*, not a second source of truth: it
holds exactly the validated bars it was given, and a re-ingest of the same
sessions replaces them rather than appending duplicates. Idempotency is the
property M1 owes — the same ``as_of`` re-run must leave the same content
behind.

pyarrow is a core dependency rather than part of the ``market`` extra so this
path, and its idempotency test, run in the network-free default CI.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from smm.core.errors import DataValidationError
from smm.domain.models import Bar

_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("date", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("adj_close", pa.float64()),
        ("adj_factor", pa.float64()),
    ]
)


def cache_path(root: Path | str, symbol: str) -> Path:
    return Path(root) / f"{symbol.upper()}.parquet"


def _to_table(bars: Sequence[Bar]) -> pa.Table:
    return pa.Table.from_pydict(
        {
            "symbol": [b.symbol for b in bars],
            "date": [b.date for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
            "adj_close": [b.adj_close for b in bars],
            "adj_factor": [b.adj_factor for b in bars],
        },
        schema=_SCHEMA,
    )


def _to_bars(table: pa.Table) -> list[Bar]:
    rows = table.to_pylist()
    return [Bar(**row) for row in rows]


def write_bars(root: Path | str, symbol: str, bars: Sequence[Bar]) -> Path:
    """Merge ``bars`` into the symbol's cache file, newest write winning per session.

    Merging rather than appending is what makes a re-run idempotent: the same
    session arriving twice must not become two rows.
    """
    if not bars:
        raise DataValidationError(f"refusing to cache an empty series for {symbol}")
    symbols = {b.symbol for b in bars}
    if symbols != {symbol.upper()}:
        raise DataValidationError(f"bars for {sorted(symbols)} written under {symbol!r}")

    target = cache_path(root, symbol)
    target.parent.mkdir(parents=True, exist_ok=True)

    merged: dict[date, Bar] = {}
    if target.exists():
        for existing in _to_bars(pq.read_table(target, schema=_SCHEMA)):
            merged[existing.date] = existing
    for bar in bars:
        merged[bar.date] = bar

    ordered = [merged[d] for d in sorted(merged)]
    pq.write_table(_to_table(ordered), target, compression="snappy")
    return target


def read_bars(
    root: Path | str,
    symbol: str,
    start: date | None = None,
    end: date | None = None,
) -> list[Bar]:
    """Read cached bars in ``[start, end]``; empty list when nothing is cached."""
    target = cache_path(root, symbol)
    if not target.exists():
        return []
    bars = _to_bars(pq.read_table(target, schema=_SCHEMA))
    return [
        b
        for b in bars
        if (start is None or b.date >= start) and (end is None or b.date <= end)
    ]


def cached_range(root: Path | str, symbol: str) -> tuple[date, date] | None:
    """First and last cached session, or ``None`` when the symbol is absent."""
    bars = read_bars(root, symbol)
    if not bars:
        return None
    return bars[0].date, bars[-1].date
