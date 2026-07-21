"""yfinance provider (ADR 2026-07-22 §1, §3.4).

Everything here except the ``network`` block runs offline.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.data import cache
from smm.data.generator import breakout_success
from smm.data.yfinance_provider import YFinanceProvider

REPO = Path(__file__).resolve().parents[2]
CONFIG = load_config(REPO / "configs" / "smm_v1_0_0.yaml").config


def build(tmp_path: Path) -> YFinanceProvider:
    return YFinanceProvider(
        cache_dir=tmp_path / "cache",
        universe_dir=REPO / "configs" / "universe",
        validation=CONFIG.validation,
        max_snapshot_age_days=CONFIG.universe.max_snapshot_age_days,
    )


# --- offline -------------------------------------------------------------


def test_universe_comes_from_the_dated_snapshot(tmp_path: Path) -> None:
    universe = build(tmp_path).get_universe(date(2026, 7, 22))
    assert {"AAPL", "MSFT", "BRK-B"} <= set(universe)
    # SPY is a benchmark, not a constituent — §10 limits the universe to common
    # stock. `smm ingest` fetches it separately.
    assert "SPY" not in universe


def test_universe_fails_closed_when_snapshot_is_stale(tmp_path: Path) -> None:
    provider = build(tmp_path)
    with pytest.raises(DataValidationError, match="days old"):
        provider.get_universe(date(2030, 1, 1))


def test_cached_range_is_served_without_fetching(tmp_path: Path) -> None:
    """A covered request must not reach the network at all."""
    provider = build(tmp_path)
    bars = list(breakout_success().bars)
    cache.write_bars(tmp_path / "cache", "NVDA", bars)

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("fetch attempted for a fully cached range")

    provider.fetch = explode  # type: ignore[method-assign]
    served = provider.get_daily_bars("NVDA", bars[10].date, bars[20].date)
    assert [b.date for b in served] == [b.date for b in bars[10:21]]


def test_calendar_derives_from_cached_benchmark(tmp_path: Path) -> None:
    provider = build(tmp_path)
    spy = [b.model_copy(update={"symbol": "SPY"}) for b in breakout_success().bars]
    cache.write_bars(tmp_path / "cache", "SPY", spy)
    calendar = provider.get_calendar(spy[0].date, spy[30].date)
    assert calendar == [b.date for b in spy[:31]]


def test_empty_calendar_when_benchmark_not_cached(tmp_path: Path) -> None:
    assert build(tmp_path).get_calendar(date(2024, 1, 1), date(2024, 2, 1)) == []


# --- session-date resolution ---------------------------------------------
#
# yfinance indexes daily bars with a naive midnight Timestamp. Routing that
# through to_session_date (which rejects naive input) broke every real fetch,
# so these pin the provider's actual contract.


def test_naive_midnight_index_is_the_session_date() -> None:
    assert YFinanceProvider._session_date(datetime(2024, 1, 2, 0, 0)) == date(2024, 1, 2)


def test_naive_intraday_index_is_rejected() -> None:
    """A time component would need timezone interpretation — refuse to guess."""
    with pytest.raises(DataValidationError, match="naive intraday timestamp"):
        YFinanceProvider._session_date(datetime(2024, 1, 2, 16, 30))


def test_aware_index_is_converted_to_the_eastern_session() -> None:
    moment = datetime(2024, 6, 8, 2, 0, tzinfo=UTC)
    assert YFinanceProvider._session_date(moment) == date(2024, 6, 7)


def test_plain_date_index_passes_through() -> None:
    assert YFinanceProvider._session_date(date(2024, 1, 2)) == date(2024, 1, 2)


# --- network: the ADR §3.4 verification ----------------------------------


@pytest.mark.network
def test_yahoo_pre_adjusts_close_and_volume_for_splits() -> None:
    """Pins the measured provider semantics the docstring and ADR §3.4 rely on.

    If Yahoo ever stops pre-adjusting volume, the relative-volume
    contamination ADR §3.4 describes becomes real, and this test is what
    catches it. NVDA split 10:1 effective 2024-06-10.
    """
    yfinance = pytest.importorskip("yfinance")
    frame = yfinance.download(
        "NVDA",
        start="2024-06-03",
        end="2024-06-15",
        auto_adjust=False,
        actions=False,
        progress=False,
        threads=False,
    )
    if frame is None or frame.empty:
        pytest.skip("provider returned no rows (rate limited)")
    if getattr(frame.columns, "nlevels", 1) > 1:
        frame.columns = frame.columns.droplevel(-1)

    closes = list(frame["Close"])
    volumes = list(frame["Volume"])
    # No ~10x discontinuity in either series across the split boundary.
    for before, after in zip(closes, closes[1:], strict=False):
        assert max(before, after) / min(before, after) < 2.0
    for before, after in zip(volumes, volumes[1:], strict=False):
        assert max(before, after) / min(before, after) < 3.0


@pytest.mark.network
def test_fetch_produces_validated_bars(tmp_path: Path) -> None:
    provider = build(tmp_path)
    try:
        bars = provider.fetch("AAPL", date(2024, 1, 2), date(2024, 3, 1))
    except DataValidationError as exc:
        if "no rows" in str(exc):
            pytest.skip("provider returned no rows (rate limited)")
        raise
    assert bars
    assert all(b.symbol == "AAPL" for b in bars)
    assert all(0 < b.adj_factor <= 1.0 for b in bars)
