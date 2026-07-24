"""Offline official-exchange repair for exact isolated provider holes."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.data.official_bar_supplements import (
    load_official_bar_supplement_snapshot,
    reconcile_official_bar_supplements,
)
from smm.domain.models import Bar

REPO = Path(__file__).resolve().parents[2]
CONFIG = load_config(REPO / "configs" / "smm_v1_0_0.yaml").config


def _bar(
    session: date,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    adj_close: float | None = None,
) -> Bar:
    return Bar(
        symbol="FISV",
        date=session,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        adj_close=close if adj_close is None else adj_close,
        adj_factor=1.0,
    )


def _provider_neighbors() -> list[Bar]:
    return [
        _bar(
            date(2025, 11, 11),
            open_=63.599998,
            high=64.480003,
            low=62.84,
            close=64.26000213623047,
            volume=5_427_200,
        ),
        _bar(
            date(2025, 11, 13),
            open_=64.849998,
            high=66.949997,
            low=64.370003,
            close=64.52999877929688,
            volume=9_274_500,
        ),
    ]


def test_fisv_exact_isolated_hole_uses_official_nasdaq_snapshot() -> None:
    snapshot = load_official_bar_supplement_snapshot(
        REPO / "configs" / "official_bar_supplements",
        as_of=date(2026, 7, 23),
        cfg=CONFIG.validation.official_bar_supplement,
    )
    bars = [
        _bar(
            date(2025, 11, 11),
            open_=63.599998,
            high=64.480003,
            low=62.84,
            close=64.26000213623047,
            volume=5_427_200,
        ),
        _bar(
            date(2025, 11, 13),
            open_=64.849998,
            high=66.949997,
            low=64.370003,
            close=64.52999877929688,
            volume=9_274_500,
        ),
    ]

    repaired, records = reconcile_official_bar_supplements(
        bars,
        calendar=[
            date(2025, 11, 11),
            date(2025, 11, 12),
            date(2025, 11, 13),
        ],
        snapshot=snapshot,
        cfg=CONFIG.validation.official_bar_supplement,
    )

    assert [bar.date for bar in repaired] == [
        date(2025, 11, 11),
        date(2025, 11, 12),
        date(2025, 11, 13),
    ]
    assert repaired[1].model_dump() == {
        "symbol": "FISV",
        "date": date(2025, 11, 12),
        "open": 64.2,
        "high": 64.87,
        "low": 63.11,
        "close": 64.38,
        "volume": 6_244_651.0,
        "adj_close": 64.38,
        "adj_factor": 1.0,
    }
    assert len(records) == 1
    assert records[0].verification_kind == "official_bar_supplement"
    assert records[0].snapshot_id == "2026-07-23_official_bar_supplements"


@pytest.mark.parametrize(
    ("neighbor_index", "field", "replacement"),
    [
        (0, "close", 64.25),
        (0, "adj_close", 64.25),
        (1, "close", 64.50),
        (1, "adj_close", 64.50),
    ],
)
def test_adjustment_evidence_must_match_actual_provider_neighbors(
    neighbor_index: int,
    field: str,
    replacement: float,
) -> None:
    snapshot = load_official_bar_supplement_snapshot(
        REPO / "configs" / "official_bar_supplements",
        as_of=date(2026, 7, 23),
        cfg=CONFIG.validation.official_bar_supplement,
    )
    bars = _provider_neighbors()
    bars[neighbor_index] = bars[neighbor_index].model_copy(
        update={field: replacement}
    )

    with pytest.raises(
        DataValidationError,
        match="adjustment evidence conflicts with actual provider bars",
    ):
        reconcile_official_bar_supplements(
            bars,
            calendar=[
                date(2025, 11, 11),
                date(2025, 11, 12),
                date(2025, 11, 13),
            ],
            snapshot=snapshot,
            cfg=CONFIG.validation.official_bar_supplement,
        )


def test_adjustment_evidence_requires_both_actual_provider_neighbors() -> None:
    snapshot = load_official_bar_supplement_snapshot(
        REPO / "configs" / "official_bar_supplements",
        as_of=date(2026, 7, 23),
        cfg=CONFIG.validation.official_bar_supplement,
    )
    bars = [
        _bar(
            date(2025, 11, 10),
            open_=63.0,
            high=64.0,
            low=62.0,
            close=63.5,
            volume=5_000_000,
        ),
        _provider_neighbors()[1],
    ]

    with pytest.raises(
        DataValidationError,
        match="adjustment evidence requires actual provider bars",
    ):
        reconcile_official_bar_supplements(
            bars,
            calendar=[
                date(2025, 11, 10),
                date(2025, 11, 12),
                date(2025, 11, 13),
            ],
            snapshot=snapshot,
            cfg=CONFIG.validation.official_bar_supplement,
        )


def test_official_bar_conflict_fails_closed() -> None:
    snapshot = load_official_bar_supplement_snapshot(
        REPO / "configs" / "official_bar_supplements",
        as_of=date(2026, 7, 23),
        cfg=CONFIG.validation.official_bar_supplement,
    )
    bars = [
        _bar(
            date(2025, 11, 11),
            open_=63.599998,
            high=64.480003,
            low=62.84,
            close=64.26000213623047,
            volume=5_427_200,
        ),
        _bar(
            date(2025, 11, 12),
            open_=64.2,
            high=64.87,
            low=63.11,
            close=64.39,
            volume=6_244_651,
        ),
        _bar(
            date(2025, 11, 13),
            open_=64.849998,
            high=66.949997,
            low=64.370003,
            close=64.52999877929688,
            volume=9_274_500,
        ),
    ]

    with pytest.raises(DataValidationError, match="conflicts with official supplement"):
        reconcile_official_bar_supplements(
            bars,
            calendar=[bar.date for bar in bars],
            snapshot=snapshot,
            cfg=CONFIG.validation.official_bar_supplement,
        )


def test_more_than_one_provider_hole_is_not_repaired() -> None:
    snapshot = load_official_bar_supplement_snapshot(
        REPO / "configs" / "official_bar_supplements",
        as_of=date(2026, 7, 23),
        cfg=CONFIG.validation.official_bar_supplement,
    )
    bars = [
        _bar(
            date(2025, 11, 11),
            open_=63.599998,
            high=64.480003,
            low=62.84,
            close=64.26000213623047,
            volume=5_427_200,
        ),
        _bar(
            date(2025, 11, 14),
            open_=64.0,
            high=65.0,
            low=63.0,
            close=64.5,
            volume=5_000_000,
        ),
    ]

    with pytest.raises(DataValidationError, match="2 provider sessions are missing"):
        reconcile_official_bar_supplements(
            bars,
            calendar=[
                date(2025, 11, 11),
                date(2025, 11, 12),
                date(2025, 11, 13),
                date(2025, 11, 14),
            ],
            snapshot=snapshot,
            cfg=CONFIG.validation.official_bar_supplement,
        )
