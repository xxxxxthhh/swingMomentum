"""Stable setup_key and logical signal id."""

from __future__ import annotations

from datetime import date

from smm.domain.identity import make_logical_signal_id, make_setup_key


def test_setup_key_stable() -> None:
    a = make_setup_key(
        "nvda",
        breakout_window=20,
        breakout_level=200.123456,
        anchor_date=date(2024, 6, 1),
    )
    b = make_setup_key(
        "NVDA",
        breakout_window=20,
        breakout_level=200.123456,
        anchor_date=date(2024, 6, 1),
    )
    assert a == b
    assert "NVDA" in a
    assert "bw20" in a


def test_setup_key_level_rounding() -> None:
    d = date(2024, 1, 1)
    a = make_setup_key("X", breakout_window=20, breakout_level=10.00001, anchor_date=d)
    b = make_setup_key("X", breakout_window=20, breakout_level=10.00002, anchor_date=d)
    # both round to 10.0000 at 4 dp
    assert a == b


def test_logical_signal_id_stable() -> None:
    key = make_setup_key(
        "NVDA",
        breakout_window=20,
        breakout_level=100.0,
        anchor_date=date(2024, 1, 2),
    )
    id1 = make_logical_signal_id(symbol="nvda", setup_key=key, strategy_version="SMM-V1.0.0")
    id2 = make_logical_signal_id(symbol="NVDA", setup_key=key, strategy_version="SMM-V1.0.0")
    assert id1 == id2
    assert id1.startswith("SMM-V1.0.0:NVDA:")
