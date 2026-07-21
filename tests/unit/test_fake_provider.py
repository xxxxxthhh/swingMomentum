"""FakeProvider against generated CSVs (no committed fixtures — ADR §4.1)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.data.fake import FakeProvider
from smm.data.protocol import DataProvider
from smm.domain.enums import SignalState
from smm.domain.identity import make_logical_signal_id, make_setup_key
from smm.domain.models import Signal

REPO = Path(__file__).resolve().parents[2]
WIDE = (date(2020, 1, 1), date(2030, 12, 31))


def test_fake_provider_is_data_provider(ohlcv_dir: Path) -> None:
    assert isinstance(FakeProvider(ohlcv_dir), DataProvider)


def test_loads_generated_paths(ohlcv_dir: Path) -> None:
    provider = FakeProvider(ohlcv_dir)
    universe = provider.get_universe(date(2024, 1, 15))
    assert {"NVDA", "FAKE", "SPY"} <= set(universe)

    bars = provider.get_daily_bars("NVDA", *WIDE)
    assert len(bars) >= 252
    dates = [b.date for b in bars]
    assert dates == sorted(dates)
    assert all(b.symbol == "NVDA" for b in bars)


def test_round_trips_the_adjusted_fields(ohlcv_dir: Path) -> None:
    """adj_close/adj_factor must survive the CSV boundary, not be re-defaulted."""
    bars = FakeProvider(ohlcv_dir).get_daily_bars("NVDA", *WIDE)
    assert all(b.adj_factor == 1.0 for b in bars)
    assert all(b.adj_close == b.close for b in bars)


def test_rejects_csv_missing_adjusted_columns(tmp_path: Path) -> None:
    """A CSV without adj_close must fail closed rather than fall back to close."""
    directory = tmp_path / "ohlcv"
    directory.mkdir()
    (directory / "bad.csv").write_text(
        "symbol,date,open,high,low,close,volume\nX,2024-01-02,10,11,9,10,1000\n",
        encoding="utf-8",
    )
    with pytest.raises(DataValidationError, match="columns"):
        FakeProvider(directory)


def test_date_range_is_inclusive(ohlcv_dir: Path) -> None:
    provider = FakeProvider(ohlcv_dir)
    every = provider.get_daily_bars("SPY", *WIDE)
    first, last = every[10].date, every[20].date
    window = provider.get_daily_bars("SPY", first, last)
    assert window[0].date == first
    assert window[-1].date == last


def test_calendar_is_sorted_and_bounded(ohlcv_dir: Path) -> None:
    provider = FakeProvider(ohlcv_dir)
    cal = provider.get_calendar(date(2023, 1, 1), date(2023, 3, 1))
    assert cal == sorted(cal)
    assert cal
    assert all(date(2023, 1, 1) <= d <= date(2023, 3, 1) for d in cal)


def test_missing_fixtures_dir() -> None:
    with pytest.raises(DataValidationError):
        FakeProvider("/nonexistent/path/ohlcv")


def test_smoke_generated_bar_to_signal_object(ohlcv_dir: Path) -> None:
    """Minimal pipeline smoke: bar + config → Signal object (not a scanner)."""
    loaded = load_config(REPO / "configs" / "smm_v1_0_0.yaml")
    bars = FakeProvider(ohlcv_dir).get_daily_bars("NVDA", *WIDE)
    last = bars[-1]
    setup_key = make_setup_key(
        last.symbol,
        breakout_window=loaded.config.signal.breakout_window,
        breakout_level=last.high,
        anchor_date=last.date,
    )
    signal_id = make_logical_signal_id(
        symbol=last.symbol,
        setup_key=setup_key,
        strategy_version=loaded.version,
    )
    sig = Signal(
        id=signal_id,
        symbol=last.symbol,
        as_of=last.date,
        state=SignalState.DETECTED,
        setup_key=setup_key,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
        reason_codes=["smoke_generated"],
    )
    assert sig.id == signal_id
    assert sig.config_hash == loaded.config_hash
