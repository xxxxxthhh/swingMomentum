"""yfinance provider (ADR 2026-07-22 §1, §3.4).

Everything here except the ``network`` block runs offline.
"""

from __future__ import annotations

import builtins
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.data import cache
from smm.data.generator import breakout_success
from smm.data.yfinance_provider import YFinanceProvider
from smm.domain.models import Bar

REPO = Path(__file__).resolve().parents[2]
CONFIG = load_config(REPO / "configs" / "smm_v1_0_0.yaml").config


def build(
    tmp_path: Path,
    *,
    sleeper=None,
    attempt_logger=None,
) -> YFinanceProvider:
    kwargs = {}
    if sleeper is not None:
        kwargs["sleeper"] = sleeper
    if attempt_logger is not None:
        kwargs["attempt_logger"] = attempt_logger
    return YFinanceProvider(
        cache_dir=tmp_path / "cache",
        universe_dir=REPO / "configs" / "universe",
        validation=CONFIG.validation,
        retry=CONFIG.market_data_retry,
        max_snapshot_age_days=CONFIG.universe.max_snapshot_age_days,
        market_events_dir=REPO / "configs" / "market_events",
        price_events_dir=REPO / "configs" / "price_events",
        security_identities_dir=REPO / "configs" / "security_identities",
        **kwargs,
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


def _never_fetch(*args, **kwargs):  # pragma: no cover - must never run
    raise AssertionError("fetch attempted for a range already recorded as covered")


def test_recorded_coverage_is_served_without_fetching(tmp_path: Path) -> None:
    """A request inside a previously *requested* window must not hit the network."""
    provider = build(tmp_path)
    bars = list(breakout_success().bars)
    cache.write_bars(
        tmp_path / "cache", "NVDA", bars, requested=(bars[0].date, bars[-1].date)
    )
    benchmark = [item.model_copy(update={"symbol": "SPY"}) for item in bars]
    cache.write_bars(
        tmp_path / "cache",
        "SPY",
        benchmark,
        requested=(benchmark[0].date, benchmark[-1].date),
    )
    provider.fetch = _never_fetch  # type: ignore[method-assign]
    served = provider.get_daily_bars("NVDA", bars[10].date, bars[20].date)
    assert [b.date for b in served] == [b.date for b in bars[10:21]]


def test_cached_fisv_hole_is_repaired_and_replayed_with_official_evidence(
    tmp_path: Path,
) -> None:
    days = [date(2025, 11, 11), date(2025, 11, 12), date(2025, 11, 13)]
    evidence_as_of = date(2026, 7, 23)

    def make(symbol: str, session: date, close: float, volume: float) -> Bar:
        return Bar(
            symbol=symbol,
            date=session,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
            adj_close=close,
            adj_factor=1.0,
        )

    spy = [make("SPY", session, 100.0, 1_000_000) for session in days]
    fisv = [
        make("FISV", days[0], 64.260002, 5_427_200),
        make("FISV", days[2], 64.529999, 9_274_500),
    ]
    cache.write_bars(
        tmp_path / "cache",
        "SPY",
        spy,
        requested=(days[0], evidence_as_of),
    )
    cache.write_bars(
        tmp_path / "cache",
        "FISV",
        fisv,
        requested=(days[0], evidence_as_of),
    )
    provider = YFinanceProvider(
        cache_dir=tmp_path / "cache",
        universe_dir=REPO / "configs" / "universe",
        validation=CONFIG.validation,
        retry=CONFIG.market_data_retry,
        max_snapshot_age_days=CONFIG.universe.max_snapshot_age_days,
        market_events_dir=REPO / "configs" / "market_events",
        price_events_dir=REPO / "configs" / "price_events",
        security_identities_dir=REPO / "configs" / "security_identities",
        official_bar_supplements_dir=REPO / "configs" / "official_bar_supplements",
    )
    provider.fetch = _never_fetch  # type: ignore[method-assign]

    repaired = provider.get_daily_bars("FISV", days[0], evidence_as_of)

    assert [bar.date for bar in repaired] == days
    payloads = [record.to_payload() for record in provider.market_data_verifications()]
    assert payloads[0]["verification_kind"] == "official_bar_supplement"
    assert payloads[0]["raw_close"] == "64.380000"
    assert provider.market_data_snapshot_identities()["official_bar_supplement"] == {
        "id": "2026-07-23_official_bar_supplements",
        "sha256": payloads[0]["snapshot_sha256"],
    }


def test_run_before_first_official_bar_snapshot_does_not_require_future_evidence(
    tmp_path: Path,
) -> None:
    days = [date(2025, 1, 2), date(2025, 1, 3)]
    bars = [
        Bar(
            symbol=symbol,
            date=session,
            open=100,
            high=100,
            low=100,
            close=100,
            volume=1_000_000,
            adj_close=100,
            adj_factor=1,
        )
        for symbol in ("SPY", "NVDA")
        for session in days
    ]
    for symbol in ("SPY", "NVDA"):
        symbol_bars = [bar for bar in bars if bar.symbol == symbol]
        cache.write_bars(
            tmp_path / "cache",
            symbol,
            symbol_bars,
            requested=(days[0], days[-1]),
        )
    provider = YFinanceProvider(
        cache_dir=tmp_path / "cache",
        universe_dir=REPO / "configs" / "universe",
        validation=CONFIG.validation,
        retry=CONFIG.market_data_retry,
        max_snapshot_age_days=CONFIG.universe.max_snapshot_age_days,
        official_bar_supplements_dir=REPO / "configs" / "official_bar_supplements",
    )
    provider.fetch = _never_fetch  # type: ignore[method-assign]

    assert [bar.date for bar in provider.get_daily_bars("NVDA", days[0], days[-1])] == days
    assert provider.market_data_verifications() == ()


def test_cached_casy_spike_reproduces_the_same_official_evidence(
    tmp_path: Path,
) -> None:
    days = [
        date(2026, 3, 27),
        date(2026, 3, 30),
        date(2026, 3, 31),
        date(2026, 4, 1),
        date(2026, 4, 2),
        date(2026, 4, 3),
        date(2026, 4, 6),
        date(2026, 4, 7),
        date(2026, 4, 8),
    ]
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

    def make(symbol: str, session: date, volume: float) -> Bar:
        return Bar(
            symbol=symbol,
            date=session,
            open=100,
            high=101,
            low=99,
            close=100,
            volume=volume,
            adj_close=100,
            adj_factor=1,
        )

    casy = [
        make("CASY", session, volume)
        for session, volume in zip(days, volumes, strict=True)
    ]
    spy = [make("SPY", session, 1_000_000) for session in days]
    for symbol, bars in (("SPY", spy), ("CASY", casy)):
        cache.write_bars(
            tmp_path / "cache",
            symbol,
            bars,
            requested=(days[0], days[-1]),
        )

    first = build(tmp_path)
    first.fetch = _never_fetch  # type: ignore[method-assign]
    first.get_daily_bars("CASY", days[0], days[-1])
    first_payload = [record.to_payload() for record in first.market_data_verifications()]

    second = build(tmp_path)
    second.fetch = _never_fetch  # type: ignore[method-assign]
    second.get_daily_bars("CASY", days[0], days[-1])

    assert [record.to_payload() for record in second.market_data_verifications()] == first_payload
    assert second.market_event_snapshot_identity() == first.market_event_snapshot_identity()


def test_cached_fer_spike_reproduces_nasdaq100_official_evidence(
    tmp_path: Path,
) -> None:
    days = [
        date(2025, 12, 9),
        date(2025, 12, 10),
        date(2025, 12, 11),
        date(2025, 12, 12),
        date(2025, 12, 15),
        date(2025, 12, 16),
        date(2025, 12, 17),
        date(2025, 12, 18),
        date(2025, 12, 19),
    ]
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
    evidence_as_of = date(2026, 7, 23)

    def make(symbol: str, session: date, volume: float) -> Bar:
        return Bar(
            symbol=symbol,
            date=session,
            open=100,
            high=101,
            low=99,
            close=100,
            volume=volume,
            adj_close=100,
            adj_factor=1,
        )

    fer = [
        make("FER", session, volume)
        for session, volume in zip(days, volumes, strict=True)
    ]
    spy = [make("SPY", session, 1_000_000) for session in days]
    for symbol, bars in (("SPY", spy), ("FER", fer)):
        cache.write_bars(
            tmp_path / "cache",
            symbol,
            bars,
            requested=(days[0], evidence_as_of),
        )

    first = build(tmp_path)
    first.fetch = _never_fetch  # type: ignore[method-assign]
    first.get_daily_bars("FER", days[0], evidence_as_of)
    first_payload = [record.to_payload() for record in first.market_data_verifications()]

    second = build(tmp_path)
    second.fetch = _never_fetch  # type: ignore[method-assign]
    second.get_daily_bars("FER", days[0], evidence_as_of)

    assert [record.to_payload() for record in second.market_data_verifications()] == first_payload
    assert first_payload[0]["index_name"] == "Nasdaq-100"
    assert first_payload[0]["raw_volume"] == "62023100.000000"
    assert second.market_event_snapshot_identity() == first.market_event_snapshot_identity()


def test_cached_echo_jump_reproduces_edgar_and_identity_evidence(
    tmp_path: Path,
) -> None:
    days = [date(2025, 8, 25), date(2025, 8, 26)]
    evidence_as_of = date(2026, 7, 23)

    def make(
        symbol: str,
        session: date,
        *,
        close: float,
        volume: float,
    ) -> Bar:
        return Bar(
            symbol=symbol,
            date=session,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
            adj_close=close,
            adj_factor=1,
        )

    echo = [
        make("ECHO", days[0], close=29.879999, volume=2_493_700),
        make("ECHO", days[1], close=50.869999, volume=46_579_100),
    ]
    spy = [
        make("SPY", day, close=650, volume=50_000_000)
        for day in days
    ]
    for symbol, bars in (("SPY", spy), ("ECHO", echo)):
        cache.write_bars(
            tmp_path / "cache",
            symbol,
            bars,
            requested=(days[0], evidence_as_of),
        )

    provider = build(tmp_path)
    provider.fetch = _never_fetch  # type: ignore[method-assign]
    provider.get_daily_bars("ECHO", days[0], evidence_as_of)

    records = provider.market_data_verifications()
    assert len(records) == 1
    assert records[0].to_payload()["verification_kind"] == "price_jump"
    assert provider.market_data_snapshot_identities() == {
        "price_event": {
            "id": "2026-07-23_edgar_item_1_01",
            "sha256": records[0].to_payload()["price_event_snapshot_sha256"],
        },
        "security_identity": {
            "id": "2026-07-23_symbol_mappings",
            "sha256": records[0].to_payload()["security_identity_snapshot_sha256"],
        },
    }


def test_bars_present_without_recorded_coverage_still_refetch(tmp_path: Path) -> None:
    """Bars alone do not prove a range is complete.

    The old check compared the first and last cached dates against the request
    with four days of slack, so a truncated tail could pass as covered. Presence
    is now not enough — only a recorded request window counts.
    """
    provider = build(tmp_path)
    bars = list(breakout_success().bars)
    cache.write_bars(tmp_path / "cache", "NVDA", bars)  # no `requested=`
    provider.fetch = _never_fetch  # type: ignore[method-assign]
    with pytest.raises(AssertionError, match="fetch attempted"):
        provider.get_daily_bars("NVDA", bars[10].date, bars[20].date)


def test_request_beyond_recorded_coverage_refetches(tmp_path: Path) -> None:
    """Asking past the recorded tail must not be served from a stale cache."""
    provider = build(tmp_path)
    bars = list(breakout_success().bars)
    cache.write_bars(
        tmp_path / "cache", "NVDA", bars[:200], requested=(bars[0].date, bars[199].date)
    )
    provider.fetch = _never_fetch  # type: ignore[method-assign]
    with pytest.raises(AssertionError, match="fetch attempted"):
        provider.get_daily_bars("NVDA", bars[0].date, bars[-1].date)


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


# --- row-to-bar validation -------------------------------------------------
#
# Bar's own OHLC invariants raise a bare pydantic ValidationError, which is
# not a FailClosedError and carries no symbol/date. Left unwrapped, the CLI's
# fail-closed handlers (which only catch FailClosedError) would miss it and
# an operator would see a raw traceback instead of a clean stop.


def test_row_with_low_above_open_is_rejected_with_symbol_and_date() -> None:
    row = {
        "Open": 172.77,
        "High": 176.61,
        "Low": 172.97,  # low > open — violates the Bar invariant
        "Close": 176.61,
        "Adj Close": 176.61,
        "Volume": 1_000_000,
    }
    with pytest.raises(DataValidationError, match=r"ANET: 2026-07-23 invalid bar"):
        YFinanceProvider._row_to_bar("ANET", date(2026, 7, 23), row)


# --- split-action history ------------------------------------------------


class _ActionColumns(list[str]):
    """List-shaped columns with the yfinance ``nlevels`` attribute."""

    nlevels = 1


class _ActionFrame:
    """Small offline stand-in for the portion of a yfinance frame we consume."""

    def __init__(self, rows: list[tuple[date, dict[str, object]]]) -> None:
        self._rows = rows
        self.empty = not rows
        self.columns = _ActionColumns({key for _, row in rows for key in row})

    def iterrows(self):
        return iter(self._rows)


def _install_yfinance(monkeypatch: pytest.MonkeyPatch, frame: _ActionFrame) -> list[dict]:
    calls: list[dict] = []

    def download(symbol: str, **kwargs) -> _ActionFrame:
        calls.append({"symbol": symbol, **kwargs})
        return frame

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=download))
    return calls


