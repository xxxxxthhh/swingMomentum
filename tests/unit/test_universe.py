"""Dated universe snapshot selection (ADR 2026-07-22 §2.1)."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from smm.core.errors import DataValidationError
from smm.data.universe import load_snapshots, load_universe, select_snapshot

REPO = Path(__file__).resolve().parents[2]
SHIPPED = REPO / "configs" / "universe"


def write_snapshot(
    directory: Path,
    snapshot_date: str,
    *,
    label: str = "test",
    symbols: tuple[str, ...] = ("AAPL", "MSFT"),
    row_date: str | None = None,
) -> Path:
    target = directory / f"{snapshot_date}_{label}.csv"
    with target.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["symbol", "name", "index_membership", "snapshot_date"])
        for symbol in symbols:
            writer.writerow([symbol, f"{symbol} Inc.", "both", row_date or snapshot_date])
    return target


@pytest.fixture
def universe_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "universe"
    directory.mkdir()
    return directory


# --- shipped production snapshot -------------------------------------------


GICS_SECTORS = {
    "information_technology",
    "financials",
    "health_care",
    "consumer_discretionary",
    "consumer_staples",
    "energy",
    "industrials",
    "materials",
    "utilities",
    "real_estate",
    "communication_services",
}

# Benchmarks, not universe members: constitution §10 limits the universe to
# common stock, and these are ETFs.
BENCHMARK_ETFS = {"SPY", "QQQ", "XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU"}


def shipped_rows() -> list[dict[str, str]]:
    path = next(SHIPPED.glob("*.csv"))
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_shipped_snapshot_parses() -> None:
    snapshots = load_snapshots(SHIPPED)
    assert len(snapshots) == 1, "two snapshots on one date would make selection order-dependent"
    assert len(snapshots[0].symbols) > 400


def test_shipped_snapshot_reconciles_with_both_indices() -> None:
    """415 + 88 = 503 S&P 500, and 88 + 15 = 103 Nasdaq-100."""
    rows = shipped_rows()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["index_membership"]] = counts.get(row["index_membership"], 0) + 1
    sp500 = counts.get("sp500", 0) + counts.get("both", 0)
    ndx = counts.get("ndx100", 0) + counts.get("both", 0)
    assert 480 <= sp500 <= 520, f"S&P 500 count off: {sp500}"
    assert 95 <= ndx <= 110, f"Nasdaq-100 count off: {ndx}"


def test_shipped_snapshot_contains_no_etfs() -> None:
    """An ETF in the universe would become a scan candidate (§10 forbids it)."""
    symbols = {r["symbol"] for r in shipped_rows()}
    assert not (symbols & BENCHMARK_ETFS)


def test_shipped_snapshot_sectors_are_gics_keys_or_empty() -> None:
    """Empty is allowed and meaningful: a missing sector propagates and drops
    the symbol, whereas a wrong one silently corrupts the whole sector's RS
    ranking. Nasdaq-only names have no GICS source, so they are left empty."""
    for row in shipped_rows():
        assert row["sector"] in GICS_SECTORS or row["sector"] == "", (
            f"{row['symbol']}: bad sector {row['sector']!r}"
        )


def test_sp500_members_all_have_a_gics_sector() -> None:
    """The S&P 500 page carries GICS directly — none of those may be empty."""
    for row in shipped_rows():
        if row["index_membership"] in ("sp500", "both"):
            assert row["sector"] in GICS_SECTORS, f"{row['symbol']} lost its GICS sector"


def test_nasdaq_only_members_have_no_guessed_sector() -> None:
    """The Nasdaq-100 page carries ICB, which does not convert safely to GICS.

    Mapping it anyway was measured at a 27% error rate (NBIS, PDD, SPCX, TRI
    out of 15), so these are deliberately left empty rather than guessed.
    """
    for row in shipped_rows():
        if row["index_membership"] == "ndx100":
            assert row["sector"] == "", f"{row['symbol']}: sector must not be guessed from ICB"


def test_shipped_snapshot_uses_yahoo_share_class_form() -> None:
    """Wikipedia writes BRK.B; Yahoo needs BRK-B. A dot would silently 404."""
    symbols = {r["symbol"] for r in shipped_rows()}
    assert not any("." in s for s in symbols)
    assert "BRK-B" in symbols


# --- allowed ---------------------------------------------------------------


def test_picks_latest_snapshot_at_or_before_as_of(universe_dir: Path) -> None:
    write_snapshot(universe_dir, "2026-01-05", symbols=("OLD",))
    write_snapshot(universe_dir, "2026-03-05", symbols=("MID",))
    write_snapshot(universe_dir, "2026-06-05", symbols=("NEW",))
    chosen = select_snapshot(
        load_snapshots(universe_dir), date(2026, 4, 1), max_age_days=90
    )
    assert chosen.symbols == ("MID",)


# --- forbidden: look-ahead -------------------------------------------------


def test_future_snapshot_is_never_used(universe_dir: Path) -> None:
    write_snapshot(universe_dir, "2026-06-05", symbols=("FUTURE",))
    with pytest.raises(DataValidationError, match="look-ahead"):
        load_universe(universe_dir, date(2026, 1, 1), max_age_days=90)


def test_exact_as_of_match_is_allowed(universe_dir: Path) -> None:
    write_snapshot(universe_dir, "2026-06-05", symbols=("TODAY",))
    chosen = load_universe(universe_dir, date(2026, 6, 5), max_age_days=90)
    assert chosen.symbols == ("TODAY",)


# --- forbidden: stale ------------------------------------------------------


def test_snapshot_older_than_limit_fails_closed(universe_dir: Path) -> None:
    write_snapshot(universe_dir, "2026-01-01")
    with pytest.raises(DataValidationError, match="days old"):
        load_universe(universe_dir, date(2026, 6, 1), max_age_days=90)


def test_snapshot_inside_limit_is_served(universe_dir: Path) -> None:
    write_snapshot(universe_dir, "2026-05-01", symbols=("FRESH",))
    chosen = load_universe(universe_dir, date(2026, 6, 1), max_age_days=90)
    assert chosen.symbols == ("FRESH",)


def test_stale_snapshot_is_not_silently_replaced_by_an_older_one(
    universe_dir: Path,
) -> None:
    """Failing closed must not degrade into 'use whatever exists'."""
    write_snapshot(universe_dir, "2025-01-01", symbols=("ANCIENT",))
    write_snapshot(universe_dir, "2026-01-01", symbols=("OLD",))
    with pytest.raises(DataValidationError, match="days old"):
        load_universe(universe_dir, date(2026, 6, 1), max_age_days=90)


# --- forbidden: inventing a universe --------------------------------------


def test_no_snapshots_at_all_fails_closed(universe_dir: Path) -> None:
    with pytest.raises(DataValidationError, match="no universe snapshots"):
        load_universe(universe_dir, date(2026, 6, 1), max_age_days=90)


def test_missing_directory_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="not found"):
        load_universe(tmp_path / "absent", date(2026, 6, 1), max_age_days=90)


# --- file integrity --------------------------------------------------------


def test_filename_date_must_match_rows(universe_dir: Path) -> None:
    write_snapshot(universe_dir, "2026-06-05", row_date="2026-01-01")
    with pytest.raises(DataValidationError, match="disagrees with"):
        load_snapshots(universe_dir)


def test_missing_columns_rejected(universe_dir: Path) -> None:
    (universe_dir / "2026-06-05_bad.csv").write_text("symbol\nAAPL\n", encoding="utf-8")
    with pytest.raises(DataValidationError, match="needs columns"):
        load_snapshots(universe_dir)


def test_unknown_membership_rejected(universe_dir: Path) -> None:
    target = universe_dir / "2026-06-05_bad.csv"
    target.write_text(
        "symbol,name,index_membership,snapshot_date\nAAPL,Apple,russell2000,2026-06-05\n",
        encoding="utf-8",
    )
    with pytest.raises(DataValidationError, match="unknown index_membership"):
        load_snapshots(universe_dir)


def test_duplicate_symbols_rejected(universe_dir: Path) -> None:
    write_snapshot(universe_dir, "2026-06-05", symbols=("AAPL", "AAPL"))
    with pytest.raises(DataValidationError, match="duplicate symbols"):
        load_snapshots(universe_dir)
