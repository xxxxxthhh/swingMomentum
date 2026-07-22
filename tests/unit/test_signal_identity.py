"""Stable setup_key and logical signal id."""

from __future__ import annotations

from datetime import date

from smm.domain.identity import make_logical_signal_id, make_setup_key


def test_setup_key_stable() -> None:
    a = make_setup_key(
        "nvda",
        breakout_window=20,
        watchlist_entry=date(2024, 6, 1),
    )
    b = make_setup_key(
        "NVDA",
        breakout_window=20,
        watchlist_entry=date(2024, 6, 1),
    )
    assert a == b
    assert a == "NVDA|bw20|w2024-06-01"


def test_new_watchlist_entry_creates_a_new_setup() -> None:
    a = make_setup_key("X", breakout_window=20, watchlist_entry=date(2024, 1, 1))
    b = make_setup_key("X", breakout_window=20, watchlist_entry=date(2024, 1, 2))
    assert a != b


def test_logical_signal_id_stable() -> None:
    key = make_setup_key(
        "NVDA",
        breakout_window=20,
        watchlist_entry=date(2024, 1, 2),
    )
    id1 = make_logical_signal_id(symbol="nvda", setup_key=key, strategy_version="SMM-V1.0.0")
    id2 = make_logical_signal_id(symbol="NVDA", setup_key=key, strategy_version="SMM-V1.0.0")
    assert id1 == id2
    assert id1.startswith("SMM-V1.0.0:NVDA:")