def _install_yfinance_sequence(
    monkeypatch: pytest.MonkeyPatch, responses: list[object]
) -> list[dict]:
    calls: list[dict] = []
    pending = iter(responses)

    def download(symbol: str, **kwargs):
        calls.append({"symbol": symbol, **kwargs})
        response = next(pending)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=download))
    return calls


def _valid_price_frame() -> _ActionFrame:
    rows = []
    for bar in breakout_success().bars:
        rows.append(
            (
                bar.date,
                {
                    "Open": bar.open,
                    "High": bar.high,
                    "Low": bar.low,
                    "Close": bar.close,
                    "Adj Close": bar.adj_close,
                    "Volume": bar.volume,
                },
            )
        )
    return _ActionFrame(rows)


def test_primary_bars_recover_on_second_attempt_with_structured_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = _ActionFrame([])
    valid = _valid_price_frame()
    calls = _install_yfinance_sequence(monkeypatch, [empty, valid])
    sleeps: list[float] = []
    logs: list[str] = []
    provider = build(tmp_path, sleeper=sleeps.append, attempt_logger=logs.append)
    first, last = valid._rows[0][0], valid._rows[-1][0]

    bars = provider.get_daily_bars("SPY", first, last)

    assert len(calls) == 2
    assert sleeps == [2.0]
    assert len(bars) == len(valid._rows)
    assert [json.loads(item)["outcome"] for item in logs] == [
        "retryable_failure",
        "success",
    ]
    assert json.loads(logs[0])["error_category"] == "provider_empty"
    assert cache.covers(tmp_path / "cache", "SPY", first, last)


