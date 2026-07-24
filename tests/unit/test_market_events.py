"""Versioned official market-event snapshots for volume-spike verification."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.data.market_events import load_market_event_snapshot

REPO = Path(__file__).resolve().parents[2]
CFG = load_config(REPO / "configs" / "smm_v1_0_0.yaml").config.validation


def test_latest_point_in_time_snapshot_is_selected() -> None:
    snapshot = load_market_event_snapshot(
        REPO / "configs" / "market_events",
        as_of=date(2026, 4, 8),
        cfg=CFG.volume_spike_verification,
    )

    assert snapshot.snapshot_id == "2026-04-06_sp500_constituent_changes"
    assert len(snapshot.sha256) == 64
    assert snapshot.events[0].symbol == "CASY"
    assert snapshot.events[0].source_published_date == date(2026, 4, 6)
    assert snapshot.events[0].effective_date == date(2026, 4, 9)


def test_canonical_snapshot_contains_sp500_and_nasdaq100_history() -> None:
    snapshot = load_market_event_snapshot(
        REPO / "configs" / "market_events",
        as_of=date(2026, 7, 23),
        cfg=CFG.volume_spike_verification,
    )

    events = {event.symbol: event for event in snapshot.events}
    assert snapshot.snapshot_id == "2026-07-23_index_constituent_changes"
    assert events["CRH"].source_published_date == date(2025, 12, 5)
    assert events["CRH"].effective_date == date(2025, 12, 22)
    assert events["CRH"].action == "addition"
    assert events["CASY"].effective_date == date(2026, 4, 9)
    assert events["EME"].source_published_date == date(2025, 9, 5)
    assert events["EME"].effective_date == date(2025, 9, 22)
    assert events["EME"].action == "addition"
    assert events["FER"].source_published_date == date(2025, 12, 12)
    assert events["FER"].effective_date == date(2025, 12, 22)
    assert events["FER"].index_name == "Nasdaq-100"
    assert events["FER"].action == "addition"


def test_future_snapshot_is_not_visible(tmp_path: Path) -> None:
    source = REPO / "configs" / "market_events" / (
        "2026-04-06_sp500_constituent_changes.csv"
    )
    (tmp_path / "2026-04-10_sp500_constituent_changes.csv").write_bytes(
        source.read_bytes()
    )

    with pytest.raises(DataValidationError, match="no market-event snapshot"):
        load_market_event_snapshot(
            tmp_path,
            as_of=date(2026, 4, 8),
            cfg=CFG.volume_spike_verification,
        )


def test_duplicate_business_event_fails_closed(tmp_path: Path) -> None:
    header = (
        "event_id,source_published_date,effective_date,index_name,action,"
        "symbol,source_url,source_title\n"
    )
    row = (
        "event-1,2026-04-06,2026-04-09,S&P 500,addition,CASY,"
        "https://press.spglobal.com/example,Official notice\n"
    )
    (tmp_path / "2026-04-06_sp500_constituent_changes.csv").write_text(
        header + row + row.replace("event-1", "event-2"),
        encoding="utf-8",
    )

    with pytest.raises(DataValidationError, match="duplicate market event"):
        load_market_event_snapshot(
            tmp_path,
            as_of=date(2026, 4, 8),
            cfg=CFG.volume_spike_verification,
        )


def test_index_event_source_must_match_its_official_index_host(tmp_path: Path) -> None:
    payload = (
        "event_id,source_published_date,effective_date,index_name,action,"
        "symbol,source_url,source_title\n"
        "ndx-event,2025-12-12,2025-12-22,Nasdaq-100,addition,FER,"
        "https://press.spglobal.com/wrong-source,Wrong official index host\n"
    )
    (tmp_path / "2026-07-23_index_constituent_changes.csv").write_text(
        payload,
        encoding="utf-8",
    )

    with pytest.raises(DataValidationError, match="source host is not allowed for Nasdaq-100"):
        load_market_event_snapshot(
            tmp_path,
            as_of=date(2026, 7, 23),
            cfg=CFG.volume_spike_verification,
        )
