"""Fail-closed market-data validation (constitution §12.4).

Every check raises :class:`~smm.core.errors.DataValidationError`, a
``FailClosedError``. Nothing here repairs, interpolates or defaults a bad
series: constitution principle 11 puts data correctness above model
complexity, and a quietly patched bar produces a signal nobody can audit.

Coverage of the §12.4 list:

===========================  ==========================================
缺失日期                      :func:`check_session_continuity` (calendar-free),
                             :func:`check_session_completeness` (with calendar)
重复记录                      :func:`check_ordering_and_duplicates`
价格为零或负数                 enforced at ``Bar`` construction (``gt=0``)
单日异常跳变                   :func:`check_price_jumps`
成交量异常                     :func:`check_volume_anomalies`
复权因子异常                   :func:`check_adj_factor`
财报日期缺失                   out of scope until M3 (no earnings source)
时区错误                      :func:`to_session_date`, :func:`check_session_dates`
===========================  ==========================================

Zero/negative prices and ``close * adj_factor == adj_close`` are enforced by
the ``Bar`` model itself, so a malformed bar cannot exist to be validated.
Re-checking them here would be dead code; the mapping is recorded instead.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from smm.config.schema import ValidationSection
from smm.core.errors import DataValidationError
from smm.data.market_events import (
    MarketEventSnapshot,
    VolumeSpikeVerification,
    match_market_event,
)
from smm.domain.models import Bar

#: US equity session dates are Eastern. Deriving them from the runner's local
#: clock would make a UTC CI run and a local run disagree about which session a
#: bar belongs to.
EXCHANGE_TZ = ZoneInfo("America/New_York")

#: Split ratios worth recognising as an integer-ratio artefact.
_COMMON_SPLIT_RATIOS = (2, 3, 4, 5, 7, 10, 20)


def _fail(msg: str) -> None:
    raise DataValidationError(msg)


def to_session_date(value: datetime | date) -> date:
    """Normalise a provider timestamp to a US/Eastern session date.

    A naive ``datetime`` is rejected rather than assumed to be UTC or local:
    guessing is how a bar silently lands on the wrong session near midnight.
    A plain ``date`` passes through — it is already session-resolution.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            _fail(f"naive datetime is not a session date: {value!r}")
        return value.astimezone(EXCHANGE_TZ).date()
    if isinstance(value, date):
        return value
    _fail(f"cannot derive a session date from {type(value).__name__}")
    raise AssertionError("unreachable")


def check_session_dates(bars: Sequence[Bar], *, calendar: Iterable[date] | None = None) -> None:
    """Bar dates must be plausible sessions (§12.4 timezone errors)."""
    for bar in bars:
        if isinstance(bar.date, datetime):
            _fail(f"{bar.symbol}: bar carries a timestamp, not a session date: {bar.date!r}")
        if bar.date.weekday() >= 5:
            _fail(f"{bar.symbol}: {bar.date} is a weekend, not a session")
    if calendar is None:
        # No calendar available at all (synthetic data, first ingest). Skipping
        # is honest; there is nothing to check against.
        return
    sessions = set(calendar)
    if not sessions:
        # An empty calendar is "we know nothing", not "no sessions existed".
        # Checking against it would reject every bar with a misleading message,
        # and skipping it would silently drop a §12.4 check. Say what is wrong.
        _fail(
            "empty trading calendar: the benchmark has no cached sessions in "
            "this window — it was either never ingested, or ingested for a "
            "different range. Sessions cannot be verified either way."
        )
    for bar in bars:
        if bar.date not in sessions:
            _fail(f"{bar.symbol}: {bar.date} is outside the trading calendar")


def check_session_completeness(
    bars: Sequence[Bar], *, calendar: Iterable[date] | None = None
) -> None:
    """Every calendar session inside the symbol's own life must have a bar.

    Distinct from :func:`check_session_dates`, which asks whether each *bar*
    falls on a session. This asks the opposite and more dangerous question:
    whether a *session* is missing its bar. A hole does not raise anything on
    its own — it silently shortens a rolling window, so SMA200 and the 52-week
    high quietly compute over the wrong span. That is the failure mode issue #5
    was opened for, and cache coverage does not detect it: coverage records what
    was *requested*, not what arrived.

    The window is clamped to ``[first bar, last bar]``. Sessions before a
    symbol's first bar or after its last are not holes — the company had not
    listed yet, or no longer trades — and treating them as errors would reject
    every recent IPO in the universe.
    """
    if calendar is None or not bars:
        return
    sessions = sorted(calendar)
    if not sessions:
        _fail(
            "empty trading calendar: cannot verify session completeness — the "
            "benchmark has no cached sessions in this window"
        )
    first, last = bars[0].date, bars[-1].date
    present = {b.date for b in bars}
    missing = [s for s in sessions if first <= s <= last and s not in present]
    if missing:
        shown = ", ".join(d.isoformat() for d in missing[:5])
        more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
        _fail(
            f"{bars[0].symbol}: {len(missing)} trading session(s) missing between "
            f"{first} and {last}: {shown}{more} — a hole silently shortens every "
            f"rolling window that spans it"
        )