def test_primary_bars_exhaust_three_attempts_without_caching_invalid_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = _ActionFrame([])
    calls = _install_yfinance_sequence(monkeypatch, [empty, empty, empty])
    sleeps: list[float] = []
    logs: list[str] = []
    provider = build(tmp_path, sleeper=sleeps.append, attempt_logger=logs.append)
    start, end = date(2024, 1, 2), date(2024, 1, 31)

    with pytest.raises(DataValidationError, match="attempts: 1/provider_empty.*3/provider_empty"):
        provider.get_daily_bars("SPY", start, end)

    assert len(calls) == 3
    assert sleeps == [2.0, 8.0]
    assert len(logs) == 3
    assert not cache.covers(tmp_path / "cache", "SPY", start, end)


def test_ohlc_invalid_payload_is_refetched_and_never_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid = _valid_price_frame()
    first_day, first_row = invalid._rows[0]
    first_row["Low"] = float(first_row["High"]) + 1.0
    calls = _install_yfinance_sequence(monkeypatch, [invalid, invalid, invalid])
    provider = build(tmp_path, sleeper=lambda _seconds: None)
    last_day = invalid._rows[-1][0]

    with pytest.raises(DataValidationError) as exc_info:
        provider.get_daily_bars("SPY", first_day, last_day)

    message = str(exc_info.value)
    assert message.index("1/provider_normalization") < message.index(
        "3/provider_normalization"
    )
    assert len(calls) == 3
    assert not cache.covers(tmp_path / "cache", "SPY", first_day, last_day)


