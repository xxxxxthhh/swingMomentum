"""FakeProvider + fixture loading."""

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

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ohlcv"
REPO = Path(__file__).resolve().parents[2]


def test_fake_provider_is_data_provider() -> None:
    p = FakeProvider(FIXTURES)
    assert isinstance(p, DataProvider)


def test_load_breakout_success_bars() -> None:
    p = FakeProvider(FIXTURES)
    universe = p.get_universe(date(2024, 1, 15))
    assert "NVDA" in universe
    bars = p.get_daily_bars("NVDA", date(2024, 1, 1), date(2024, 12, 31))
    assert len(bars) >= 30
    dates = [b.date for b in bars]
    assert dates == sorted(dates)
    assert all(b.symbol == "NVDA" for b in bars)
    assert bars[-1].volume > 0


def test_false_breakout_and_spy() -> None:
    p = FakeProvider(FIXTURES)
    fake_bars = p.get_daily_bars("FAKE", date(2024, 1, 1), date(2024, 12, 31))
    spy = p.get_daily_bars("SPY", date(2024, 1, 1), date(2024, 12, 31))
    assert len(fake_bars) > 30
    assert len(spy) > 40
    cal = p.get_calendar(date(2024, 1, 1), date(2024, 3, 1))
    assert cal == sorted(cal)
    assert len(cal) > 0


def test_missing_fixtures_dir() -> None:
    with pytest.raises(DataValidationError):
        FakeProvider("/nonexistent/path/ohlcv")


def test_smoke_fixture_to_signal_object() -> None:
    """Minimal pipeline smoke: fixture bar + config → Signal object (not a scanner)."""
    loaded = load_config(REPO / "configs" / "smm_v1_0_0.yaml")
    p = FakeProvider(FIXTURES)
    bars = p.get_daily_bars("NVDA", date(2024, 1, 1), date(2024, 12, 31))
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
        reason_codes=["smoke_fixture"],
        scores=None,
    )
    assert sig.id == signal_id
    assert sig.config_hash == loaded.config_hash
