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


_COVERAGE_START = b"smm_requested_start"
_COVERAGE_END = b"smm_requested_end"


def cache_path(root: Path | str, symbol: str) -> Path:
    return Path(root) / f"{symbol.upper()}.parquet"


def _read_coverage(target: Path) -> tuple[date, date] | None:
    """Read the recorded window from the file's own schema metadata.

    Read via ``pq.read_schema``: passing an explicit ``schema=`` to
    ``read_table`` substitutes that schema and drops the file's metadata with
    it, which silently loses the coverage record.
    """
    meta = pq.read_schema(target).metadata or {}
    start, end = meta.get(_COVERAGE_START), meta.get(_COVERAGE_END)
    if not start or not end:
        return None
    return date.fromisoformat(start.decode()), date.fromisoformat(end.decode())


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


def write_bars(
    root: Path | str,
    symbol: str,
    bars: Sequence[Bar],
    *,
    requested: tuple[date, date] | None = None,
) -> Path:
    """Merge ``bars`` into the symbol's cache file, newest write winning per session.

    Merging rather than appending is what makes a re-run idempotent: the same
    session arriving twice must not become two rows.

    ``requested`` is the window that was asked for, recorded in file metadata
    and widened across writes. It answers "is this range complete?" exactly,
    which the bar dates alone cannot: a series that legitimately has no bar on
    the last requested day is indistinguishable from one that was truncated.
    """
    if not bars:
        raise DataValidationError(f"refusing to cache an empty series for {symbol}")
    symbols = {b.symbol for b in bars}
    if symbols != {symbol.upper()}:
        raise DataValidationError(f"bars for {sorted(symbols)} written under {symbol!r}")

    target = cache_path(root, symbol)
    target.parent.mkdir(parents=True, exist_ok=True)

    merged: dict[date, Bar] = {}
    coverage = requested
    if target.exists():
        for existing in _to_bars(pq.read_table(target, schema=_SCHEMA)):
            merged[existing.date] = existing
        previous = _read_coverage(target)
        if previous and coverage:
            coverage = (min(previous[0], coverage[0]), max(previous[1], coverage[1]))
        elif previous:
            coverage = previous
    for bar in bars:
        merged[bar.date] = bar

    ordered = [merged[d] for d in sorted(merged)]
    table = _to_table(ordered)
    if coverage:
        table = table.replace_schema_metadata(
            {
                _COVERAGE_START: coverage[0].isoformat().encode(),
                _COVERAGE_END: coverage[1].isoformat().encode(),
            }
        )
    pq.write_table(table, target, compression="snappy")
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


def covered_range(root: Path | str, symbol: str) -> tuple[date, date] | None:
    """The window this symbol was actually *asked* for, or ``None`` if unrecorded.

    Distinct from :func:`cached_range`, which reports the bars present. A gap at
    either edge is invisible to the latter — the provider simply may not have
    had a session there.
    """
    target = cache_path(root, symbol)
    if not target.exists():
        return None
    return _read_coverage(target)


def covers(root: Path | str, symbol: str, start: date, end: date) -> bool:
    """Whether ``[start, end]`` lies inside a previously requested window."""
    coverage = covered_range(root, symbol)
    return coverage is not None and coverage[0] <= start and coverage[1] >= end