def test_missing_optional_dependency_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(sys.modules, "yfinance", raising=False)
    original_import = builtins.__import__
    imports = 0

    def missing_yfinance(name, *args, **kwargs):
        nonlocal imports
        if name == "yfinance":
            imports += 1
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_yfinance)
    sleeps: list[float] = []

    with pytest.raises(DataValidationError, match="market extra"):
        build(tmp_path, sleeper=sleeps.append).fetch(
            "SPY", date(2024, 1, 2), date(2024, 1, 31)
        )

    assert imports == 1
    assert sleeps == []


def test_unknown_member_calendar_fails_before_any_provider_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_yfinance_sequence(monkeypatch, [_valid_price_frame()])
    sleeps: list[float] = []

    with pytest.raises(DataValidationError, match="provider retries cannot establish"):
        build(tmp_path, sleeper=sleeps.append).get_daily_bars(
            "AAPL", date(2024, 1, 2), date(2024, 1, 31)
        )

    assert calls == []
    assert sleeps == []


def test_fetch_split_action_history_normalises_yahoo_actions_and_checks_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_yfinance(
        monkeypatch,
        _ActionFrame(
            [
                (date(2024, 6, 7), {"Stock Splits": 0.0}),
                (date(2024, 6, 10), {"Stock Splits": 10.0}),
            ]
        ),
    )

    history = build(tmp_path).fetch_split_action_history(
        "nvda",
        date(2024, 6, 7),
        date(2024, 6, 10),
        observation_cutoff=date(2024, 6, 10),
        expected_sessions=(date(2024, 6, 7), date(2024, 6, 10)),
    )

    assert history.symbol == "NVDA"
    assert history.coverage_start == date(2024, 6, 7)
    assert history.coverage_end == date(2024, 6, 10)
    actual_actions = [
        (action.action_id, action.action_date, str(action.split_ratio))
        for action in history.actions
    ]
    assert actual_actions == [
        ("yahoo:NVDA:2024-06-10:stock-split", date(2024, 6, 10), "10.0")
    ]
    assert calls == [
        {
            "symbol": "nvda",
            "start": "2024-06-07",
            "end": "2024-06-11",
            "auto_adjust": False,
            "actions": True,
            "progress": False,
            "threads": False,
        }
    ]


