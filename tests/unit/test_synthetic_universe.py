"""Offline cross-section for M2 (ADR prerequisite).

Without sector ETFs, RS_Sector is missing for every symbol, so
RelativeStrengthScore is missing for every symbol and the candidate set is
empty by construction. These tests pin the properties that make the offline
path able to produce a real result.
"""

from __future__ import annotations

from datetime import date

from smm.config.loader import load_config
from smm.data.generator import (
    SYNTHETIC_SECTORS,
    synthetic_universe,
    universe_rows,
)
from smm.data.validation import validate_bars

CFG = load_config(None).config.validation


def test_universe_covers_benchmark_etfs_and_members() -> None:
    paths = synthetic_universe()
    assert "SPY" in paths
    for etf, members in SYNTHETIC_SECTORS.values():
        assert etf in paths
        assert set(members) <= set(paths)


def test_every_path_passes_validation() -> None:
    """A fixture the validator would reject proves nothing downstream."""
    for path in synthetic_universe().values():
        validate_bars(list(path.bars), cfg=CFG)


def test_paths_are_long_enough_for_the_history_gate() -> None:
    for path in synthetic_universe().values():
        assert len(path.bars) >= 252


def test_generation_is_deterministic() -> None:
    first = {s: p.digest() for s, p in synthetic_universe().items()}
    second = {s: p.digest() for s, p in synthetic_universe().items()}
    assert first == second


def test_members_spread_around_their_sector_etf() -> None:
    """Ranking proves nothing if every symbol performs identically.

    Each sector needs at least one member beating its ETF and one lagging it,
    or sector RS cannot discriminate.
    """
    paths = synthetic_universe()

    def total_return(symbol: str) -> float:
        bars = paths[symbol].bars
        return bars[-1].adj_close / bars[-127].adj_close - 1.0

    for etf, members in SYNTHETIC_SECTORS.values():
        benchmark = total_return(etf)
        returns = [total_return(m) for m in members]
        assert any(r > benchmark for r in returns), f"no member beats {etf}"
        assert any(r < benchmark for r in returns), f"no member lags {etf}"


def test_universe_rows_carry_sectors_and_exclude_etfs() -> None:
    rows = universe_rows(date(2024, 1, 1))
    symbols = {r["symbol"] for r in rows}
    sectors = {r["sector"] for r in rows}

    assert sectors == set(SYNTHETIC_SECTORS)
    # Benchmarks are not universe members (constitution §10).
    assert "SPY" not in symbols
    for etf, members in SYNTHETIC_SECTORS.values():
        assert etf not in symbols
        assert set(members) <= symbols


def test_universe_rows_agree_with_the_snapshot_date() -> None:
    as_of = date(2024, 3, 7)
    assert all(r["snapshot_date"] == as_of.isoformat() for r in universe_rows(as_of))