def check_ordering_and_duplicates(bars: Sequence[Bar]) -> None:
    """Strictly increasing dates, one symbol, no duplicate sessions (§12.4)."""
    if not bars:
        _fail("empty bar series")
    symbols = {b.symbol for b in bars}
    if len(symbols) != 1:
        _fail(f"expected a single symbol, got {sorted(symbols)}")
    for previous, current in zip(bars, bars[1:], strict=False):
        if current.date == previous.date:
            _fail(f"{current.symbol}: duplicate session {current.date}")
        if current.date < previous.date:
            _fail(f"{current.symbol}: out-of-order sessions {previous.date} → {current.date}")


def check_session_continuity(bars: Sequence[Bar], *, cfg: ValidationSection) -> None:
    """No unexplained holes in the series (§12.4 missing dates).

    Counted in weekdays, so ordinary weekends never trip it. A run of missing
    weekdays longer than the configured gap is a data hole or a halt — either
    way, not something to trade through.
    """
    for previous, current in zip(bars, bars[1:], strict=False):
        gap = _weekdays_between(previous.date, current.date)
        if gap > cfg.max_session_gap_weekdays:
            _fail(
                f"{current.symbol}: {gap} weekdays missing between "
                f"{previous.date} and {current.date}"
            )


def _weekdays_between(start: date, end: date) -> int:
    """Weekdays strictly between two dates."""
    from datetime import timedelta

    count = 0
    cursor = start + timedelta(days=1)
    while cursor < end:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def check_price_jumps(bars: Sequence[Bar], *, cfg: ValidationSection) -> None:
    """Reject implausible single-session moves (§12.4 abnormal jumps).

    A genuine 60% gap does happen; the point is that it must be looked at
    rather than silently scored, so the run stops.
    """
    for previous, current in zip(bars, bars[1:], strict=False):
        move = abs(current.close / previous.close - 1.0)
        if move > cfg.max_abs_daily_return:
            _fail(
                f"{current.symbol}: {current.date} moved {move:.1%} "
                f"(limit {cfg.max_abs_daily_return:.0%}) — verify corporate actions"
            )