@pytest.mark.parametrize(
    ("frame", "match"),
    [
        (_ActionFrame([(date(2024, 6, 7), {"Close": 120.0})]), "Stock Splits"),
        (
            _ActionFrame([(date(2024, 6, 7), {"Stock Splits": 0.0})]),
            "missing expected sessions",
        ),
        (_ActionFrame([(date(2024, 6, 7), {"Stock Splits": -2.0})]), "positive"),
        (
            _ActionFrame(
                [
                    (date(2024, 6, 7), {"Stock Splits": 0.0}),
                    (date(2024, 6, 7), {"Stock Splits": 0.0}),
                ]
            ),
            "duplicate provider session",
        ),
    ],
)
def test_fetch_split_action_history_fails_closed_for_unverifiable_action_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frame: _ActionFrame,
    match: str,
) -> None:
    _install_yfinance(monkeypatch, frame)

    with pytest.raises(DataValidationError, match=match):
        build(tmp_path).fetch_split_action_history(
            "NVDA",
            date(2024, 6, 7),
            date(2024, 6, 10),
            observation_cutoff=date(2024, 6, 10),
            expected_sessions=(date(2024, 6, 7), date(2024, 6, 10)),
        )


def test_fetch_split_action_history_rejects_unexpected_provider_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_yfinance(
        monkeypatch,
        _ActionFrame(
            [
                (date(2024, 6, 7), {"Stock Splits": 0.0}),
                (date(2024, 6, 10), {"Stock Splits": 0.0}),
            ]
        ),
    )

    with pytest.raises(DataValidationError, match="unexpected provider sessions"):
        build(tmp_path).fetch_split_action_history(
            "NVDA",
            date(2024, 6, 7),
            date(2024, 6, 7),
            observation_cutoff=date(2024, 6, 10),
            expected_sessions=(date(2024, 6, 7),),
        )


