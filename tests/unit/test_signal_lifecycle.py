"""M3 logical identity, expiry, replay, and append-only persistence."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from smm.config.loader import load_config
from smm.core.errors import FailClosedError
from smm.data.generator import breakout_success
from smm.domain.enums import SignalState
from smm.domain.identity import make_logical_signal_id
from smm.features.engine import SymbolFeatures, compute_features
from smm.scanner.engine import scan_session
from smm.signals.lifecycle import SignalTransition, current_states
from smm.signals.store import append_transitions, read_transitions


def _weekdays(start: date, count: int) -> list[date]:
    sessions: list[date] = []
    cursor = start
    while len(sessions) < count:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return sessions


def _case():
    loaded = load_config()
    path = breakout_success()
    bars = list(path.bars)
    assert path.breakout_index is not None
    trigger_as_of = bars[path.breakout_index].date
    feature = compute_features(bars, as_of=trigger_as_of, cfg=loaded.config.features)
    assert isinstance(feature, SymbolFeatures)
    return loaded, bars, trigger_as_of, feature


def test_first_qualification_can_trigger_without_a_prior_watchlist_day() -> None:
    loaded, bars, as_of, feature = _case()

    result = scan_session(
        as_of=as_of,
        sessions=[bar.date for bar in bars],
        symbols=[feature.symbol],
        features={feature.symbol: feature},
        bars_by_symbol={feature.symbol: bars},
        loaded=loaded,
        prior_transitions=[],
    )

    assert len(result.transitions) == 1
    transition = result.transitions[0]
    assert transition.from_state is SignalState.DETECTED
    assert transition.to_state is SignalState.TRIGGERED
    assert transition.reason_codes == ("breakout_confirmed",)


def test_same_day_rerun_reproduces_event_and_revision_conflicts(tmp_path) -> None:
    loaded, bars, as_of, feature = _case()
    arguments = {
        "as_of": as_of,
        "sessions": [bar.date for bar in bars],
        "symbols": [feature.symbol],
        "features": {feature.symbol: feature},
        "loaded": loaded,
    }
    first = scan_session(
        **arguments,
        bars_by_symbol={feature.symbol: bars},
        prior_transitions=[],
    )
    rerun = scan_session(
        **arguments,
        bars_by_symbol={feature.symbol: bars},
        prior_transitions=first.transitions,
    )
    assert rerun == first

    append_transitions(tmp_path, first.transitions)
    append_transitions(tmp_path, rerun.transitions)

    index = next(i for i, bar in enumerate(bars) if bar.date == as_of)
    quiet = bars[index].model_copy(update={"volume": bars[index].volume * 0.10})
    revised_bars = [*bars[:index], quiet, *bars[index + 1 :]]
    revised = scan_session(
        **arguments,
        bars_by_symbol={feature.symbol: revised_bars},
        prior_transitions=first.transitions,
    )
    assert revised.transitions[0].to_state is SignalState.WATCHLISTED
    with pytest.raises(FailClosedError, match="conflicting transition"):
        append_transitions(tmp_path, revised.transitions)


def test_same_watchlist_setup_keeps_one_signal_id_across_days() -> None:
    loaded, bars, trigger_as_of, feature = _case()
    trigger_index = next(i for i, bar in enumerate(bars) if bar.date == trigger_as_of)
    start_index = trigger_index - 3
    sessions = [bar.date for bar in bars]
    transitions: list[SignalTransition] = []

    for index in range(start_index, trigger_index):
        as_of = bars[index].date
        daily_feature = compute_features(bars, as_of=as_of, cfg=loaded.config.features)
        assert isinstance(daily_feature, SymbolFeatures)
        result = scan_session(
            as_of=as_of,
            sessions=sessions,
            symbols=[daily_feature.symbol],
            features={daily_feature.symbol: daily_feature},
            bars_by_symbol={daily_feature.symbol: bars},
            loaded=loaded,
            prior_transitions=transitions,
        )
        transitions.extend(result.transitions)

    assert len(transitions) == 1
    assert transitions[0].to_state is SignalState.WATCHLISTED
    assert "breakout_not_confirmed" in transitions[0].reason_codes
    assert len({transition.signal_id for transition in transitions}) == 1
    assert current_states(transitions)[transitions[0].signal_id] is SignalState.WATCHLISTED


def test_non_member_feature_cannot_birth_a_signal() -> None:
    """FeatureRun also contains SPY/sector ETFs; only universe members may scan."""
    loaded, bars, as_of, feature = _case()

    result = scan_session(
        as_of=as_of,
        sessions=[bar.date for bar in bars],
        symbols=[],
        features={feature.symbol: feature},
        bars_by_symbol={feature.symbol: bars},
        loaded=loaded,
        prior_transitions=[],
    )

    assert result.transitions == ()


def test_active_symbol_removed_from_universe_expires() -> None:
    loaded, bars, trigger_as_of, _ = _case()
    entry_as_of = max(bar.date for bar in bars if bar.date < trigger_as_of)
    feature = compute_features(bars, as_of=entry_as_of, cfg=loaded.config.features)
    assert isinstance(feature, SymbolFeatures)
    initial = scan_session(
        as_of=entry_as_of,
        sessions=[bar.date for bar in bars],
        symbols=[feature.symbol],
        features={feature.symbol: feature},
        bars_by_symbol={feature.symbol: bars},
        loaded=loaded,
        prior_transitions=[],
    ).transitions

    result = scan_session(
        as_of=trigger_as_of,
        sessions=[bar.date for bar in bars],
        symbols=[],
        features={},
        bars_by_symbol={},
        loaded=loaded,
        prior_transitions=initial,
    )

    assert result.transitions[0].reason_codes == (
        "hard_filter_lost",
        "hard_filter_failed:universe_membership",
    )


def test_hard_filter_loss_expires_before_trigger_evaluation() -> None:
    loaded, bars, trigger_as_of, feature = _case()
    entry_as_of = max(bar.date for bar in bars if bar.date < trigger_as_of)
    entry_feature = compute_features(bars, as_of=entry_as_of, cfg=loaded.config.features)
    assert isinstance(entry_feature, SymbolFeatures)
    initial = scan_session(
        as_of=entry_as_of,
        sessions=[bar.date for bar in bars],
        symbols=[entry_feature.symbol],
        features={entry_feature.symbol: entry_feature},
        bars_by_symbol={feature.symbol: bars},
        loaded=loaded,
        prior_transitions=[],
    ).transitions
    assert initial[0].to_state is SignalState.WATCHLISTED
    failed = replace(feature, close=loaded.config.universe.min_price)

    result = scan_session(
        as_of=trigger_as_of,
        sessions=[bar.date for bar in bars],
        symbols=[failed.symbol],
        features={failed.symbol: failed},
        bars_by_symbol={failed.symbol: bars},
        loaded=loaded,
        prior_transitions=initial,
    )

    assert result.transitions[0].to_state is SignalState.EXPIRED
    assert result.transitions[0].reason_codes[0] == "hard_filter_lost"
    assert "hard_filter_failed:min_price" in result.transitions[0].reason_codes


def test_watchlist_expires_at_age_n_without_triggering() -> None:
    loaded, bars, _, feature = _case()
    sessions = _weekdays(date(2026, 3, 2), loaded.config.signal.watchlist_expire_bars + 1)
    entry = sessions[0]
    quiet_bars = [
        bar.model_copy(update={"date": entry + timedelta(days=i)})
        for i, bar in enumerate(bars)
    ]
    setup_key = f"{feature.symbol}|bw20|w{entry.isoformat()}"
    initial = SignalTransition(
        signal_id=make_logical_signal_id(
            symbol=feature.symbol,
            setup_key=setup_key,
            strategy_version=loaded.version,
        ),
        symbol=feature.symbol,
        setup_key=setup_key,
        watchlist_entry=entry,
        from_state=SignalState.DETECTED,
        to_state=SignalState.WATCHLISTED,
        as_of=entry,
        reason_codes=["hard_filters_passed"],
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )

    result = scan_session(
        as_of=sessions[-1],
        sessions=sessions,
        symbols=[feature.symbol],
        features={feature.symbol: replace(feature, as_of=sessions[-1])},
        bars_by_symbol={feature.symbol: quiet_bars},
        loaded=loaded,
        prior_transitions=[initial],
    )

    assert result.transitions[0].to_state is SignalState.EXPIRED
    assert result.transitions[0].reason_codes == ("watchlist_expired",)


def test_transition_store_is_idempotent_and_conflicts_fail_closed(tmp_path) -> None:
    loaded, _, as_of, feature = _case()
    setup_key = f"{feature.symbol}|bw20|w{as_of.isoformat()}"
    transition = SignalTransition(
        signal_id=make_logical_signal_id(
            symbol=feature.symbol,
            setup_key=setup_key,
            strategy_version=loaded.version,
        ),
        symbol=feature.symbol,
        setup_key=setup_key,
        watchlist_entry=as_of,
        from_state=SignalState.DETECTED,
        to_state=SignalState.WATCHLISTED,
        as_of=as_of,
        reason_codes=["hard_filters_passed"],
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )

    append_transitions(tmp_path, [transition])
    append_transitions(tmp_path, [transition])
    assert read_transitions(tmp_path) == [transition]

    conflict = transition.model_copy(
        update={"to_state": SignalState.TRIGGERED, "reason_codes": ("breakout_confirmed",)}
    )
    with pytest.raises(FailClosedError, match="conflicting transition"):
        append_transitions(tmp_path, [conflict])


def test_empty_first_run_is_a_valid_no_op(tmp_path) -> None:
    target = append_transitions(tmp_path, [])

    assert not target.exists()
    assert read_transitions(tmp_path) == []
