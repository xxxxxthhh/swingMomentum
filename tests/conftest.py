"""Shared test fixtures.

Committed OHLCV CSVs were removed in favour of the deterministic generator
(ADR 2026-07-22 §4.1). Tests that need a CSV-backed provider write generated
paths to a temporary directory instead.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from smm.data.generator import SYNTHETIC_PATHS, SyntheticPath

_COLUMNS = [
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
    "adj_factor",
]


def write_path_csv(directory: Path, path: SyntheticPath, name: str) -> Path:
    target = directory / f"{name}.csv"
    with target.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLUMNS)
        writer.writeheader()
        for bar in path.bars:
            writer.writerow(
                {
                    "symbol": bar.symbol,
                    "date": bar.date.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "adj_close": bar.adj_close,
                    "adj_factor": bar.adj_factor,
                }
            )
    return target


@pytest.fixture
def ohlcv_dir(tmp_path: Path) -> Path:
    """A directory of generated CSVs, one per named synthetic path."""
    directory = tmp_path / "ohlcv"
    directory.mkdir()
    for name, build in SYNTHETIC_PATHS.items():
        write_path_csv(directory, build(), name)
    return directory
