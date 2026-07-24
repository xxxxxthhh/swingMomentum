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

import json
import logging
import time
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from smm.config.schema import MarketDataRetrySection, ValidationSection
from smm.core.errors import DataValidationError
from smm.data import cache
from smm.data.market_events import (
    VolumeSpikeVerification,
    load_market_event_snapshot,
)
from smm.data.price_events import (
    PriceJumpVerification,
    load_price_event_snapshot,
    load_security_identity_snapshot,
)
from smm.data.universe import load_universe
from smm.data.validation import to_session_date, validate_bars
from smm.domain.models import Bar
from smm.paper.prints import SplitAction, SplitActionHistory

LOGGER = logging.getLogger(__name__)


class _RetryableProviderError(Exception):
    """One retryable provider attempt failure with a stable operator category."""

    def __init__(self, category: str, detail: str) -> None:
        super().__init__(detail)
        self.category = category
        self.detail = detail


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
        retry: MarketDataRetrySection,
        max_snapshot_age_days: int,
        market_events_dir: Path | str | None = None,
        price_events_dir: Path | str | None = None,
        security_identities_dir: Path | str | None = None,
        benchmark: str = "SPY",
        sleeper: Callable[[float], None] = time.sleep,
        attempt_logger: Callable[[str], None] | None = None,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._universe_dir = Path(universe_dir)
        self._validation = validation
        self._retry = retry
        self._max_snapshot_age_days = max_snapshot_age_days
        self._benchmark = benchmark.upper()
        self._market_events_dir = (
            Path(market_events_dir) if market_events_dir is not None else None
        )
        self._price_events_dir = (
            Path(price_events_dir) if price_events_dir is not None else None
        )
        self._security_identities_dir = (
            Path(security_identities_dir)
            if security_identities_dir is not None
            else None
        )
        self._sleeper = sleeper
        self._attempt_logger = attempt_logger
        self._volume_verifications: dict[
            tuple[str, date, str], VolumeSpikeVerification
        ] = {}
        self._price_verifications: dict[
            tuple[str, date, str], PriceJumpVerification
        ] = {}

    # -- DataProvider ----------------------------------------------------

    def get_universe(self, as_of: date) -> list[str]:
        snapshot = load_universe(
            self._universe_dir, as_of, max_age_days=self._max_snapshot_age_days
        )
        return list(snapshot.symbols)

    def get_daily_bars(self, symbol: str, start: date, end: date) -> Sequence[Bar]:
        if cache.covers(self._cache_dir, symbol, start, end):
            bars = cache.read_bars(self._cache_dir, symbol, start, end)
            self._validate_and_collect(
                bars,
                calendar=self._calendar_for(symbol, start, end),
                as_of=end,
            )
            return bars
        fetched = self.fetch(
            symbol, start, end, calendar=self._calendar_for(symbol, start, end)
        )
        cache.write_bars(self._cache_dir, symbol, fetched, requested=(start, end))
        return cache.read_bars(self._cache_dir, symbol, start, end)

    def market_data_verifications(
        self,
    ) -> tuple[PriceJumpVerification | VolumeSpikeVerification, ...]:
        """Deterministic evidence accumulated by all validated provider reads."""
        price = tuple(
            self._price_verifications[key]
            for key in sorted(self._price_verifications)
        )
        volume = tuple(
            self._volume_verifications[key]
            for key in sorted(self._volume_verifications)
        )
        return (*price, *volume)

    def reset_market_data_verifications(self) -> None:
        """Begin a new daily evidence scope without changing cached market data."""
        self._price_verifications.clear()
        self._volume_verifications.clear()

    def market_event_snapshot_identity(self) -> dict[str, str] | None:
        identities = {
            (record.snapshot_id, record.snapshot_sha256)
            for record in self._volume_verifications.values()
        }
        if not identities:
            return None
        if len(identities) != 1:
            raise DataValidationError(
                "one daily run collected volume verifications from multiple event snapshots"
            )
        snapshot_id, sha256 = next(iter(identities))
        return {"id": snapshot_id, "sha256": sha256}

    def market_data_snapshot_identities(self) -> dict[str, dict[str, str]]:
        """All committed snapshot identities consumed by this daily evidence."""
        output: dict[str, dict[str, str]] = {}
        price_events = {
            (record.price_event_snapshot_id, record.price_event_snapshot_sha256)
            for record in self._price_verifications.values()
        }
        security_identities = {
            (
                record.security_identity_snapshot_id,
                record.security_identity_snapshot_sha256,
            )
            for record in self._price_verifications.values()
        }
        volume_events = {
            (record.snapshot_id, record.snapshot_sha256)
            for record in self._volume_verifications.values()
        }
        for label, identities in (
            ("price_event", price_events),
            ("security_identity", security_identities),
            ("volume_event", volume_events),
        ):
            if len(identities) > 1:
                raise DataValidationError(
                    f"one daily run collected {label} evidence from multiple snapshots"
                )
            if identities:
                snapshot_id, sha256 = next(iter(identities))
                output[label] = {"id": snapshot_id, "sha256": sha256}
        return output

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
        """Download, normalise and validate with finite provider-boundary retries."""
        if calendar is not None and not calendar:
            raise DataValidationError(
                "empty trading calendar: the benchmark has no cached sessions in "
                "this window — provider retries cannot establish which member "
                "sessions are valid"
            )
        try:
            import yfinance  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise DataValidationError(
                "yfinance is not installed; install the market extra: pip install -e '.[market]'"
            ) from exc

        failures: list[str] = []
        for attempt in range(1, self._retry.max_attempts + 1):
            if attempt > 1:
                self._sleeper(self._retry.backoff_seconds[attempt - 2])
            try:
                bars = self._fetch_once(
                    yfinance,
                    symbol,
                    start,
                    end,
                    calendar=calendar,
                )
            except _RetryableProviderError as exc:
                failures.append(f"{attempt}/{exc.category}: {exc.detail}")
                self._log_attempt(
                    symbol=symbol,
                    start=start,
                    end=end,
                    attempt=attempt,
                    outcome="retryable_failure",
                    error_category=exc.category,
                )
                continue

            self._log_attempt(
                symbol=symbol,
                start=start,
                end=end,
                attempt=attempt,
                outcome="success",
                error_category=None,
            )
            return bars

        summary = " | ".join(failures)
        raise DataValidationError(
            f"{symbol.upper()}: provider attempts exhausted for {start}..{end}; "
            f"attempts: {summary}"
        )

    def _fetch_once(
        self,
        yfinance,
        symbol: str,
        start: date,
        end: date,
        *,
        calendar: list[date] | None,
    ) -> list[Bar]:
        try:
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
        except Exception as exc:
            raise _RetryableProviderError(
                "provider_transport", f"{type(exc).__name__}: {exc}"
            ) from exc

        if frame is None or frame.empty:
            raise _RetryableProviderError(
                "provider_empty",
                "provider returned no rows (rate-limited, truncated, or unknown symbol)",
            )
        try:
            if getattr(frame.columns, "nlevels", 1) > 1:
                frame.columns = frame.columns.droplevel(-1)
            bars = [self._row_to_bar(symbol, index, row) for index, row in frame.iterrows()]
        except (DataValidationError, KeyError, TypeError, ValueError, OverflowError) as exc:
            raise _RetryableProviderError(
                "provider_normalization", f"{type(exc).__name__}: {exc}"
            ) from exc
        try:
            self._validate_and_collect(bars, calendar=calendar, as_of=end)
        except DataValidationError as exc:
            raise _RetryableProviderError(
                "provider_validation", f"{type(exc).__name__}: {exc}"
            ) from exc
        return bars

    def _validate_and_collect(
        self,
        bars: Sequence[Bar],
        *,
        calendar: list[date] | None,
        as_of: date,
    ) -> None:
        volume_snapshot = None
        price_snapshot = None
        identity_snapshot = None
        has_jump = any(
            abs(current.close / previous.close - 1.0)
            > self._validation.max_abs_daily_return
            for previous, current in zip(bars, bars[1:], strict=False)
        )
        if has_jump:
            if self._price_events_dir is None or self._security_identities_dir is None:
                raise DataValidationError(
                    "price jump requires configured price-event and "
                    "security-identity snapshot directories"
                )
            price_snapshot = load_price_event_snapshot(
                self._price_events_dir,
                as_of=as_of,
                cfg=self._validation.price_jump_verification,
            )
            identity_snapshot = load_security_identity_snapshot(
                self._security_identities_dir,
                as_of=as_of,
                cfg=self._validation.price_jump_verification,
            )
        volumes = sorted(bar.volume for bar in bars)
        if volumes:
            median = volumes[len(volumes) // 2]
            has_spike = median > 0 and any(
                bar.volume / median > self._validation.max_volume_spike_ratio
                for bar in bars
            )
            if has_spike:
                if self._market_events_dir is None:
                    raise DataValidationError(
                        "volume spike requires a configured market-event snapshot directory"
                    )
                volume_snapshot = load_market_event_snapshot(
                    self._market_events_dir,
                    as_of=as_of,
                    cfg=self._validation.volume_spike_verification,
                )
        records = validate_bars(
            bars,
            cfg=self._validation,
            calendar=calendar,
            event_snapshot=volume_snapshot,
            price_event_snapshot=price_snapshot,
            identity_snapshot=identity_snapshot,
        )
        for record in records:
            key = (record.symbol, record.session, record.event_id)
            target = (
                self._price_verifications
                if isinstance(record, PriceJumpVerification)
                else self._volume_verifications
            )
            prior = target.get(key)
            if prior is not None and prior != record:
                raise DataValidationError(
                    f"{record.symbol}: conflicting market-data verification evidence "
                    f"for {record.session}"
                )
            target[key] = record

    def _log_attempt(
        self,
        *,
        symbol: str,
        start: date,
        end: date,
        attempt: int,
        outcome: str,
        error_category: str | None,
    ) -> None:
        payload = {
            "attempt": attempt,
            "end": end.isoformat(),
            "error_category": error_category,
            "max_attempts": self._retry.max_attempts,
            "outcome": outcome,
            "provider": "yfinance",
            "start": start.isoformat(),
            "symbol": symbol.upper(),
        }
        message = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if self._attempt_logger is not None:
            self._attempt_logger(message)
        else:
            LOGGER.info("%s", message)

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
        session = cls._session_date(index)
        try:
            return Bar(
                symbol=symbol.upper(),
                date=session,
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
        except PydanticValidationError as exc:
            # Preserve fail-closed rejection while adding the symbol/session
            # context required for the CLI's operator-facing error boundary.
            raise DataValidationError(f"{symbol}: {session} invalid bar: {exc}") from exc
