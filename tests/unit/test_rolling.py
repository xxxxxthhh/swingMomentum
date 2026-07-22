"""Frozen numeric definitions (ADR 2026-07-22 R3).

Each test pins a definition against a hand-computed value. Without that the
definitions are not actually frozen — an implementation could drift and every
test would still pass.
"""

from __future__ import annotations

import pytest

from smm.features.rolling import (
    atr,
    ema,
    highest,
    max_drawdown,
    percentile_ranks,
    slope,
    sma,
    total_return,
    true_range,
    weighted_score,
)

# --- sma -------------------------------------------------------------------


def test_sma_hand_computed() -> None:
    assert sma([1, 2, 3, 4, 5], 3) == pytest.approx(4.0)  # (3+4+5)/3


def test_sma_needs_a_full_window() -> None:
    assert sma([1, 2], 3) is None


# --- ema -------------------------------------------------------------------


def test_ema_is_seeded_from_the_sma_not_the_first_value() -> None:
    """Hand-computed: seed = mean(1,2,3) = 2; alpha = 2/4 = 0.5.

    bar 4: 0.5*4 + 0.5*2   = 3.0
    bar 5: 0.5*5 + 0.5*3.0 = 4.0
    """
    assert ema([1, 2, 3, 4, 5], 3) == pytest.approx(4.0)


def test_ema_seed_choice_is_observable() -> None:
    """Seeding from the first close would give a different answer here.

    First-close seeding: 1 -> 0.5*2+0.5*1=1.5 -> 0.5*3+0.5*1.5=2.25 ->
    0.5*4+0.5*2.25=3.125 -> 0.5*5+0.5*3.125=4.0625, not 4.0.
    """
    assert ema([1, 2, 3, 4, 5], 3) != pytest.approx(4.0625)


def test_ema_needs_a_full_window() -> None:
    assert ema([1, 2], 3) is None


# --- atr -------------------------------------------------------------------


def test_true_range_takes_the_widest_of_the_three() -> None:
    assert true_range(10, 8, 9) == pytest.approx(2.0)  # high-low
    assert true_range(10, 8, 5) == pytest.approx(5.0)  # high-prev_close
    assert true_range(10, 8, 14) == pytest.approx(6.0)  # prev_close-low


def test_atr_uses_wilder_smoothing() -> None:
    """Hand-computed with window=2 on a constant 2.0 true range.

    Every TR is 2.0, so the Wilder recursion holds at 2.0 — and so would a
    simple mean. The next test separates them.
    """
    highs = [10, 12, 14, 16, 18]
    lows = [8, 10, 12, 14, 16]
    closes = [10, 12, 14, 16, 18]
    assert atr(highs, lows, closes, 2) == pytest.approx(2.0)


def test_atr_wilder_differs_from_a_simple_mean() -> None:
    """TRs are [2, 2, 10, 2]. window=2.

    Wilder: seed=(2+2)/2=2 -> (2*1+10)/2=6 -> (6*1+2)/2=4.0
    Simple mean of the last 2 TRs would be (10+2)/2 = 6.0.
    """
    highs = [10, 12, 14, 24, 26]
    lows = [8, 10, 12, 14, 24]
    closes = [10, 12, 14, 24, 26]
    assert atr(highs, lows, closes, 2) == pytest.approx(4.0)


def test_atr_needs_window_plus_one_bars() -> None:
    assert atr([1, 2], [1, 2], [1, 2], 2) is None


def test_atr_rejects_ragged_input() -> None:
    with pytest.raises(ValueError, match="same length"):
        atr([1, 2], [1], [1, 2], 1)


# --- returns and slope -----------------------------------------------------


def test_total_return_hand_computed() -> None:
    assert total_return([100, 110, 120], 2) == pytest.approx(0.2)


def test_total_return_needs_window_plus_one() -> None:
    assert total_return([100, 110], 2) is None


def test_slope_is_relative_not_absolute() -> None:
    """A $500 and a $20 stock rising 10% must score the same.

    An absolute slope would rank the expensive one far higher on price level
    alone, which is meaningless cross-sectionally.
    """
    cheap = slope([20.0, 21.0, 22.0], 2)
    pricey = slope([500.0, 525.0, 550.0], 2)
    assert cheap == pytest.approx(0.1)
    assert cheap == pytest.approx(pricey)


# --- position --------------------------------------------------------------


def test_highest_over_window() -> None:
    assert highest([1, 9, 3, 4], 3) == 9
    assert highest([1, 2], 3) is None


def test_max_drawdown_hand_computed() -> None:
    """Peak 100, trough 80 -> -20%."""
    assert max_drawdown([50, 100, 80, 90], 4) == pytest.approx(-0.2)


def test_max_drawdown_is_zero_when_monotonic() -> None:
    assert max_drawdown([1, 2, 3, 4], 4) == pytest.approx(0.0)


# --- percentile ------------------------------------------------------------


def test_percentile_hand_computed_with_a_tie() -> None:
    """[10, 20, 20, 30, 40] -> [10, 40, 40, 70, 90]."""
    ranks = percentile_ranks({"a": 10, "b": 20, "c": 20, "d": 30, "e": 40})
    assert ranks["a"] == pytest.approx(10.0)
    assert ranks["b"] == pytest.approx(40.0)
    assert ranks["c"] == pytest.approx(40.0)
    assert ranks["d"] == pytest.approx(70.0)
    assert ranks["e"] == pytest.approx(90.0)


def test_percentile_ties_score_identically() -> None:
    ranks = percentile_ranks({"a": 5, "b": 5, "c": 5})
    assert ranks["a"] == ranks["b"] == ranks["c"] == pytest.approx(50.0)


def test_percentile_stays_inside_zero_to_hundred() -> None:
    ranks = percentile_ranks({str(i): float(i) for i in range(20)})
    assert all(0.0 <= v <= 100.0 for v in ranks.values())


def test_percentile_of_a_lone_symbol_is_neutral() -> None:
    """No cross-section exists, so it is neither strong nor weak."""
    assert percentile_ranks({"a": 42.0}) == {"a": 50.0}


def test_percentile_of_an_empty_universe() -> None:
    assert percentile_ranks({}) == {}


def test_percentile_is_monotonic() -> None:
    ranks = percentile_ranks({"lo": 1.0, "mid": 2.0, "hi": 3.0})
    assert ranks["lo"] < ranks["mid"] < ranks["hi"]


# --- weighted score --------------------------------------------------------


def test_weighted_score_hand_computed() -> None:
    score = weighted_score({"a": 10.0, "b": 20.0}, {"a": 0.25, "b": 0.75})
    assert score == pytest.approx(17.5)


def test_missing_component_propagates_rather_than_renormalising() -> None:
    """Renormalising would put a differently-scoped score in the same table."""
    assert weighted_score({"a": 10.0, "b": None}, {"a": 0.5, "b": 0.5}) is None


def test_absent_component_is_also_missing() -> None:
    assert weighted_score({"a": 10.0}, {"a": 0.5, "b": 0.5}) is None
