"""Fail-closed data validation (constitution §12.4, ADR §3.4)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from smm.config.loader import load_config
from smm.core.errors import DataValidationError, FailClosedError
from smm.data.generator import breakout_success
from smm.data.validation import (
    EXCHANGE_TZ,
    check_adj_factor,
    check_ordering_and_duplicates,
    check_price_jumps,
    check_session_continuity,
    check_session_dates,
    check_split_artefacts,
    check_volume_anomalies,
    to_session_date,
    validate_bars,
)
from smm.domain.models import Bar

REPO = Path(__file__).resolve().parents[2]
CFG = load_config(REPO / "configs" / "smm_v1_0_0.yaml").config.validation


def bar(
    day: date,
    *,
    symbol: str = "X",
    close: float = 100.0,
    volume: float = 1_000_000.0,
    adj_factor: float = 1.0,
) -> Bar:
    return Bar(
        symbol=symbol,
        date=day,
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=volume,
        adj_close=close * adj_factor,
        adj_factor=adj_factor,
    )


def series(n: int = 10, *, start: date = date(2024, 1, 2), **kw) -> list[Bar]:
    out: list[Bar] = []
    day = start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(bar(day, **kw))
        day += timedelta(days=1)
    return out


# --- generated paths must pass everything ---------------------------------


def test_generated_path_is_clean() -> None:
    validate_bars(list(breakout_success().bars), cfg=CFG)


# --- every failure is fail-closed -----------------------------------------


def test_all_failures_are_fail_closed() -> None:
    """DataValidationError must remain a FailClosedError, not a warning."""
    assert issubclass(DataValidationError, FailClosedError)


# --- 时区错误 --------------------------------------------------------------


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(DataValidationError, match="naive datetime"):
        to_session_date(datetime(2024, 6, 7, 16, 0))


def test_aware_datetime_maps_to_eastern_session() -> None:
    """22:00 UTC is still the same Eastern session; 02:00 UTC is the day before."""
    assert to_session_date(datetime(2024, 6, 7, 22, 0, tzinfo=UTC)) == date(2024, 6, 7)
    assert to_session_date(datetime(2024, 6, 8, 2, 0, tzinfo=UTC)) == date(2024, 6, 7)


def test_session_date_is_timezone_independent() -> None:
    """A UTC CI run and a Tokyo run must agree on the session."""
    moment = datetime(2024, 6, 7, 20, 30, tzinfo=UTC)
    assert to_session_date(moment) == to_session_date(moment.astimezone(EXCHANGE_TZ))


def test_plain_date_passes_through() -> None:
    assert to_session_date(date(2024, 6, 7)) == date(2024, 6, 7)


def test_weekend_session_rejected() -> None:
    with pytest.raises(DataValidationError, match="weekend"):
        check_session_dates([bar(date(2024, 6, 8))])


def test_empty_calendar_fails_closed() -> None:
    """An empty calendar means "we know nothing", not "no sessions existed".

    Checking bars against it would reject every one with a misleading message;
    skipping it would silently drop a §12.4 check. It says what is wrong.
    """
    with pytest.raises(DataValidationError, match="benchmark series is not cached"):
        check_session_dates([bar(date(2024, 6, 6))], calendar=[])


def test_absent_calendar_is_skipped_not_failed() -> None:
    """None is a legitimate state — synthetic data has no exchange calendar."""
    check_session_dates([bar(date(2024, 6, 6))], calendar=None)


def test_date_outside_calendar_rejected() -> None:
    bars = [bar(date(2024, 6, 6)), bar(date(2024, 6, 7))]
    with pytest.raises(DataValidationError, match="outside the trading calendar"):
        check_session_dates(bars, calendar=[date(2024, 6, 6)])


# --- 重复记录 / 排序 --------------------------------------------------------


def test_duplicate_session_rejected() -> None:
    day = date(2024, 6, 6)
    with pytest.raises(DataValidationError, match="duplicate session"):
        check_ordering_and_duplicates([bar(day), bar(day)])


def test_out_of_order_rejected() -> None:
    with pytest.raises(DataValidationError, match="out-of-order"):
        check_ordering_and_duplicates([bar(date(2024, 6, 7)), bar(date(2024, 6, 6))])


def test_mixed_symbols_rejected() -> None:
    with pytest.raises(DataValidationError, match="single symbol"):
        check_ordering_and_duplicates(
            [bar(date(2024, 6, 6), symbol="A"), bar(date(2024, 6, 7), symbol="B")]
        )


def test_empty_series_rejected() -> None:
    with pytest.raises(DataValidationError, match="empty"):
        check_ordering_and_duplicates([])


# --- 缺失日期 --------------------------------------------------------------


def test_weekend_gap_is_not_a_hole() -> None:
    check_session_continuity([bar(date(2024, 6, 7)), bar(date(2024, 6, 10))], cfg=CFG)


def test_long_gap_rejected() -> None:
    with pytest.raises(DataValidationError, match="weekdays missing"):
        check_session_continuity([bar(date(2024, 6, 3)), bar(date(2024, 6, 21))], cfg=CFG)


# --- 单日异常跳变 ----------------------------------------------------------


def test_abnormal_jump_rejected() -> None:
    bars = [bar(date(2024, 6, 6), close=100), bar(date(2024, 6, 7), close=210)]
    with pytest.raises(DataValidationError, match="verify corporate actions"):
        check_price_jumps(bars, cfg=CFG)


def test_ordinary_move_accepted() -> None:
    check_price_jumps(
        [bar(date(2024, 6, 6), close=100), bar(date(2024, 6, 7), close=108)], cfg=CFG
    )


# --- 成交量异常 ------------------------------------------------------------


def test_zero_volume_rejected() -> None:
    bars = series(5)
    bars[2] = bar(bars[2].date, volume=0)
    with pytest.raises(DataValidationError, match="zero volume"):
        check_volume_anomalies(bars, cfg=CFG)


def test_volume_spike_rejected() -> None:
    bars = series(9)
    bars[4] = bar(bars[4].date, volume=1_000_000 * 40)
    with pytest.raises(DataValidationError, match="the median"):
        check_volume_anomalies(bars, cfg=CFG)


# --- 复权因子异常 ----------------------------------------------------------


def test_adj_factor_above_one_rejected() -> None:
    with pytest.raises(DataValidationError, match="outside"):
        check_adj_factor([bar(date(2024, 6, 6), adj_factor=1.5)], cfg=CFG)


def test_adj_factor_must_not_decrease_toward_present() -> None:
    """A falling factor means two adjustment vintages were spliced together."""
    bars = [
        bar(date(2024, 6, 6), adj_factor=0.99),
        bar(date(2024, 6, 7), adj_factor=0.95),
    ]
    with pytest.raises(DataValidationError, match="mixed adjustment vintages"):
        check_adj_factor(bars, cfg=CFG)


def test_adj_factor_rising_toward_present_is_fine() -> None:
    check_adj_factor(
        [bar(date(2024, 6, 6), adj_factor=0.95), bar(date(2024, 6, 7), adj_factor=0.99)],
        cfg=CFG,
    )


# --- ADR §3.4 拆股伪信号 ---------------------------------------------------


def test_unadjusted_split_rejected() -> None:
    """NVDA's 10:1 shape: close /= 10 and volume *= 10 on the same session.

    Left undetected the volume step depresses the trailing average and
    manufactures a relative-volume breakout that never happened.
    """
    bars = [
        bar(date(2024, 6, 6), close=1200, volume=40_000_000),
        bar(date(2024, 6, 7), close=120, volume=400_000_000),
    ]
    with pytest.raises(DataValidationError, match="unadjusted 10:1 split"):
        check_split_artefacts(bars, cfg=CFG)


def test_unadjusted_reverse_split_rejected() -> None:
    bars = [
        bar(date(2024, 6, 6), close=2.0, volume=20_000_000),
        bar(date(2024, 6, 7), close=10.0, volume=4_000_000),
    ]
    with pytest.raises(DataValidationError, match="unadjusted 5:1 reverse split"):
        check_split_artefacts(bars, cfg=CFG)


def test_volume_doubling_alone_is_not_a_split() -> None:
    """Volume doubling day-over-day is ordinary. Flagging it would halt the run
    constantly, which is why detection needs the price leg too."""
    check_split_artefacts(
        [
            bar(date(2024, 6, 6), close=100, volume=1_000_000),
            bar(date(2024, 6, 7), close=102, volume=2_000_000),
        ],
        cfg=CFG,
    )


def test_price_halving_alone_is_not_flagged_as_split() -> None:
    """A crash without the matching volume leg is a price event, not a split.

    check_price_jumps is what catches this; the split check must not also claim
    it, or the operator gets a misleading diagnosis.
    """
    check_split_artefacts(
        [
            bar(date(2024, 6, 6), close=100, volume=1_000_000),
            bar(date(2024, 6, 7), close=50, volume=1_100_000),
        ],
        cfg=CFG,
    )
