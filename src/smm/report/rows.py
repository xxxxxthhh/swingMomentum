"""Report row assembly: the four mutually exclusive buckets (M4 ADR §4/§5).

One rule governs every field, for every bucket:

- Transition fields (from_state / to_state / reason_codes) come from
  *today's* transition only when the signal actually transitioned today.
  Otherwise they are blank -- never copied from an older transition.
- Measurement fields (close / breakout_level / relative_volume /
  extension_atr) always come from today's feature + same-day scanner
  observation, regardless of whether a transition happened today. Reporting
  never recomputes the trigger formula and never reuses a stale reading.

That single rule is what makes a silent watchlist row and a fresh
watchlist-today row both correct with no special-casing, and what makes an
open_trigger row read today's numbers instead of the day it first triggered.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from smm.domain.enums import MarketRegime, SignalState
from smm.features.cross_section import CrossSection
from smm.features.engine import SymbolFeatures
from smm.scanner.engine import ScanResult
from smm.signals.lifecycle import SignalTransition, active_transitions_by_symbol

BUCKET_NEW_TRIGGER = "new_trigger"
BUCKET_OPEN_TRIGGER = "open_trigger"
BUCKET_WATCHLIST = "watchlist"
BUCKET_TERMINAL_CHANGE = "terminal_change"
BUCKET_ORDER = (BUCKET_NEW_TRIGGER, BUCKET_OPEN_TRIGGER, BUCKET_WATCHLIST, BUCKET_TERMINAL_CHANGE)

_TERMINAL_STATES = frozenset(
    {SignalState.EXPIRED, SignalState.CANCELLED, SignalState.EXITED, SignalState.STOPPED}
)


@dataclass(frozen=True, slots=True)
class ReportRow:
    as_of: date
    bucket: str
    symbol: str
    signal_id: str
    state: SignalState
    watchlist_entry: date
    from_state: SignalState | None
    to_state: SignalState | None
    reason_codes: tuple[str, ...]
    close: float | None
    breakout_level: float | None
    relative_volume: float | None
    extension_atr: float | None
    momentum_score: float | None
    relative_strength_score: float | None
    regime: MarketRegime
    strategy_version: str
    config_hash: str


def build_report_rows(
    *,
    as_of: date,
    scan_result: ScanResult,
    all_transitions: Sequence[SignalTransition],
    features: Mapping[str, SymbolFeatures],
    cross_section: CrossSection,
    regime: MarketRegime,
    strategy_version: str,
    config_hash: str,
) -> list[ReportRow]:
    """Assemble every signal's report row for ``as_of``, sorted and deduped.

    ``all_transitions`` must include today's batch -- current-state replay
    (silent watchlist, carried TRIGGERED) needs it, not just today's delta.
    """
    active = active_transitions_by_symbol(all_transitions)
    new_trigger_symbols = {
        row.symbol for row in scan_result.transitions if row.to_state is SignalState.TRIGGERED
    }

    def measurements(symbol: str) -> tuple[float | None, float | None, float | None, float | None]:
        feature = features.get(symbol)
        close = feature.close if feature is not None else None
        observation = scan_result.observations.get(symbol)
        if observation is None:
            return close, None, None, None
        return (
            close,
            observation.breakout_level,
            observation.relative_volume,
            observation.extension_atr,
        )

    def scores(symbol: str) -> tuple[float | None, float | None]:
        scored = cross_section.scored.get(symbol)
        if scored is None:
            return None, None
        return scored.momentum_score, scored.relative_strength_score

    def make_row(
        *,
        bucket: str,
        symbol: str,
        signal_id: str,
        state: SignalState,
        watchlist_entry: date,
        transition: SignalTransition | None,
    ) -> ReportRow:
        close, breakout_level, relative_volume, extension_atr = measurements(symbol)
        momentum_score, relative_strength_score = scores(symbol)
        return ReportRow(
            as_of=as_of,
            bucket=bucket,
            symbol=symbol,
            signal_id=signal_id,
            state=state,
            watchlist_entry=watchlist_entry,
            from_state=transition.from_state if transition is not None else None,
            to_state=transition.to_state if transition is not None else None,
            reason_codes=transition.reason_codes if transition is not None else (),
            close=close,
            breakout_level=breakout_level,
            relative_volume=relative_volume,
            extension_atr=extension_atr,
            momentum_score=momentum_score,
            relative_strength_score=relative_strength_score,
            regime=regime,
            strategy_version=strategy_version,
            config_hash=config_hash,
        )

    rows: list[ReportRow] = []

    # new_trigger: today's transitions to TRIGGERED (DETECTED-> or WATCHLISTED->).
    for row in scan_result.transitions:
        if row.to_state is SignalState.TRIGGERED:
            rows.append(
                make_row(
                    bucket=BUCKET_NEW_TRIGGER,
                    symbol=row.symbol,
                    signal_id=row.signal_id,
                    state=row.to_state,
                    watchlist_entry=row.watchlist_entry,
                    transition=row,
                )
            )

    # open_trigger: active TRIGGERED, minus today's new_trigger -- mutually
    # exclusive by construction, never both for the same signal.
    for symbol, latest in active.items():
        if latest.to_state is SignalState.TRIGGERED and symbol not in new_trigger_symbols:
            rows.append(
                make_row(
                    bucket=BUCKET_OPEN_TRIGGER,
                    symbol=symbol,
                    signal_id=latest.signal_id,
                    state=latest.to_state,
                    watchlist_entry=latest.watchlist_entry,
                    transition=None,  # never today's transition -- excluded above
                )
            )

    # watchlist: current WATCHLISTED state, whether freshly reached today or a
    # silent continuation with no row at all today.
    for symbol, latest in active.items():
        if latest.to_state is SignalState.WATCHLISTED:
            rows.append(
                make_row(
                    bucket=BUCKET_WATCHLIST,
                    symbol=symbol,
                    signal_id=latest.signal_id,
                    state=latest.to_state,
                    watchlist_entry=latest.watchlist_entry,
                    transition=latest if latest.as_of == as_of else None,
                )
            )

    # terminal_change: today's transitions into a terminal state.
    for row in scan_result.transitions:
        if row.to_state in _TERMINAL_STATES:
            rows.append(
                make_row(
                    bucket=BUCKET_TERMINAL_CHANGE,
                    symbol=row.symbol,
                    signal_id=row.signal_id,
                    state=row.to_state,
                    watchlist_entry=row.watchlist_entry,
                    transition=row,
                )
            )

    return sort_report_rows(rows)


def sort_report_rows(rows: list[ReportRow]) -> list[ReportRow]:
    """§5: MomentumScore desc, RelativeStrengthScore desc, symbol asc, missing
    last. Symbol is the final key so equal/missing scores still total-order --
    never fall back to insertion order.
    """
    bucket_rank = {name: index for index, name in enumerate(BUCKET_ORDER)}

    def key(row: ReportRow) -> tuple:
        return (
            bucket_rank[row.bucket],
            row.momentum_score is None,
            -(row.momentum_score or 0.0),
            row.relative_strength_score is None,
            -(row.relative_strength_score or 0.0),
            row.symbol,
        )

    return sorted(rows, key=key)
