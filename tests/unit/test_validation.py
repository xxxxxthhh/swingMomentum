"""Fail-closed data validation (constitution §12.4, ADR §3.4)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from smm.config.loader import load_config
from smm.core.errors import DataValidationError, FailClosedError
from smm.data.generator import breakout_success
from smm.data.market_events import load_market_event_snapshot
from smm.data.price_events import (
    load_price_event_snapshot,
    load_security_identity_snapshot,
)
from smm.data.validation import (
    EXCHANGE_TZ,
    check_adj_factor,
    check_ordering_and_duplicates,
    check_price_jumps,
    check_session_completeness,
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
    with pytest.raises(DataValidationError, match="no cached sessions in"):
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
    assert check_price_jumps(
        [bar(date(2024, 6, 6), close=100), bar(date(2024, 6, 7), close=108)], cfg=CFG
    ) == ()


def test_echo_price_jump_is_verified_by_edgar_and_identity_snapshots() -> None:
    event_snapshot = load_price_event_snapshot(
        REPO / "configs" / "price_events",
        as_of=date(2026, 7, 23),
        cfg=CFG.price_jump_verification,
    )
    identity_snapshot = load_security_identity_snapshot(
        REPO / "configs" / "security_identities",
        as_of=date(2026, 7, 23),
        cfg=CFG.price_jump_verification,
    )
    bars = [
        Bar(
            symbol="ECHO",
            date=date(2025, 8, 25),
            open=29.950001,
            high=30.070000,
            low=29.340000,
            close=29.879999,
            volume=2_493_700,
            adj_close=29.879999,
            adj_factor=1.0,
        ),
        Bar(
            symbol="ECHO",
            date=date(2025, 8, 26),
            open=54.110001,
            high=55.189999,
            low=50.619999,
            close=50.869999,
            volume=46_579_100,
            adj_close=50.869999,
            adj_factor=1.0,
        ),
    ]

    records = check_price_jumps(
        bars,
        cfg=CFG,
        calendar=[item.date for item in bars],
        price_event_snapshot=event_snapshot,
        identity_snapshot=identity_snapshot,
    )

    assert len(records) == 1
    assert records[0].verification_kind == "price_jump"
    assert records[0].symbol == "ECHO"
    assert records[0].historical_symbol == "SATS"
    assert records[0].session == date(2025, 8, 26)
    assert records[0].previous_close == 29.879999
    assert records[0].raw_close == 50.869999
    assert records[0].move == pytest.approx(0.7024766226)
    assert records[0].threshold == 0.5
    assert records[0].accession_number == "0001415404-25-000035"
    assert records[0].identity_mapping_id == "echostar-sats-echo-2026-06-24"


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


def test_casy_t_minus_one_volume_spike_is_verified_by_official_event() -> None:
    snapshot = load_market_event_snapshot(
        REPO / "configs" / "market_events",
        as_of=date(2026, 4, 8),
        cfg=CFG.volume_spike_verification,
    )
    bars = series(9, start=date(2026, 3, 27), symbol="CASY")
    volumes = [
        300_000,
        312_400,
        320_000,
        330_000,
        338_700,
        340_000,
        350_000,
        360_000,
        8_688_600,
    ]
    bars = [
        bar(item.date, symbol="CASY", volume=volume)
        for item, volume in zip(bars, volumes, strict=True)
    ]
    calendar = [item.date for item in bars] + [date(2026, 4, 9)]

    records = check_volume_anomalies(
        bars,
        cfg=CFG,
        calendar=calendar,
        event_snapshot=snapshot,
    )

    assert len(records) == 1
    assert records[0].symbol == "CASY"
    assert records[0].session == date(2026, 4, 8)
    assert records[0].effective_date == date(2026, 4, 9)
    assert records[0].action == "addition"
    assert records[0].raw_volume == 8_688_600
    assert records[0].median_volume == 338_700
    assert records[0].ratio == pytest.approx(25.6527900797)
    assert records[0].snapshot_id == snapshot.snapshot_id
    assert records[0].snapshot_sha256 == snapshot.sha256


def test_casy_effective_day_volume_spike_is_verified() -> None:
    snapshot = load_market_event_snapshot(
        REPO / "configs" / "market_events",
        as_of=date(2026, 4, 9),
        cfg=CFG.volume_spike_verification,
    )
    bars = series(10, start=date(2026, 3, 27), symbol="CASY")
    bars = [
        bar(item.date, symbol="CASY", volume=volume)
        for item, volume in zip(
            bars,
            [
                300_000,
                312_400,
                320_000,
                330_000,
                338_700,
                340_000,
                350_000,
                360_000,
                370_000,
                8_688_600,
            ],
            strict=True,
        )
    ]

    records = check_volume_anomalies(
        bars,
        cfg=CFG,
        calendar=[item.date for item in bars],
        event_snapshot=snapshot,
    )

    assert len(records) == 1
    assert records[0].session == date(2026, 4, 9)


def test_crh_t_minus_one_volume_spike_uses_canonical_snapshot() -> None:
    snapshot = load_market_event_snapshot(
        REPO / "configs" / "market_events",
        as_of=date(2026, 7, 23),
        cfg=CFG.volume_spike_verification,
    )
    bars = series(9, start=date(2025, 12, 9), symbol="CRH")
    bars = [
        bar(item.date, symbol="CRH", volume=volume)
        for item, volume in zip(
            bars,
            [
                3_800_000,
                4_000_000,
                4_100_000,
                4_200_000,
                4_229_700,
                8_073_100,
                8_465_200,
                9_489_600,
                140_096_300,
            ],
            strict=True,
        )
    ]

    records = check_volume_anomalies(
        bars,
        cfg=CFG,
        calendar=[item.date for item in bars] + [date(2025, 12, 22)],
        event_snapshot=snapshot,
    )

    assert len(records) == 1
    assert records[0].symbol == "CRH"
    assert records[0].session == date(2025, 12, 19)
    assert records[0].effective_date == date(2025, 12, 22)
    assert records[0].raw_volume == 140_096_300
    assert records[0].median_volume == 4_229_700
    assert records[0].ratio == pytest.approx(33.1220417524)
    assert records[0].snapshot_id == "2026-07-23_index_constituent_changes"


def test_eme_t_minus_one_volume_spike_uses_canonical_snapshot() -> None:
    snapshot = load_market_event_snapshot(
        REPO / "configs" / "market_events",
        as_of=date(2026, 7, 23),
        cfg=CFG.volume_spike_verification,
    )
    bars = series(9, start=date(2025, 9, 9), symbol="EME")
    bars = [
        bar(item.date, symbol="EME", volume=volume)
        for item, volume in zip(
            bars,
            [
                340_000,
                350_000,
                360_000,
                365_000,
                368_300,
                381_600,
                706_300,
                837_700,
                10_505_000,
            ],
            strict=True,
        )
    ]

    records = check_volume_anomalies(
        bars,
        cfg=CFG,
        calendar=[item.date for item in bars],
        event_snapshot=snapshot,
    )

    assert len(records) == 1
    assert records[0].symbol == "EME"
    assert records[0].session == date(2025, 9, 19)
    assert records[0].effective_date == date(2025, 9, 22)
    assert records[0].raw_volume == 10_505_000
    assert records[0].median_volume == 368_300
    assert records[0].ratio == pytest.approx(28.5229432528)


def test_fer_t_minus_one_volume_spike_uses_nasdaq100_official_event() -> None:
    snapshot = load_market_event_snapshot(
        REPO / "configs" / "market_events",
        as_of=date(2026, 7, 23),
        cfg=CFG.volume_spike_verification,
    )
    bars = series(9, start=date(2025, 12, 9), symbol="FER")
    volumes = [
        900_000,
        950_000,
        1_000_000,
        1_050_000,
        1_072_900,
        1_100_000,
        2_688_300,
        3_320_700,
        62_023_100,
    ]
    bars = [
        bar(item.date, symbol="FER", volume=volume)
        for item, volume in zip(bars, volumes, strict=True)
    ]

    records = check_volume_anomalies(
        bars,
        cfg=CFG,
        calendar=[item.date for item in bars] + [date(2025, 12, 22)],
        event_snapshot=snapshot,
    )

    assert len(records) == 1
    assert records[0].symbol == "FER"
    assert records[0].session == date(2025, 12, 19)
    assert records[0].effective_date == date(2025, 12, 22)
    assert records[0].index_name == "Nasdaq-100"
    assert records[0].raw_volume == 62_023_100
    assert records[0].median_volume == 1_072_900
    assert records[0].ratio == pytest.approx(57.8088358654)


def test_verified_event_outside_t_minus_one_or_t_window_is_rejected() -> None:
    snapshot = load_market_event_snapshot(
        REPO / "configs" / "market_events",
        as_of=date(2026, 4, 7),
        cfg=CFG.volume_spike_verification,
    )
    bars = series(8, start=date(2026, 3, 27), symbol="CASY")
    bars[-1] = bar(date(2026, 4, 7), symbol="CASY", volume=40_000_000)

    with pytest.raises(DataValidationError, match="no unique official"):
        check_volume_anomalies(
            bars,
            cfg=CFG,
            calendar=[item.date for item in bars] + [date(2026, 4, 8), date(2026, 4, 9)],
            event_snapshot=snapshot,
        )


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


# --- session completeness (issue #10) --------------------------------------


def sessions(n: int, *, start: date = date(2024, 1, 2)) -> list[date]:
    out: list[date] = []
    day = start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def test_complete_series_passes() -> None:
    days = sessions(10)
    check_session_completeness([bar(d) for d in days], calendar=days)


def test_a_hole_is_detected() -> None:
    """The failure that silently shortens SMA200 rather than raising."""
    days = sessions(10)
    holed = [bar(d) for d in days if d != days[4]]
    with pytest.raises(DataValidationError, match="trading session"):
        check_session_completeness(holed, calendar=days)


def test_hole_message_names_the_missing_session() -> None:
    days = sessions(10)
    holed = [bar(d) for d in days if d != days[4]]
    with pytest.raises(DataValidationError, match=days[4].isoformat()):
        check_session_completeness(holed, calendar=days)


def test_a_late_listing_is_not_a_hole() -> None:
    """Sessions before a symbol's first bar are not missing data.

    Treating them as errors would reject every recent IPO in the universe —
    exactly the over-strictness that gets fail-closed routed around.
    """
    days = sessions(10)
    listed_late = [bar(d) for d in days[5:]]
    check_session_completeness(listed_late, calendar=days)


def test_a_delisting_is_not_a_hole() -> None:
    days = sessions(10)
    stopped_early = [bar(d) for d in days[:5]]
    check_session_completeness(stopped_early, calendar=days)


def test_completeness_without_a_calendar_is_skipped() -> None:
    days = sessions(10)
    check_session_completeness([bar(d) for d in days if d != days[4]], calendar=None)


def test_empty_calendar_fails_completeness_too() -> None:
    with pytest.raises(DataValidationError, match="no cached sessions"):
        check_session_completeness([bar(date(2024, 1, 2))], calendar=[])


def test_many_holes_are_summarised() -> None:
    days = sessions(20)
    holed = [bar(d) for d in days if d not in days[2:12]]
    with pytest.raises(DataValidationError, match=r"\+5 more"):
        check_session_completeness(holed, calendar=days)


def test_validate_bars_runs_completeness() -> None:
    """Wired into the full check, not only callable on its own."""
    days = sessions(10)
    holed = [bar(d) for d in days if d != days[4]]
    with pytest.raises(DataValidationError, match="trading session"):
        validate_bars(holed, cfg=CFG, calendar=days)
