"""yfinance-backed DataProvider (ADR 2026-07-22 §1).

Import of :mod:`yfinance` is deferred to call time so the module can be
imported — and the rest of the package tested — without the ``market`` extra
installed. Business code must never import yfinance directly; it goes through
:class:`~smm.data.protocol.DataProvider`.

Measured provider semantics
---------------------------
Checked on 2026-07-22 against three known splits (NVDA 10:1 2024-06-10,
AAPL 4:1 2020-08-31, TSLA 3:1 2022-08-25), with ``auto_adjust=False``:

- ``Close`` is **already split-adjusted**. NVDA's pre-split close comes back as
  120.888, not the 1208.88 that actually traded.
- ``Volume`` is **also already split-adjusted** — pre-split volume is scaled
  into post-split share terms, so there is no step at the boundary.
- ``Adj Close`` differs from ``Close`` by **dividends only**. TSLA, which pays
  none, returns ``Adj Close == Close`` to the last decimal across its split.

Two consequences worth stating plainly:

1. The ADR calls the primary series "unadjusted". For this provider that is
   inaccurate: it is split-adjusted, dividend-unadjusted. Recovering the price
   that actually traded needs the ``Stock Splits`` action column. This matters
   for MVP-B fills and stops, not for M1 or M2, and is flagged for review.
2. The relative-volume contamination that ADR §3.4 was written to guard against
   **does not occur with this provider**, because volume is pre-adjusted. The
   split check in :mod:`smm.data.validation` is retained as a guard against a
   provider change, not as a fix for a live defect.

Further limitations (ADR §1.1): unofficial API with no SLA, occasional
rate-limiting and silent empty returns, and an adjusted series that is
backward-adjusted **as of download day** rather than point-in-time — the same
historical date can return a different ``adj_close`` on a different day. Do not
treat it as audit-grade truth for cross-split long-window backtests.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

from smm.config.schema import ValidationSection
from smm.core.errors import DataValidationError
from smm.data import cache
from smm.data.universe import load_universe
from smm.data.validation import to_session_date, validate_bars
from smm.domain.models import Bar


class YFinanceProvider:
    """Daily bars from Yahoo, cached to Parquet and validated before use.

    Bars are validated on the way *in*. A cache that could hold data which
    never passed §12.4 would quietly launder bad input into every later run.
    """

    def __init__(
        self,
        *,
        cache_dir: Path | str,
        universe_dir: Path | str,
        validation: ValidationSection,
        max_snapshot_age_days: int,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._universe_dir = Path(universe_dir)
        self._validation = validation
        self._max_snapshot_age_days = max_snapshot_age_days

    # -- DataProvider ----------------------------------------------------

    def get_universe(self, as_of: date) -> list[str]:
        snapshot = load_universe(
            self._universe_dir, as_of, max_age_days=self._max_snapshot_age_days
        )
        return list(snapshot.symbols)

    def get_daily_bars(self, symbol: str, start: date, end: date) -> Sequence[Bar]:
        cached = cache.read_bars(self._cache_dir, symbol, start, end)
        if self._covers(cached, start, end):
            return cached
        fetched = self.fetch(symbol, start, end)
        cache.write_bars(self._cache_dir, symbol, fetched)
        return cache.read_bars(self._cache_dir, symbol, start, end)

    def get_calendar(self, start: date, end: date) -> list[date]:
        """Sessions observed in the cached benchmark series.

        Deliberately derived from data already fetched rather than from an
        exchange-calendar dependency: M1 needs a calendar to validate against,
        not a holiday authority.
        """
        bars = cache.read_bars(self._cache_dir, "SPY", start, end)
        return [b.date for b in bars]

    # -- internals -------------------------------------------------------

    @staticmethod
    def _covers(bars: Sequence[Bar], start: date, end: date) -> bool:
        """Whether the cache plausibly spans the request.

        Weekday-based rather than calendar-based: without a holiday calendar the
        only honest statement is that the edges are close enough not to be a
        hole. Anything looser would let a partially-cached range pass as
        complete.
        """
        if not bars:
            return False
        return bars[0].date <= start + timedelta(days=4) and bars[-1].date >= end - timedelta(
            days=4
        )

    def fetch(self, symbol: str, start: date, end: date) -> list[Bar]:
        """Download, normalise and validate. Raises rather than returning partial data."""
        try:
            import yfinance  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise DataValidationError(
                "yfinance is not installed; install the market extra: pip install -e '.[market]'"
            ) from exc

        frame = yfinance.download(
            symbol,
            start=start.isoformat(),
            # yfinance treats `end` as exclusive.
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            actions=False,
            progress=False,
            threads=False,
        )
        if frame is None or frame.empty:
            raise DataValidationError(
                f"{symbol}: provider returned no rows for {start}..{end} "
                f"(rate-limited or unknown symbol — not treated as 'no sessions')"
            )
        if getattr(frame.columns, "nlevels", 1) > 1:
            frame.columns = frame.columns.droplevel(-1)

        bars = [self._row_to_bar(symbol, index, row) for index, row in frame.iterrows()]
        validate_bars(bars, cfg=self._validation)
        return bars

    @staticmethod
    def _session_date(index) -> date:
        """Resolve yfinance's index entry to a session date.

        Daily bars come back as a **naive midnight** ``Timestamp`` that already
        is the exchange session date — there is no time-of-day to interpret, so
        passing it through :func:`to_session_date` (which rejects naive input)
        would be wrong. Anything carrying a real time component does need
        timezone interpretation, so it is rejected rather than guessed at; that
        would mean intraday data arrived where daily was expected.
        """
        value = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return to_session_date(value)
            if (value.hour, value.minute, value.second, value.microsecond) != (0, 0, 0, 0):
                raise DataValidationError(
                    f"expected a daily bar, got a naive intraday timestamp: {value!r}"
                )
            return value.date()
        return to_session_date(value)

    @classmethod
    def _row_to_bar(cls, symbol: str, index, row) -> Bar:
        close = float(row["Close"])
        adj_close = float(row["Adj Close"])
        if close <= 0:
            raise DataValidationError(f"{symbol}: non-positive close at {index}")
        return Bar(
            symbol=symbol.upper(),
            date=cls._session_date(index),
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=close,
            volume=float(row["Volume"]),
            adj_close=adj_close,
            # Derived, never defaulted: a missing adj_close must fail rather
            # than silently fall back to close (ADR §3.3).
            adj_factor=adj_close / close,
        )
