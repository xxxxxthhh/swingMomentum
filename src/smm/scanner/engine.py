"""M3 hard filters, breakout trigger, and signal-lifecycle orchestration.

The scanner consumes the adjusted feature/bar surface only. It deliberately
does not know about order plans or tradeable prices; those belong to MVP-B's
Risk Engine and paper broker, which prevents this module from becoming a route
around the future risk gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from smm.config.loader import LoadedConfig
from smm.config.schema import SignalSection, StrategyConfig
from smm.core.errors import ConfigError, DataValidationError
from smm.domain.enums import SignalState
from smm.domain.identity import make_logical_signal_id, make_setup_key
from smm.domain.models import Bar
from smm.domain.views import to_adjusted
from smm.features.engine import SymbolFeatures
from smm.signals.lifecycle import (
    SignalTransition,
    active_transitions_by_symbol,
)

_CONFIGURED_HARD_FILTERS = (
    "close_above_sma_50",
    "close_above_sma_200",
    "sma_50_above_sma_200",
    "return_63_positive",
    "return_126_positive",
    "within_15_percent_of_52w_high",
)
_M3_HARD_FILTERS = (*_CONFIGURED_HARD_FILTERS, "min_price", "min_avg_dollar_volume_20d")


@dataclass(frozen=True, slots=True)
class HardFilterResult:
    failed_rules: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failed_rules

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(f"hard_filter_failed:{rule}" for rule in self.failed_rules)


@dataclass(frozen=True, slots=True)
class TriggerResult:
    triggered: bool
    breakout_level: float
    relative_volume: float
    extension_atr: float | None
    failed_conditions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScanResult:
    as_of: date
    transitions: tuple[SignalTransition, ...]
    # Non-persisted, same-day trigger diagnostics per symbol (M4 ADR §5): the
    # single public source reporting reads for a signal's current
    # breakout_level/relative_volume/extension_atr, whether or not it
    # transitioned today. Covers active WATCHLISTED (silent stay included)
    # and active TRIGGERED (open_trigger) symbols with a feature available
    # for `as_of`. Absence means no same-day observation exists -- the
    # report must show that explicitly, not fill it in.
    observations: Mapping[str, TriggerResult]


def evaluate_hard_filters(feature: SymbolFeatures, config: StrategyConfig) -> HardFilterResult:
    """Evaluate the eight accepted M3 filters with deterministic failure order."""
    configured = tuple(config.hard_filters.rules)
    if set(configured) != set(_CONFIGURED_HARD_FILTERS) or len(configured) != len(
        _CONFIGURED_HARD_FILTERS
    ):
        raise ConfigError(
            "hard_filters.rules must contain exactly the six implemented M3 feature rules; "
            f"got {configured!r}"
        )

    values: dict[str, bool] = {
        "close_above_sma_50": (
            feature.sma_fast is not None and feature.close > feature.sma_fast
        ),
        "close_above_sma_200": (
            feature.sma_slow is not None and feature.close > feature.sma_slow
        ),
        "sma_50_above_sma_200": (
            feature.sma_fast is not None
            and feature.sma_slow is not None
            and feature.sma_fast > feature.sma_slow
        ),
        "return_63_positive": (
            feature.returns.get(63) is not None and feature.returns[63] > 0
        ),
        "return_126_positive": (
            feature.returns.get(126) is not None and feature.returns[126] > 0
        ),
        "within_15_percent_of_52w_high": (
            feature.distance_from_high is not None
            and feature.distance_from_high <= config.hard_filters.max_distance_from_52w_high
        ),
        # Constitution §10 says "above" $10, so equality is a failure.
        "min_price": feature.close > config.universe.min_price,
        "min_avg_dollar_volume_20d": (
            feature.avg_dollar_volume is not None
            and feature.avg_dollar_volume >= config.universe.min_avg_dollar_volume_20d
        ),
    }
    order = (*configured, *_M3_HARD_FILTERS[-2:])
    return HardFilterResult(tuple(rule for rule in order if not values[rule]))


def evaluate_trigger(
    bars: Sequence[Bar],
    *,
    features: SymbolFeatures,
    as_of: date,
    sessions: Sequence[date],
    cfg: SignalSection,
) -> TriggerResult:
    """Evaluate the accepted V1 trigger using only sessions through ``as_of``.

    Both reference windows are right-open: the current session cannot raise its
    own breakout level or dilute its own relative-volume denominator.
    """
    if features.as_of != as_of:
        raise DataValidationError(
            f"feature date {features.as_of} does not match scanner as_of {as_of}"
        )
    calendar = list(sessions)
    if calendar != sorted(set(calendar)):
        raise DataValidationError("trigger calendar must be sorted with unique sessions")
    try:
        as_of_index = calendar.index(as_of)
    except ValueError as exc:
        raise DataValidationError(f"trigger calendar does not contain as_of {as_of}") from exc

    history = sorted((bar for bar in bars if bar.date <= as_of), key=lambda bar: bar.date)
    if not history or history[-1].date != as_of:
        raise DataValidationError(f"{features.symbol}: no bar for scanner as_of {as_of}")
    if len({bar.date for bar in history}) != len(history):
        raise DataValidationError(f"{features.symbol}: duplicate sessions through {as_of}")
    if {bar.symbol for bar in history} != {features.symbol}:
        raise DataValidationError(f"{features.symbol}: mixed-symbol trigger history")
    needed = cfg.breakout_window + 1
    expected_sessions = calendar[max(0, as_of_index - cfg.breakout_window) : as_of_index + 1]
    if len(expected_sessions) < needed:
        raise DataValidationError(
            f"{features.symbol}: calendar provides only {len(expected_sessions)} of "
            f"{needed} trigger sessions through {as_of}"
        )
    by_date = {bar.date: bar for bar in history}
    missing = [session for session in expected_sessions if session not in by_date]
    if missing:
        raise DataValidationError(
            f"{features.symbol}: missing {len(missing)} trigger session(s) through {as_of}: "
            f"{missing[0]}"
        )

    adjusted = [to_adjusted(by_date[session]) for session in expected_sessions]
    current = adjusted[-1]
    if abs(features.close - current.adj_close) > 1e-9 * current.adj_close:
        raise DataValidationError(
            f"{features.symbol}: feature close does not match the adjusted as_of bar"
        )
    prior = adjusted[-needed:-1]
    breakout_level = max(bar.adj_high for bar in prior)
    average_volume = sum(bar.volume for bar in prior) / len(prior)
    if average_volume <= 0:
        raise DataValidationError(
            f"{features.symbol}: non-positive prior-volume reference through {as_of}"
        )
    relative_volume = current.volume / average_volume

    failed: list[str] = []
    if current.adj_close <= breakout_level:
        failed.append("breakout_not_confirmed")
    if relative_volume < cfg.relative_volume_min:
        failed.append("relative_volume_below_min")
    if cfg.extension_filter_enabled and (
        features.extension_atr is None or features.extension_atr > cfg.max_extension_atr
    ):
        failed.append("extension_above_max")

    return TriggerResult(
        triggered=not failed,
        breakout_level=breakout_level,
        relative_volume=relative_volume,
        extension_atr=features.extension_atr,
        failed_conditions=tuple(failed),
    )


def _session_age(sessions: Sequence[date], entry: date, as_of: date) -> int:
    ordered = list(sessions)
    if ordered != sorted(set(ordered)):
        raise DataValidationError("scanner calendar must be sorted with unique sessions")
    try:
        return ordered.index(as_of) - ordered.index(entry)
    except ValueError as exc:
        raise DataValidationError(
            f"scanner calendar does not contain watchlist entry {entry} and as_of {as_of}"
        ) from exc


def _transition(
    *,
    signal_id: str,
    symbol: str,
    setup_key: str,
    watchlist_entry: date,
    from_state: SignalState,
    to_state: SignalState,
    as_of: date,
    reasons: list[str],
    loaded: LoadedConfig,
    trigger: TriggerResult | None = None,
) -> SignalTransition:
    return SignalTransition(
        signal_id=signal_id,
        symbol=symbol,
        setup_key=setup_key,
        watchlist_entry=watchlist_entry,
        from_state=from_state,
        to_state=to_state,
        as_of=as_of,
        reason_codes=reasons,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
        breakout_level=trigger.breakout_level if trigger else None,
        relative_volume=trigger.relative_volume if trigger else None,
        extension_atr=trigger.extension_atr if trigger else None,
    )


def scan_session(
    *,
    as_of: date,
    sessions: Sequence[date],
    symbols: Sequence[str],
    features: Mapping[str, SymbolFeatures],
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    loaded: LoadedConfig,
    prior_transitions: Sequence[SignalTransition],
) -> ScanResult:
    """Advance every M3 signal by at most one transition for ``as_of``."""
    if as_of not in sessions:
        raise DataValidationError(f"scanner calendar does not contain as_of {as_of}")
    members = tuple(dict.fromkeys(symbol.upper() for symbol in symbols))
    for symbol, feature in features.items():
        if symbol != symbol.upper() or feature.symbol != symbol:
            raise DataValidationError(f"scanner feature map has inconsistent symbol key {symbol!r}")
        if feature.as_of != as_of:
            raise DataValidationError(
                f"{symbol}: feature date {feature.as_of} does not match scanner as_of {as_of}"
            )

    future = [row for row in prior_transitions if row.as_of > as_of]
    if future:
        raise DataValidationError(
            f"scanner replay contains {len(future)} transition(s) after as_of {as_of}"
        )
    # Re-runs must reproduce the decision made *at* as_of. Replaying a same-day
    # event first would hide data revisions for a DETECTED -> TRIGGERED row: the
    # signal would already look triggered and the scanner would emit nothing,
    # bypassing the store's conflict gate.
    history = [row for row in prior_transitions if row.as_of < as_of]
    active = active_transitions_by_symbol(history)
    emitted: list[SignalTransition] = []
    observations: dict[str, TriggerResult] = {}

    # Existing observations must be resolved before any new setup may be born.
    for symbol, latest in sorted(active.items()):
        if latest.strategy_version != loaded.version or latest.config_hash != loaded.config_hash:
            raise DataValidationError(
                f"{symbol}: active signal identity does not match the current config"
            )
        if latest.to_state is not SignalState.WATCHLISTED:
            # A carried TRIGGERED (open_trigger) signal isn't re-evaluated by
            # the scanner, but the report still needs its same-day reading --
            # not the stale attributes off the original trigger transition.
            if latest.to_state is SignalState.TRIGGERED:
                feature = features.get(symbol)
                if feature is not None:
                    observations[symbol] = evaluate_trigger(
                        bars_by_symbol.get(symbol, ()),
                        features=feature,
                        as_of=as_of,
                        sessions=sessions,
                        cfg=loaded.config.signal,
                    )
            continue

        if symbol not in members:
            emitted.append(
                _transition(
                    signal_id=latest.signal_id,
                    symbol=symbol,
                    setup_key=latest.setup_key,
                    watchlist_entry=latest.watchlist_entry,
                    from_state=SignalState.WATCHLISTED,
                    to_state=SignalState.EXPIRED,
                    as_of=as_of,
                    reasons=[
                        "hard_filter_lost",
                        "hard_filter_failed:universe_membership",
                    ],
                    loaded=loaded,
                )
            )
            continue

        feature = features.get(symbol)
        if feature is None:
            emitted.append(
                _transition(
                    signal_id=latest.signal_id,
                    symbol=symbol,
                    setup_key=latest.setup_key,
                    watchlist_entry=latest.watchlist_entry,
                    from_state=SignalState.WATCHLISTED,
                    to_state=SignalState.EXPIRED,
                    as_of=as_of,
                    reasons=["hard_filter_lost", "hard_filter_failed:data_complete"],
                    loaded=loaded,
                )
            )
            continue

        filters = evaluate_hard_filters(feature, loaded.config)
        if not filters.passed:
            emitted.append(
                _transition(
                    signal_id=latest.signal_id,
                    symbol=symbol,
                    setup_key=latest.setup_key,
                    watchlist_entry=latest.watchlist_entry,
                    from_state=SignalState.WATCHLISTED,
                    to_state=SignalState.EXPIRED,
                    as_of=as_of,
                    reasons=["hard_filter_lost", *filters.reason_codes],
                    loaded=loaded,
                )
            )
            continue

        age = _session_age(sessions, latest.watchlist_entry, as_of)
        if age < 0:
            raise DataValidationError(
                f"{symbol}: as_of {as_of} precedes watchlist entry {latest.watchlist_entry}"
            )
        if age >= loaded.config.signal.watchlist_expire_bars:
            emitted.append(
                _transition(
                    signal_id=latest.signal_id,
                    symbol=symbol,
                    setup_key=latest.setup_key,
                    watchlist_entry=latest.watchlist_entry,
                    from_state=SignalState.WATCHLISTED,
                    to_state=SignalState.EXPIRED,
                    as_of=as_of,
                    reasons=["watchlist_expired"],
                    loaded=loaded,
                )
            )
            continue

        trigger = evaluate_trigger(
            bars_by_symbol.get(symbol, ()),
            features=feature,
            as_of=as_of,
            sessions=sessions,
            cfg=loaded.config.signal,
        )
        # Captured whether or not it triggers -- the silent-stay case (M3's
        # own product) needs today's reading just as much as a trigger day.
        observations[symbol] = trigger
        if trigger.triggered:
            emitted.append(
                _transition(
                    signal_id=latest.signal_id,
                    symbol=symbol,
                    setup_key=latest.setup_key,
                    watchlist_entry=latest.watchlist_entry,
                    from_state=SignalState.WATCHLISTED,
                    to_state=SignalState.TRIGGERED,
                    as_of=as_of,
                    reasons=["breakout_confirmed"],
                    loaded=loaded,
                    trigger=trigger,
                )
            )

    # Only symbols with no non-terminal signal are eligible for a new setup.
    for symbol in members:
        if symbol in active:
            continue
        feature = features.get(symbol)
        # Missing/insufficient data is a hard-filter rejection for a new setup;
        # unlike an active signal, there is no lifecycle entity to expire.
        if feature is None:
            continue
        filters = evaluate_hard_filters(feature, loaded.config)
        if not filters.passed:
            continue
        trigger = evaluate_trigger(
            bars_by_symbol.get(symbol, ()),
            features=feature,
            as_of=as_of,
            sessions=sessions,
            cfg=loaded.config.signal,
        )
        observations[symbol] = trigger
        setup_key = make_setup_key(
            symbol,
            breakout_window=loaded.config.signal.breakout_window,
            watchlist_entry=as_of,
        )
        signal_id = make_logical_signal_id(
            symbol=symbol,
            setup_key=setup_key,
            strategy_version=loaded.version,
        )
        emitted.append(
            _transition(
                signal_id=signal_id,
                symbol=symbol,
                setup_key=setup_key,
                watchlist_entry=as_of,
                from_state=SignalState.DETECTED,
                to_state=(
                    SignalState.TRIGGERED if trigger.triggered else SignalState.WATCHLISTED
                ),
                as_of=as_of,
                reasons=(
                    ["breakout_confirmed"]
                    if trigger.triggered
                    else ["hard_filters_passed", *trigger.failed_conditions]
                ),
                loaded=loaded,
                trigger=trigger,
            )
        )

    return ScanResult(as_of=as_of, transitions=tuple(emitted), observations=observations)
