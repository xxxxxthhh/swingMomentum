from __future__ import annotations

from datetime import date

import pytest

from smm.core.errors import DataValidationError
from smm.domain.models import Bar
from smm.paper.prints import SplitAction, SplitActionHistory, rebuild_print_bars


def _bar(
    session: date,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> Bar:
    return Bar(
        symbol="NVDA",
        date=session,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        adj_close=close,
        adj_factor=1.0,
    )


def _history(*, actions: tuple[SplitAction, ...]) -> SplitActionHistory:
    return SplitActionHistory(
        symbol="NVDA",
        requested_start=date(2024, 6, 7),
        requested_end=date(2024, 6, 10),
        coverage_start=date(2024, 1, 1),
        coverage_end=date(2024, 6, 10),
        observation_cutoff=date(2024, 6, 10),
        actions=actions,
    )


def test_rebuilds_true_prints_across_known_split_with_action_date_boundary() -> None:
    bars = (
        _bar(
            date(2024, 6, 7),
            open_=50.0,
            high=55.0,
            low=48.0,
            close=52.0,
            volume=1_000.0,
        ),
        _bar(
            date(2024, 6, 10),
            open_=53.0,
            high=56.0,
            low=51.0,
            close=55.0,
            volume=1_200.0,
        ),
    )
    history = _history(
        actions=(
            SplitAction(
                action_id="nvda-2024-06-10-10-for-1",
                symbol="NVDA",
                action_date=date(2024, 6, 10),
                split_ratio="10",
            ),
        )
    )

    prints = rebuild_print_bars(bars, history=history)

    assert [(bar.date, bar.open, bar.high, bar.low, bar.close, bar.volume) for bar in prints] == [
        (date(2024, 6, 7), 500.0, 550.0, 480.0, 520.0, 100.0),
        (date(2024, 6, 10), 53.0, 56.0, 51.0, 55.0, 1_200.0),
    ]


@pytest.mark.parametrize(
    "history",
    [
        SplitActionHistory(
            symbol="NVDA",
            requested_start=date(2024, 6, 7),
            requested_end=date(2024, 6, 10),
            coverage_start=date(2024, 6, 8),
            coverage_end=date(2024, 6, 10),
            observation_cutoff=date(2024, 6, 10),
            actions=(),
        ),
        SplitActionHistory(
            symbol="NVDA",
            requested_start=date(2024, 6, 7),
            requested_end=date(2024, 6, 10),
            coverage_start=date(2024, 1, 1),
            coverage_end=date(2024, 6, 7),
            observation_cutoff=date(2024, 6, 10),
            actions=(),
        ),
    ],
)
def test_rejects_incomplete_split_history_coverage(history: SplitActionHistory) -> None:
    bars = (_bar(date(2024, 6, 7), open_=50.0, high=55.0, low=48.0, close=52.0, volume=1_000),)

    with pytest.raises(DataValidationError, match="coverage"):
        rebuild_print_bars(bars, history=history)


def test_rejects_duplicate_split_action_identity() -> None:
    action = SplitAction(
        action_id="nvda-2024-06-10-10-for-1",
        symbol="NVDA",
        action_date=date(2024, 6, 10),
        split_ratio="10",
    )
    bars = (_bar(date(2024, 6, 7), open_=50.0, high=55.0, low=48.0, close=52.0, volume=1_000),)

    with pytest.raises(DataValidationError, match="duplicate split action"):
        rebuild_print_bars(bars, history=_history(actions=(action, action)))


def test_rejects_split_action_for_an_unknown_symbol() -> None:
    bars = (_bar(date(2024, 6, 7), open_=50.0, high=55.0, low=48.0, close=52.0, volume=1_000),)
    history = _history(
        actions=(
            SplitAction(
                action_id="amd-2024-06-10-10-for-1",
                symbol="AMD",
                action_date=date(2024, 6, 10),
                split_ratio="10",
            ),
        )
    )

    with pytest.raises(DataValidationError, match="does not match history symbol"):
        rebuild_print_bars(bars, history=history)


def test_rejects_provider_bar_outside_requested_interval() -> None:
    bars = (_bar(date(2024, 6, 6), open_=50.0, high=55.0, low=48.0, close=52.0, volume=1_000),)

    with pytest.raises(DataValidationError, match="outside requested interval"):
        rebuild_print_bars(bars, history=_history(actions=()))
