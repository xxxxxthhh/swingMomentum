"""Point-in-time EDGAR price events and security-identity mappings."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.data.price_events import (
    load_price_event_snapshot,
    load_security_identity_snapshot,
    match_price_event,
)

REPO = Path(__file__).resolve().parents[2]
CFG = load_config(REPO / "configs" / "smm_v1_0_0.yaml").config.validation


def test_canonical_snapshots_match_echo_through_independent_identity() -> None:
    events = load_price_event_snapshot(
        REPO / "configs" / "price_events",
        as_of=date(2026, 7, 23),
        cfg=CFG.price_jump_verification,
    )
    identities = load_security_identity_snapshot(
        REPO / "configs" / "security_identities",
        as_of=date(2026, 7, 23),
        cfg=CFG.price_jump_verification,
    )

    event, mapping = match_price_event(
        events,
        identities,
        current_symbol="ECHO",
        jump_session=date(2025, 8, 26),
        calendar=[date(2025, 8, 25), date(2025, 8, 26), date(2025, 8, 27)],
    )

    assert events.snapshot_id == "2026-07-23_edgar_item_1_01"
    assert identities.snapshot_id == "2026-07-23_symbol_mappings"
    assert event.registrant_cik == "0001415404"
    assert event.historical_symbol == "SATS"
    assert event.item_number == "1.01"
    assert mapping is not None
    assert mapping.old_symbol == "SATS"
    assert mapping.new_symbol == "ECHO"
    assert mapping.cusip_continuity == "unchanged"


def test_missing_ticker_mapping_fails_closed(tmp_path: Path) -> None:
    events = load_price_event_snapshot(
        REPO / "configs" / "price_events",
        as_of=date(2026, 7, 23),
        cfg=CFG.price_jump_verification,
    )
    identities = load_security_identity_snapshot(
        REPO / "configs" / "security_identities",
        as_of=date(2026, 7, 23),
        cfg=CFG.price_jump_verification,
    )

    with pytest.raises(DataValidationError, match="no unique security identity"):
        match_price_event(
            events,
            identities,
            current_symbol="OTHER",
            jump_session=date(2025, 8, 26),
            calendar=[date(2025, 8, 25), date(2025, 8, 26)],
        )


def test_non_item_1_01_event_fails_closed(tmp_path: Path) -> None:
    header = (
        "event_id,registrant_cik,accession_number,form,item_number,"
        "acceptance_datetime,historical_symbol,security_class,source_url,source_title\n"
    )
    row = (
        "event-1,0001415404,0001415404-25-000035,8-K,8.01,"
        "2025-08-26T06:31:18-04:00,SATS,Class A common stock,"
        "https://www.sec.gov/example,Other event\n"
    )
    (tmp_path / "2026-07-23_edgar_item_1_01.csv").write_text(
        header + row,
        encoding="utf-8",
    )

    with pytest.raises(DataValidationError, match="outside the frozen EDGAR catalog"):
        load_price_event_snapshot(
            tmp_path,
            as_of=date(2026, 7, 23),
            cfg=CFG.price_jump_verification,
        )


def test_after_close_filing_cannot_validate_same_session(tmp_path: Path) -> None:
    header = (
        "event_id,registrant_cik,accession_number,form,item_number,"
        "acceptance_datetime,historical_symbol,security_class,source_url,source_title\n"
    )
    row = (
        "event-1,0001415404,0001415404-25-000035,8-K,1.01,"
        "2025-08-26T16:30:00-04:00,ECHO,Class A common stock,"
        "https://www.sec.gov/example,Material agreement\n"
    )
    (tmp_path / "2026-07-23_edgar_item_1_01.csv").write_text(
        header + row,
        encoding="utf-8",
    )
    events = load_price_event_snapshot(
        tmp_path,
        as_of=date(2026, 7, 23),
        cfg=CFG.price_jump_verification,
    )
    identities = load_security_identity_snapshot(
        REPO / "configs" / "security_identities",
        as_of=date(2026, 7, 23),
        cfg=CFG.price_jump_verification,
    )

    with pytest.raises(DataValidationError, match="no unique EDGAR"):
        match_price_event(
            events,
            identities,
            current_symbol="ECHO",
            jump_session=date(2025, 8, 26),
            calendar=[date(2025, 8, 25), date(2025, 8, 26), date(2025, 8, 27)],
        )