def check_volume_anomalies(
    bars: Sequence[Bar],
    *,
    cfg: ValidationSection,
    calendar: Iterable[date] | None = None,
    event_snapshot: MarketEventSnapshot | None = None,
) -> tuple[VolumeSpikeVerification, ...]:
    """Reject bad volume, or verify an exact reviewed T-1/T index event."""
    volumes = sorted(b.volume for b in bars)
    median = volumes[len(volumes) // 2]
    records: list[VolumeSpikeVerification] = []
    sessions = list(calendar) if calendar is not None else None
    for bar in bars:
        if not math.isfinite(bar.volume):
            _fail(f"{bar.symbol}: {bar.date} has non-finite volume")
        if bar.volume <= 0:
            _fail(f"{bar.symbol}: {bar.date} has zero volume on a session")
        if median > 0 and bar.volume / median > cfg.max_volume_spike_ratio:
            if event_snapshot is None or sessions is None:
                _fail(
                    f"{bar.symbol}: {bar.date} volume {bar.volume:,.0f} is "
                    f"{bar.volume / median:.0f}x the median and has no official "
                    "event snapshot/calendar verification"
                )
            event = match_market_event(
                event_snapshot,
                symbol=bar.symbol,
                spike_session=bar.date,
                calendar=sessions,
            )
            records.append(
                VolumeSpikeVerification(
                    symbol=bar.symbol,
                    session=bar.date,
                    raw_volume=bar.volume,
                    median_volume=median,
                    ratio=bar.volume / median,
                    threshold=cfg.max_volume_spike_ratio,
                    event_id=event.event_id,
                    index_name=event.index_name,
                    action=event.action,
                    effective_date=event.effective_date,
                    source_published_date=event.source_published_date,
                    source_url=event.source_url,
                    snapshot_id=event_snapshot.snapshot_id,
                    snapshot_sha256=event_snapshot.sha256,
                )
            )
    return tuple(records)


def check_adj_factor(bars: Sequence[Bar], *, cfg: ValidationSection) -> None:
    """Adjustment-factor sanity (§12.4 adjustment anomalies).

    Backward dividend adjustment scales historical prices down, so the factor
    lies in ``(0, 1]`` and rises monotonically toward the present. A factor that
    falls as time moves forward means the series mixes two adjustment vintages.
    """
    for bar in bars:
        if not (cfg.min_adj_factor <= bar.adj_factor <= 1.0 + cfg.adj_factor_tolerance):
            _fail(
                f"{bar.symbol}: {bar.date} adj_factor {bar.adj_factor} outside "
                f"[{cfg.min_adj_factor}, 1.0]"
            )
    for previous, current in zip(bars, bars[1:], strict=False):
        if current.adj_factor < previous.adj_factor - cfg.adj_factor_tolerance:
            _fail(
                f"{current.symbol}: adj_factor decreased {previous.adj_factor} → "
                f"{current.adj_factor} at {current.date} — mixed adjustment vintages"
            )


def check_split_artefacts(bars: Sequence[Bar], *, cfg: ValidationSection) -> None:
    """Guard against an unadjusted split contaminating the series (ADR §3.4).

    A split that reached ``close`` or ``volume`` without a matching move in
    ``adj_factor`` leaves a near-integer-ratio step. In ``volume`` this is the
    dangerous one: it depresses the trailing average and manufactures a
    relative-volume breakout that never happened.

    Yahoo was measured to pre-adjust both series (see the yfinance provider
    docstring), so this should never fire for that provider — it is here to
    catch a provider change or a new provider, not to fix a known defect.

    Detection requires price and volume to step by the *same* ratio in
    *opposite* directions on the same session. Testing either series alone
    would be unusable: volume doubling day-over-day is ordinary market
    behaviour, and flagging it as a 2:1 split would halt the run constantly.
    An unadjusted split has the far more specific signature of ``close`` /= N
    together with ``volume`` *= N.
    """
    for previous, current in zip(bars, bars[1:], strict=False):
        if abs(current.adj_factor - previous.adj_factor) > cfg.adj_factor_tolerance:
            continue
        if previous.volume <= 0 or current.volume <= 0:
            continue
        price_drop = previous.close / current.close
        price_rise = current.close / previous.close
        volume_rise = current.volume / previous.volume
        volume_drop = previous.volume / current.volume
        for candidate in _COMMON_SPLIT_RATIOS:
            tolerance = cfg.split_ratio_tolerance * candidate
            forward = (
                abs(price_drop - candidate) <= tolerance
                and abs(volume_rise - candidate) <= tolerance
            )
            reverse = (
                abs(price_rise - candidate) <= tolerance
                and abs(volume_drop - candidate) <= tolerance
            )
            if forward or reverse:
                kind = "split" if forward else "reverse split"
                _fail(
                    f"{current.symbol}: {current.date} looks like an unadjusted "
                    f"{candidate}:1 {kind} — close {previous.close:,.2f} → "
                    f"{current.close:,.2f}, volume {previous.volume:,.0f} → "
                    f"{current.volume:,.0f}, with no adj_factor change"
                )


def validate_bars(
    bars: Sequence[Bar],
    *,
    cfg: ValidationSection,
    calendar: Iterable[date] | None = None,
    event_snapshot: MarketEventSnapshot | None = None,
) -> tuple[VolumeSpikeVerification, ...]:
    """Run every §12.4 check. Raises on the first failure; never repairs."""
    sessions = list(calendar) if calendar is not None else None
    check_ordering_and_duplicates(bars)
    check_session_dates(bars, calendar=sessions)
    check_session_completeness(bars, calendar=sessions)
    check_session_continuity(bars, cfg=cfg)
    check_price_jumps(bars, cfg=cfg)
    check_adj_factor(bars, cfg=cfg)
    check_split_artefacts(bars, cfg=cfg)
    verifications = check_volume_anomalies(
        bars,
        cfg=cfg,
        calendar=sessions,
        event_snapshot=event_snapshot,
    )
    return verifications
