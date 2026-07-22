"""Parquet cache round-trip and idempotency (Plan v1.1 M1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from smm.core.errors import DataValidationError
from smm.data import cache
from smm.data.generator import breakout_success

PATH = breakout_success()
BARS = list(PATH.bars)


def test_round_trip_preserves_every_field(tmp_path: Path) -> None:
    cache.write_bars(tmp_path, "NVDA", BARS)
    assert cache.read_bars(tmp_path, "NVDA") == BARS


def test_adjusted_fields_survive_the_cache(tmp_path: Path) -> None:
    """adj_close/adj_factor must not be dropped and re-derived on read."""
    cache.write_bars(tmp_path, "NVDA", BARS)
    restored = cache.read_bars(tmp_path, "NVDA")
    assert all(b.adj_factor == 1.0 for b in restored)
    assert all(b.adj_close == b.close for b in restored)


def test_rewriting_the_same_bars_is_idempotent(tmp_path: Path) -> None:
    """The M1 DoD: same as_of re-run leaves the same content behind."""
    cache.write_bars(tmp_path, "NVDA", BARS)
    first = cache.read_bars(tmp_path, "NVDA")
    cache.write_bars(tmp_path, "NVDA", BARS)
    cache.write_bars(tmp_path, "NVDA", BARS)
    assert cache.read_bars(tmp_path, "NVDA") == first
    assert len(cache.read_bars(tmp_path, "NVDA")) == len(BARS)


def test_overlapping_write_merges_rather_than_appends(tmp_path: Path) -> None:
    cache.write_bars(tmp_path, "NVDA", BARS[:100])
    cache.write_bars(tmp_path, "NVDA", BARS[50:])
    restored = cache.read_bars(tmp_path, "NVDA")
    assert len(restored) == len(BARS)
    assert [b.date for b in restored] == sorted(b.date for b in BARS)


def test_rewrite_replaces_a_corrected_session(tmp_path: Path) -> None:
    """A re-fetched session must win, not coexist with the stale one."""
    cache.write_bars(tmp_path, "NVDA", BARS)
    corrected = BARS[10].model_copy(update={"volume": 12_345.0})
    cache.write_bars(tmp_path, "NVDA", [corrected])
    restored = cache.read_bars(tmp_path, "NVDA")
    assert len(restored) == len(BARS)
    assert restored[10].volume == 12_345.0


def test_read_range_is_inclusive(tmp_path: Path) -> None:
    cache.write_bars(tmp_path, "NVDA", BARS)
    first, last = BARS[5].date, BARS[15].date
    window = cache.read_bars(tmp_path, "NVDA", first, last)
    assert window[0].date == first
    assert window[-1].date == last
    assert len(window) == 11


def test_missing_symbol_reads_empty(tmp_path: Path) -> None:
    assert cache.read_bars(tmp_path, "NOPE") == []
    assert cache.cached_range(tmp_path, "NOPE") is None


def test_cached_range(tmp_path: Path) -> None:
    cache.write_bars(tmp_path, "NVDA", BARS)
    assert cache.cached_range(tmp_path, "NVDA") == (BARS[0].date, BARS[-1].date)


def test_coverage_is_recorded_and_queryable(tmp_path: Path) -> None:
    window = (BARS[0].date, BARS[-1].date)
    cache.write_bars(tmp_path, "NVDA", BARS, requested=window)
    assert cache.covered_windows(tmp_path, "NVDA") == [window]
    assert cache.covers(tmp_path, "NVDA", BARS[5].date, BARS[9].date)


def test_coverage_absent_when_never_requested(tmp_path: Path) -> None:
    cache.write_bars(tmp_path, "NVDA", BARS)
    assert cache.covered_windows(tmp_path, "NVDA") == []
    assert not cache.covers(tmp_path, "NVDA", BARS[5].date, BARS[9].date)


def test_contiguous_windows_merge(tmp_path: Path) -> None:
    """Adjacent fetches leave one window: no session can sit between them."""
    cache.write_bars(
        tmp_path, "NVDA", BARS[:100], requested=(BARS[0].date, BARS[99].date)
    )
    cache.write_bars(
        tmp_path, "NVDA", BARS[100:], requested=(BARS[100].date, BARS[-1].date)
    )
    assert cache.covered_windows(tmp_path, "NVDA") == [(BARS[0].date, BARS[-1].date)]
    assert cache.covers(tmp_path, "NVDA", BARS[0].date, BARS[-1].date)


def test_disjoint_windows_do_not_claim_the_gap(tmp_path: Path) -> None:
    """The bug a single [min, max] span would hide.

    Fetching [d0, d99] and later [d200, d-1] leaves everything between them
    unrequested. Merging into one span would report the whole range as covered
    and never re-fetch the hole — the same silently-incomplete failure the
    metadata exists to prevent, moved from the tail into the middle.
    """
    cache.write_bars(
        tmp_path, "NVDA", BARS[:100], requested=(BARS[0].date, BARS[99].date)
    )
    cache.write_bars(
        tmp_path, "NVDA", BARS[200:], requested=(BARS[200].date, BARS[-1].date)
    )
    windows = cache.covered_windows(tmp_path, "NVDA")
    assert len(windows) == 2

    # Each requested window is covered on its own.
    assert cache.covers(tmp_path, "NVDA", BARS[0].date, BARS[99].date)
    assert cache.covers(tmp_path, "NVDA", BARS[200].date, BARS[-1].date)
    # Spanning the gap, or sitting inside it, is not.
    assert not cache.covers(tmp_path, "NVDA", BARS[0].date, BARS[-1].date)
    assert not cache.covers(tmp_path, "NVDA", BARS[120].date, BARS[150].date)


def test_a_later_window_can_close_the_gap(tmp_path: Path) -> None:
    cache.write_bars(
        tmp_path, "NVDA", BARS[:100], requested=(BARS[0].date, BARS[99].date)
    )
    cache.write_bars(
        tmp_path, "NVDA", BARS[200:], requested=(BARS[200].date, BARS[-1].date)
    )
    cache.write_bars(
        tmp_path, "NVDA", BARS[100:200], requested=(BARS[100].date, BARS[199].date)
    )
    assert cache.covered_windows(tmp_path, "NVDA") == [(BARS[0].date, BARS[-1].date)]
    assert cache.covers(tmp_path, "NVDA", BARS[0].date, BARS[-1].date)


def test_coverage_survives_a_write_without_a_window(tmp_path: Path) -> None:
    """A later metadata-less write must not erase what was already proven."""
    cache.write_bars(tmp_path, "NVDA", BARS, requested=(BARS[0].date, BARS[-1].date))
    cache.write_bars(tmp_path, "NVDA", [BARS[10]])
    assert cache.covered_windows(tmp_path, "NVDA") == [(BARS[0].date, BARS[-1].date)]


def test_partial_coverage_is_not_claimed(tmp_path: Path) -> None:
    cache.write_bars(tmp_path, "NVDA", BARS, requested=(BARS[10].date, BARS[20].date))
    assert not cache.covers(tmp_path, "NVDA", BARS[0].date, BARS[20].date)
    assert not cache.covers(tmp_path, "NVDA", BARS[10].date, BARS[-1].date)


def test_refuses_empty_series(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="empty series"):
        cache.write_bars(tmp_path, "NVDA", [])


def test_refuses_symbol_mismatch(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="written under"):
        cache.write_bars(tmp_path, "AAPL", BARS)