def test_fetch_split_action_history_rejects_inverted_request_interval(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="start must not be after end"):
        build(tmp_path).fetch_split_action_history(
            "NVDA",
            date(2024, 6, 10),
            date(2024, 6, 7),
            observation_cutoff=date(2024, 6, 10),
            expected_sessions=(date(2024, 6, 7),),
        )


def test_fetch_split_action_history_rejects_cutoff_before_requested_end(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="cutoff must cover requested end"):
        build(tmp_path).fetch_split_action_history(
            "NVDA",
            date(2024, 6, 7),
            date(2024, 6, 10),
            observation_cutoff=date(2024, 6, 9),
            expected_sessions=(date(2024, 6, 7),),
        )


def test_fetch_split_action_history_rejects_non_finite_split_ratio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_yfinance(
        monkeypatch,
        _ActionFrame(
            [
                (date(2024, 6, 7), {"Stock Splits": float("nan")}),
                (date(2024, 6, 10), {"Stock Splits": 0.0}),
            ]
        ),
    )

    with pytest.raises(DataValidationError, match="non-finite"):
        build(tmp_path).fetch_split_action_history(
            "NVDA",
            date(2024, 6, 7),
            date(2024, 6, 10),
            observation_cutoff=date(2024, 6, 10),
            expected_sessions=(date(2024, 6, 7), date(2024, 6, 10)),
        )


def test_fetch_split_action_history_rejects_empty_expected_sessions(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="non-empty expected sessions"):
        build(tmp_path).fetch_split_action_history(
            "NVDA",
            date(2024, 6, 7),
            date(2024, 6, 10),
            observation_cutoff=date(2024, 6, 10),
            expected_sessions=(),
        )


def test_fetch_split_action_history_rejects_duplicate_expected_session(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="duplicate expected split-history session"):
        build(tmp_path).fetch_split_action_history(
            "NVDA",
            date(2024, 6, 7),
            date(2024, 6, 10),
            observation_cutoff=date(2024, 6, 10),
            expected_sessions=(date(2024, 6, 7), date(2024, 6, 7)),
        )


def test_fetch_split_action_history_rejects_out_of_range_expected_session(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="outside split-history query"):
        build(tmp_path).fetch_split_action_history(
            "NVDA",
            date(2024, 6, 7),
            date(2024, 6, 10),
            observation_cutoff=date(2024, 6, 10),
            expected_sessions=(date(2024, 6, 6),),
        )


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


