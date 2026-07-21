"""Dated universe snapshots (ADR 2026-07-22 §2).

Index membership is a *point-in-time* fact, so it is checked into the repo as
dated files rather than fetched at run time. A runtime scrape would make the
same ``as_of`` replay differently on two days, which breaks the idempotency the
whole daily pipeline rests on.

Selection rules (ADR §2.1):

- **allowed** — the snapshot with the largest ``snapshot_date <= as_of``
- **forbidden** — any snapshot dated after ``as_of`` (that is look-ahead)
- **forbidden** — inventing an empty or full universe when none qualifies
- **forbidden** — serving a snapshot older than ``max_snapshot_age_days``

The last rule is deliberately fail-closed. Constituents drift continuously, and
a silently stale universe puts the cross-sectional ranking on the wrong sample
without ever announcing itself.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from smm.core.errors import DataValidationError

REQUIRED_COLUMNS = {"symbol", "name", "index_membership", "snapshot_date"}
VALID_MEMBERSHIPS = {"sp500", "ndx100", "both"}


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    """One dated membership list."""

    snapshot_date: date
    symbols: tuple[str, ...]
    path: Path

    def age_days(self, as_of: date) -> int:
        return (as_of - self.snapshot_date).days


def _parse_snapshot(path: Path) -> UniverseSnapshot:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(set(reader.fieldnames)):
            raise DataValidationError(
                f"{path.name}: universe snapshot needs columns {sorted(REQUIRED_COLUMNS)}"
            )
        symbols: list[str] = []
        dates: set[date] = set()
        for row in reader:
            membership = row["index_membership"].strip().lower()
            if membership not in VALID_MEMBERSHIPS:
                raise DataValidationError(
                    f"{path.name}: unknown index_membership {membership!r}"
                )
            symbols.append(row["symbol"].strip().upper())
            dates.add(date.fromisoformat(row["snapshot_date"].strip()))

    if not symbols:
        raise DataValidationError(f"{path.name}: universe snapshot is empty")
    if len(dates) != 1:
        raise DataValidationError(
            f"{path.name}: rows disagree on snapshot_date: {sorted(dates)}"
        )
    duplicates = {s for s in symbols if symbols.count(s) > 1}
    if duplicates:
        raise DataValidationError(f"{path.name}: duplicate symbols {sorted(duplicates)}")

    snapshot_date = dates.pop()
    stem_date = path.name.split("_", 1)[0]
    if stem_date != snapshot_date.isoformat():
        raise DataValidationError(
            f"{path.name}: filename date {stem_date} disagrees with "
            f"snapshot_date {snapshot_date.isoformat()}"
        )
    return UniverseSnapshot(
        snapshot_date=snapshot_date, symbols=tuple(sorted(symbols)), path=path
    )


def load_snapshots(directory: Path | str) -> list[UniverseSnapshot]:
    """Parse every snapshot in ``directory``, oldest first."""
    root = Path(directory)
    if not root.is_dir():
        raise DataValidationError(f"universe directory not found: {root}")
    snapshots = [_parse_snapshot(p) for p in sorted(root.glob("*.csv"))]
    if not snapshots:
        raise DataValidationError(f"no universe snapshots in {root}")
    return sorted(snapshots, key=lambda s: s.snapshot_date)


def select_snapshot(
    snapshots: list[UniverseSnapshot],
    as_of: date,
    *,
    max_age_days: int,
) -> UniverseSnapshot:
    """Apply the ADR §2.1 selection rules, failing closed rather than guessing."""
    eligible = [s for s in snapshots if s.snapshot_date <= as_of]
    if not eligible:
        future = min((s.snapshot_date for s in snapshots), default=None)
        raise DataValidationError(
            f"no universe snapshot on or before {as_of}"
            + (f" (earliest available is {future}, which would be look-ahead)" if future else "")
        )
    chosen = eligible[-1]
    age = chosen.age_days(as_of)
    if age > max_age_days:
        raise DataValidationError(
            f"universe snapshot {chosen.snapshot_date} is {age} days old at {as_of} "
            f"(limit {max_age_days}) — commit a fresh snapshot; constituents drift"
        )
    return chosen


def load_universe(
    directory: Path | str,
    as_of: date,
    *,
    max_age_days: int,
) -> UniverseSnapshot:
    """Load and select in one step."""
    return select_snapshot(load_snapshots(directory), as_of, max_age_days=max_age_days)
