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
from datetime import date, timedelta
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


#: Serialised as "start:end,start:end". A **list**, not one span: two disjoint
#: requests merged into [min, max] would claim the untouched gap between them,
#: which is the same "silently incomplete" failure this metadata exists to
#: prevent — just moved from the tail into the middle.
_COVERAGE_WINDOWS = b"smm_requested_windows"


def cache_path(root: Path | str, symbol: str) -> Path:
    return Path(root) / f"{symbol.upper()}.parquet"


def _read_windows(target: Path) -> list[tuple[date, date]]:
    """Read the recorded request windows from the file's schema metadata.

    Read via ``pq.read_schema``: passing an explicit ``schema=`` to
    ``read_table`` substitutes that schema and drops the file's metadata with
    it, which silently loses the coverage record.
    """
    raw = (pq.read_schema(target).metadata or {}).get(_COVERAGE_WINDOWS)
    if not raw:
        return []
    windows: list[tuple[date, date]] = []
    for chunk in raw.decode().split(","):
        if not chunk:
            continue
        start, _, end = chunk.partition(":")
        windows.append((date.fromisoformat(start), date.fromisoformat(end)))
    return windows


def _next_weekday(day: date) -> date:
    """The first weekday strictly after ``day``."""
    cursor = day + timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    return cursor


def _merge_windows(windows: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Merge windows with no unrequested weekday between them; keep real gaps.

    A Friday-to-Monday gap holds only a weekend, so no session can sit in it and
    the windows are effectively contiguous. A gap containing a weekday is left
    open even if that weekday turned out to be a holiday: without a calendar the
    two cases are indistinguishable, and the safe error is an extra fetch rather
    than a false claim of coverage.
    """
    merged: list[tuple[date, date]] = []
    for start, end in sorted(windows):
        if merged and start <= _next_weekday(merged[-1][1]):
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


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
    windows: list[tuple[date, date]] = []
    if target.exists():
        for existing in _to_bars(pq.read_table(target, schema=_SCHEMA)):
            merged[existing.date] = existing
        windows = _read_windows(target)
    if requested:
        windows = _merge_windows([*windows, requested])
    for bar in bars:
        merged[bar.date] = bar

    ordered = [merged[d] for d in sorted(merged)]
    table = _to_table(ordered)
    if windows:
        table = table.replace_schema_metadata(
            {
                _COVERAGE_WINDOWS: ",".join(
                    f"{start.isoformat()}:{end.isoformat()}" for start, end in windows
                ).encode()
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


def covered_windows(root: Path | str, symbol: str) -> list[tuple[date, date]]:
    """Every window this symbol was actually *asked* for, merged and sorted.

    Distinct from :func:`cached_range`, which reports the bars present. A gap at
    either edge is invisible to the latter — the provider simply may not have
    had a session there.

    Note this records what was **requested**, not that every session in the
    window produced a bar. An IPO, a delisting, or a truncated response can
    still leave holes inside a covered window; catching those needs the
    validation layer and a benchmark calendar.
    """
    target = cache_path(root, symbol)
    if not target.exists():
        return []
    return _merge_windows(_read_windows(target))


def covers(root: Path | str, symbol: str, start: date, end: date) -> bool:
    """Whether ``[start, end]`` lies inside a **single** recorded window.

    Spanning two windows is not coverage: whatever sits between them was never
    requested.
    """
    return any(
        window_start <= start and window_end >= end
        for window_start, window_end in covered_windows(root, symbol)
    )
