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
   inaccurate: it is split-adjusted, dividend-unadjusted. MVP-B therefore uses
   a separate ``PrintBar`` contract whose producer must rebuild true prints
   from the ``Stock Splits`` action history. This provider deliberately never
   produces ``PrintBar`` or ``TradeableBar`` objects and cannot feed fills or
   stops directly.
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
from decimal import Decimal, InvalidOperation
from pathlib import Path

from smm.config.schema import ValidationSection
from smm.core.errors import DataValidationError
from smm.data import cache
from smm.data.universe import load_universe
from smm.data.validation import to_session_date, validate_bars
from smm.domain.models import Bar
from smm.paper.prints import SplitAction, SplitActionHistory


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
        benchmark: str = "SPY",
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._universe_dir = Path(universe_dir)
        self._validation = validation
        self._max_snapshot_age_days = max_snapshot_age_days
        self._benchmark = benchmark.upper()

    # -- DataProvider ----------------------------------------------------

    def get_universe(self, as_of: date) -> list[str]:
        snapshot = load_universe(
            self._universe_dir, as_of, max_age_days=self._max_snapshot_age_days
        )
        return list(snapshot.symbols)

    def get_daily_bars(self, symbol: str, start: date, end: date) -> Sequence[Bar]:
        if cache.covers(self._cache_dir, symbol, start, end):
            return cache.read_bars(self._cache_dir, symbol, start, end)
        fetched = self.fetch(
            symbol, start, end, calendar=self._calendar_for(symbol, start, end)
        )
        cache.write_bars(self._cache_dir, symbol, fetched, requested=(start, end))
        return cache.read_bars(self._cache_dir, symbol, start, end)

    def get_calendar(self, start: date, end: date) -> list[date]:
        """Sessions observed in the cached benchmark series.

        Deliberately derived from data already fetched rather than from an
        exchange-calendar dependency: this needs a calendar to validate against,
        not a holiday authority.

        Returns an empty list when the benchmark is not cached. That is a
        *known-nothing* state, not "no sessions existed" — callers must treat it
        as such, which :func:`~smm.data.validation.check_session_dates` does by
        failing closed rather than silently passing every bar.
        """
        bars = cache.read_bars(self._cache_dir, self._benchmark, start, end)
        return [b.date for b in bars]

    # -- internals -------------------------------------------------------

    def _calendar_for(self, symbol: str, start: date, end: date) -> list[date] | None:
        """Sessions to validate ``symbol`` against.

        Three distinct outcomes, and conflating any two of them re-opens the
        hole this exists to close:

        - ``None`` — **only** when the subject is the benchmark itself. It
          defines the calendar and cannot be checked against itself, so the
          first fetch of a run has nothing to compare to. This is the single
          legitimate skip.
        - ``[]`` — the benchmark is not cached, or has no sessions in this
          window. Everything downstream treats an empty calendar as
          fail-closed. Returning ``None`` here instead would let any member
          validate with no calendar at all, get written to cache **with its
          coverage recorded**, and then be served from that cache forever
          without ever being checked.
        - a session list — the normal path.

        This is what makes "benchmark before members" a property of the provider
        rather than of whatever happens to call it first.
        """
        if symbol.upper() == self._benchmark:
            return None
        if not cache.cached_range(self._cache_dir, self._benchmark):
            return []
        # Deliberately not `or None`: an empty window must stay empty so the
        # fail-closed branch fires.
        return list(self.get_calendar(start, end))

    def fetch(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        calendar: list[date] | None = None,
    ) -> list[Bar]:
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
        validate_bars(bars, cfg=self._validation, calendar=calendar)
        return bars

    def fetch_split_action_history(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        observation_cutoff: date,
        expected_sessions: Sequence[date],
    ) -> SplitActionHistory:
        """Fetch and verify Yahoo's split history for an explicit session set.

        Yahoo's action response contains price rows as well as the ``Stock
        Splits`` column.  The caller must supply the complete, independently
        validated session set for this query (normally the matching primary
        bars or benchmark calendar).  Matching every returned provider session
        against that set distinguishes a covered *empty action history* from a
        partial action response.  The result remains provenance only: callers
        must still pass it through :func:`smm.paper.rebuild_print_bars` before
        any Paper pricing use.
        """
        if start > end:
            raise DataValidationError("split history request start must not be after end")
        if observation_cutoff < end:
            raise DataValidationError("split history observation cutoff must cover requested end")

        expected = self._expected_split_sessions(
            expected_sessions,
            start=start,
            observation_cutoff=observation_cutoff,
        )
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
            end=(observation_cutoff + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            actions=True,
            progress=False,
            threads=False,
        )
        return self._split_action_history_from_frame(
            symbol,
            frame,
            requested_start=start,
            requested_end=end,
            observation_cutoff=observation_cutoff,
            expected_sessions=expected,
        )

    @staticmethod
    def _expected_split_sessions(
        sessions: Sequence[date],
        *,
        start: date,
        observation_cutoff: date,
    ) -> frozenset[date]:
        """Validate the complete reference sessions required for a history read."""
        if not sessions:
            raise DataValidationError("split history requires non-empty expected sessions")

        expected: set[date] = set()
        for session in sessions:
            if isinstance(session, datetime) or not isinstance(session, date):
                raise DataValidationError("expected split-history sessions must be plain dates")
            if session < start or session > observation_cutoff:
                raise DataValidationError(
                    f"expected session {session} is outside split-history query "
                    f"{start}..{observation_cutoff}"
                )
            if session in expected:
                raise DataValidationError(f"duplicate expected split-history session {session}")
            expected.add(session)
        return frozenset(expected)

    @classmethod
    def _split_action_history_from_frame(
        cls,
        symbol: str,
        frame,
        *,
        requested_start: date,
        requested_end: date,
        observation_cutoff: date,
        expected_sessions: frozenset[date],
    ) -> SplitActionHistory:
        """Normalise one Yahoo ``actions=True`` response without trusting it."""
        if frame is None or frame.empty:
            raise DataValidationError(
                f"{symbol}: provider returned no action-history rows for "
                f"{requested_start}..{observation_cutoff}"
            )
        if getattr(frame.columns, "nlevels", 1) > 1:
            frame.columns = frame.columns.droplevel(-1)
        if "Stock Splits" not in frame.columns:
            raise DataValidationError(
                f"{symbol}: provider action response lacks Stock Splits column"
            )

        provider_sessions: set[date] = set()
        actions: list[SplitAction] = []
        for index, row in frame.iterrows():
            session = cls._session_date(index)
            if session < requested_start or session > observation_cutoff:
                raise DataValidationError(
                    f"{symbol}: provider action session {session} is outside requested coverage "
                    f"{requested_start}..{observation_cutoff}"
                )
            if session in provider_sessions:
                raise DataValidationError(f"{symbol}: duplicate provider session {session}")
            provider_sessions.add(session)

            try:
                split_ratio = Decimal(str(row["Stock Splits"]))
            except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
                raise DataValidationError(
                    f"{symbol}: invalid Stock Splits value at {session}"
                ) from exc
            if not split_ratio.is_finite():
                raise DataValidationError(f"{symbol}: non-finite Stock Splits value at {session}")
            if split_ratio < 0:
                raise DataValidationError(
                    f"{symbol}: Stock Splits value must be zero or a finite positive ratio "
                    f"at {session}"
                )
            if split_ratio == 0:
                continue
            actions.append(
                SplitAction(
                    action_id=f"yahoo:{symbol.upper()}:{session.isoformat()}:stock-split",
                    symbol=symbol.upper(),
                    action_date=session,
                    split_ratio=split_ratio,
                )
            )

        missing_sessions = expected_sessions - provider_sessions
        if missing_sessions:
            missing = ", ".join(day.isoformat() for day in sorted(missing_sessions))
            raise DataValidationError(
                f"{symbol}: action response missing expected sessions: {missing}"
            )
        unexpected_sessions = provider_sessions - expected_sessions
        if unexpected_sessions:
            unexpected = ", ".join(day.isoformat() for day in sorted(unexpected_sessions))
            raise DataValidationError(
                f"{symbol}: action response has unexpected provider sessions: {unexpected}"
            )

        return SplitActionHistory(
            symbol=symbol.upper(),
            requested_start=requested_start,
            requested_end=requested_end,
            coverage_start=requested_start,
            coverage_end=observation_cutoff,
            observation_cutoff=observation_cutoff,
            actions=tuple(actions),
        )

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
