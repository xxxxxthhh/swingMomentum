"""Price-series consumption boundary (ADR 2026-07-22 §3.3).

These tests are the enforcement mechanism the reviewer asked for: the boundary
must fail as an AttributeError, not as a missed code review.
"""

from __future__ import annotations

from datetime import date

import pytest

from smm.domain.models import Bar, PrintBar
from smm.domain.views import AdjustedBar, TradeableBar, to_adjusted, to_tradeable


def make_bar(*, adj_factor: float = 0.98) -> Bar:
    close = 100.0
    return Bar(
        symbol="NVDA",
        date=date(2024, 6, 7),
        open=98.0,
        high=101.0,
        low=97.0,
        close=close,
        volume=1_000_000,
        adj_close=close * adj_factor,
        adj_factor=adj_factor,
    )


def make_print_bar() -> PrintBar:
    return PrintBar(
        symbol="NVDA",
        date=date(2024, 6, 7),
        open=980.0,
        high=1010.0,
        low=970.0,
        close=1000.0,
        volume=100_000,
    )


def test_bar_requires_adjusted_fields() -> None:
    """adj_close must not be defaultable — no silent substitution of close."""
    with pytest.raises(ValueError):
        Bar(
            symbol="X",
            date=date(2024, 1, 2),
            open=10,
            high=11,
            low=9,
            close=10,
            volume=1,
        )


def test_bar_rejects_inconsistent_adj_factor() -> None:
    with pytest.raises(ValueError, match="adj_factor inconsistent"):
        Bar(
            symbol="X",
            date=date(2024, 1, 2),
            open=10,
            high=11,
            low=9,
            close=10,
            volume=1,
            adj_close=9.0,
            adj_factor=0.5,  # 10 * 0.5 = 5 != 9
        )


def test_adjusted_view_derives_all_four_prices() -> None:
    bar = make_bar(adj_factor=0.98)
    view = to_adjusted(bar)
    assert isinstance(view, AdjustedBar)
    assert view.adj_open == pytest.approx(98.0 * 0.98)
    assert view.adj_high == pytest.approx(101.0 * 0.98)
    assert view.adj_low == pytest.approx(97.0 * 0.98)
    assert view.adj_close == pytest.approx(100.0 * 0.98)


def test_adjusted_view_preserves_intrabar_ratios() -> None:
    """One factor for all four prices, so bar geometry is unchanged."""
    bar = make_bar(adj_factor=0.7)
    view = to_adjusted(bar)
    raw_range = (bar.high - bar.low) / bar.close
    adj_range = (view.adj_high - view.adj_low) / view.adj_close
    assert raw_range == pytest.approx(adj_range)


def test_feature_side_cannot_reach_raw_prices() -> None:
    """The adjusted view must not expose the tradeable series at all."""
    view = to_adjusted(make_bar())
    for forbidden in ("open", "high", "low", "close"):
        assert not hasattr(view, forbidden)
    with pytest.raises(AttributeError):
        _ = view.close  # type: ignore[attr-defined]


def test_fill_side_cannot_reach_adjusted_prices() -> None:
    """The tradeable view must not expose the adjusted series at all."""
    view = to_tradeable(make_print_bar())
    assert isinstance(view, TradeableBar)
    for forbidden in ("adj_close", "adj_factor", "adj_open", "adj_high", "adj_low"):
        assert not hasattr(view, forbidden)
    with pytest.raises(AttributeError):
        _ = view.adj_close  # type: ignore[attr-defined]


def test_views_hold_no_reference_back_to_bar() -> None:
    """slots=True: no __dict__ smuggling the source Bar across the boundary."""
    adj = to_adjusted(make_bar())
    trade = to_tradeable(make_print_bar())
    assert not hasattr(adj, "__dict__")
    assert not hasattr(trade, "__dict__")


def test_tradeable_view_uses_true_print_prices() -> None:
    bar = make_print_bar()
    view = to_tradeable(bar)
    assert view.close == bar.close == 1000.0
    assert view.open == 980.0


def test_provider_native_bar_cannot_enter_fill_side() -> None:
    """Yahoo's split-adjusted primary series is not a historical print."""
    with pytest.raises(TypeError, match="PrintBar"):
        to_tradeable(make_bar())  # type: ignore[arg-type]
