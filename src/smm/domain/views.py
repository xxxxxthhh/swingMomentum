"""Price-series consumption boundary (ADR 2026-07-22 §3.3 and §5.3).

Constitution §12.1 requires two price series and forbids mixing them: returns,
moving averages, ATR and momentum use the adjusted series; simulated fills and
stops use true historical prints. Provider-native OHLC is not necessarily the
latter — Yahoo adjusts historical OHLC for splits — so the two sides originate
from different domain types.

Rather than rely on code review, the two consumers get **views with
non-overlapping attribute surfaces**:

- :class:`AdjustedBar` exposes ``adj_open``/``adj_high``/``adj_low``/
  ``adj_close`` and has no ``open``/``high``/``low``/``close`` at all.
- :class:`TradeableBar` exposes ``open``/``high``/``low``/``close`` and has no
  ``adj_*`` at all.

Reaching across the boundary is therefore an ``AttributeError``, not a
judgement call. Both are ``slots=True`` so no reference to either source model
survives on the view.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date

from smm.domain.models import Bar, PrintBar


@dataclass(frozen=True, slots=True)
class AdjustedBar:
    """Total-return view. The only legal input to feature computation."""

    symbol: str
    date: _date
    adj_open: float
    adj_high: float
    adj_low: float
    adj_close: float
    volume: float


@dataclass(frozen=True, slots=True)
class TradeableBar:
    """Tradeable view. The only legal input to fills, stops and gap checks."""

    symbol: str
    date: _date
    open: float
    high: float
    low: float
    close: float
    volume: float


def to_adjusted(bar: Bar) -> AdjustedBar:
    """Derive the adjusted view: ``adj_x = raw_x * adj_factor`` (ADR §3.2).

    The same daily ``adj_factor`` applies to all four prices, so ratios within
    a bar are preserved.
    """
    f = bar.adj_factor
    return AdjustedBar(
        symbol=bar.symbol,
        date=bar.date,
        adj_open=bar.open * f,
        adj_high=bar.high * f,
        adj_low=bar.low * f,
        adj_close=bar.adj_close,
        volume=bar.volume,
    )


def to_tradeable(bar: PrintBar) -> TradeableBar:
    """Project a verified historical print into the fill/stop surface."""
    if not isinstance(bar, PrintBar):
        raise TypeError("to_tradeable requires PrintBar, not a provider-native Bar")
    return TradeableBar(
        symbol=bar.symbol,
        date=bar.date,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
    )