# --- calendar is a provider contract, not a calling convention -------------


def _stub_fetch(bars):
    """A fetch that returns fixed bars but still runs the real validation."""

    def fetch(self, symbol, start, end, *, calendar=None):
        from smm.data.validation import validate_bars

        validate_bars(bars, cfg=self._validation, calendar=calendar)
        return bars

    return fetch


def test_member_without_a_cached_benchmark_fails_closed(tmp_path: Path) -> None:
    """The bypass this guards: returning None here would let a member validate
    with no calendar, get cached WITH its coverage recorded, and then be served
    from that cache forever without ever being checked."""
    provider = build(tmp_path)
    bars = [b.model_copy(update={"symbol": "AAPL"}) for b in breakout_success().bars]
    provider.fetch = _stub_fetch(bars).__get__(provider)  # type: ignore[method-assign]

    with pytest.raises(DataValidationError, match="empty trading calendar"):
        provider.get_daily_bars("AAPL", bars[0].date, bars[-1].date)

    # And crucially: nothing was written, so no coverage was recorded either.
    assert cache.read_bars(tmp_path / "cache", "AAPL") == []
    assert cache.covered_windows(tmp_path / "cache", "AAPL") == []


def test_member_outside_the_benchmarks_cached_window_fails_closed(tmp_path: Path) -> None:
    """Benchmark file present but no sessions in this window.

    `get_calendar(...) or None` would have turned this empty list back into a
    skip, which is the same bypass by another route.
    """
    provider = build(tmp_path)
    spy = [b.model_copy(update={"symbol": "SPY"}) for b in breakout_success().bars]
    cache.write_bars(
        tmp_path / "cache", "SPY", spy[:50], requested=(spy[0].date, spy[49].date)
    )
    member = [b.model_copy(update={"symbol": "AAPL"}) for b in breakout_success().bars]
    provider.fetch = _stub_fetch(member[200:]).__get__(provider)  # type: ignore[method-assign]

    with pytest.raises(DataValidationError, match="empty trading calendar"):
        provider.get_daily_bars("AAPL", member[200].date, member[-1].date)
    assert cache.read_bars(tmp_path / "cache", "AAPL") == []


def test_the_benchmark_itself_bootstraps_without_a_calendar(tmp_path: Path) -> None:
    """The single legitimate skip: it defines the calendar."""
    provider = build(tmp_path)
    spy = [b.model_copy(update={"symbol": "SPY"}) for b in breakout_success().bars]
    provider.fetch = _stub_fetch(spy).__get__(provider)  # type: ignore[method-assign]

    served = provider.get_daily_bars("SPY", spy[0].date, spy[-1].date)
    assert len(served) == len(spy)


def test_member_validates_once_the_benchmark_is_cached(tmp_path: Path) -> None:
    provider = build(tmp_path)
    spy = [b.model_copy(update={"symbol": "SPY"}) for b in breakout_success().bars]
    cache.write_bars(tmp_path / "cache", "SPY", spy, requested=(spy[0].date, spy[-1].date))
    member = [b.model_copy(update={"symbol": "AAPL"}) for b in breakout_success().bars]
    provider.fetch = _stub_fetch(member).__get__(provider)  # type: ignore[method-assign]

    served = provider.get_daily_bars("AAPL", member[0].date, member[-1].date)
    assert len(served) == len(member)


def test_calendar_for_returns_none_only_for_the_benchmark(tmp_path: Path) -> None:
    provider = build(tmp_path)
    spy = [b.model_copy(update={"symbol": "SPY"}) for b in breakout_success().bars]
    window = (spy[0].date, spy[-1].date)

    assert provider._calendar_for("SPY", *window) is None
    assert provider._calendar_for("AAPL", *window) == []  # fail-closed, not a skip

    cache.write_bars(tmp_path / "cache", "SPY", spy, requested=window)
    assert provider._calendar_for("AAPL", *window) == [b.date for b in spy]
