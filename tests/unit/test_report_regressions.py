"""Proof of the two report defects M4 ADR §207-1 requires proving first.

No report module exists yet. These tests build the naive alternatives the
ADR's "备选（未采纳）" table rejected -- inline, minimally -- against the
real M3 scanner/store seam, and show each one silently drops a signal that
is still an open observation. That is the justification for §4's design
(current-state replay + a fourth `open_trigger` bucket), not a test of code
that exists.
"""

from __future__ import annotations

from smm.data.generator import breakout_success
from smm.domain.enums import SignalState
from smm.features.engine import SymbolFeatures, compute_features
from smm.scanner.engine import scan_session
from smm.signals.lifecycle import active_transitions_by_symbol
from smm.signals.store import append_transitions, read_transitions


def _case():
    from smm.config.loader import load_config

    loaded = load_config()
    path = breakout_success()
    bars = list(path.bars)
    assert path.breakout_index is not None
    trigger_as_of = bars[path.breakout_index].date
    feature = compute_features(bars, as_of=trigger_as_of, cfg=loaded.config.features)
    assert isinstance(feature, SymbolFeatures)
    return loaded, bars, trigger_as_of, feature


def _naive_event_only_rows(transitions_today) -> list:
    """The rejected alternative: '日报只列当日 transitions'."""
    return list(transitions_today)


def _naive_three_bucket_rows(transitions_today, active_by_symbol: dict) -> dict:
    """The rejected alternative: three buckets, no `open_trigger`."""
    new_trigger = [row for row in transitions_today if row.to_state is SignalState.TRIGGERED]
    watchlist = [
        symbol
        for symbol, row in active_by_symbol.items()
        if row.to_state is SignalState.WATCHLISTED
    ]
    terminal_states = {
        SignalState.EXPIRED,
        SignalState.CANCELLED,
        SignalState.EXITED,
        SignalState.STOPPED,
    }
    terminal_change = [row for row in transitions_today if row.to_state in terminal_states]
    return {"new_trigger": new_trigger, "watchlist": watchlist, "terminal_change": terminal_change}


def test_event_only_report_drops_a_silent_watchlist_continuation(tmp_path) -> None:
    """§4 needs current-state replay, not just today's transitions.

    Day 1 births a WATCHLISTED signal. Day 2 is a silent continuation (no
    transition row at all -- the M3 product this whole gap is about). An
    event-only report for day 2 sees nothing, even though the signal is a
    live, open observation the operator needs to see.
    """
    loaded, bars, as_of, feature = _case()
    as_of_index = next(i for i, bar in enumerate(bars) if bar.date == as_of)
    entry_as_of = bars[as_of_index - 1].date
    entry_feature = compute_features(bars, as_of=entry_as_of, cfg=loaded.config.features)
    assert isinstance(entry_feature, SymbolFeatures)

    initial = scan_session(
        as_of=entry_as_of,
        sessions=[bar.date for bar in bars],
        symbols=[feature.symbol],
        features={feature.symbol: entry_feature},
        bars_by_symbol={feature.symbol: bars},
        loaded=loaded,
        prior_transitions=[],
    )
    assert initial.transitions[0].to_state is SignalState.WATCHLISTED
    append_transitions(
        tmp_path,
        initial.transitions,
        as_of=entry_as_of,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )

    current = bars[as_of_index]
    quiet = current.model_copy(update={"volume": current.volume * 0.10})
    quiet_bars = [*bars[:as_of_index], quiet, *bars[as_of_index + 1 :]]
    silent = scan_session(
        as_of=as_of,
        sessions=[bar.date for bar in bars],
        symbols=[feature.symbol],
        features={feature.symbol: feature},
        bars_by_symbol={feature.symbol: quiet_bars},
        loaded=loaded,
        prior_transitions=initial.transitions,
    )
    assert silent.transitions == ()
    append_transitions(
        tmp_path,
        silent.transitions,
        as_of=as_of,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )

    # The defect: an event-only report for day 2 is empty.
    naive_rows = _naive_event_only_rows(silent.transitions)
    assert naive_rows == [], "naive event-only report unexpectedly saw a row"

    # The information the naive report dropped does exist and is current.
    all_transitions = read_transitions(tmp_path)
    active_by_symbol = active_transitions_by_symbol(all_transitions)
    assert active_by_symbol[feature.symbol].to_state is SignalState.WATCHLISTED


def test_three_bucket_report_drops_a_carried_triggered_signal(tmp_path) -> None:
    """§4 needs a fourth `open_trigger` bucket.

    Day 1 triggers. Day 2: the scanner does not re-touch an already-TRIGGERED
    signal (only WATCHLISTED signals get evaluated further), so there is no
    day-2 transition. A report with only new_trigger/watchlist/terminal_change
    has no bucket that fits a signal that is TRIGGERED but didn't trigger
    today -- it vanishes, even though it is a non-terminal entity nothing
    downstream has consumed yet.
    """
    loaded, bars, trigger_as_of, feature = _case()

    triggered = scan_session(
        as_of=trigger_as_of,
        sessions=[bar.date for bar in bars],
        symbols=[feature.symbol],
        features={feature.symbol: feature},
        bars_by_symbol={feature.symbol: bars},
        loaded=loaded,
        prior_transitions=[],
    )
    assert triggered.transitions[0].to_state is SignalState.TRIGGERED
    append_transitions(
        tmp_path,
        triggered.transitions,
        as_of=trigger_as_of,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )

    trigger_index = next(i for i, bar in enumerate(bars) if bar.date == trigger_as_of)
    next_day = bars[trigger_index + 1].date
    next_feature = compute_features(bars, as_of=next_day, cfg=loaded.config.features)
    assert isinstance(next_feature, SymbolFeatures)

    carried = scan_session(
        as_of=next_day,
        sessions=[bar.date for bar in bars],
        symbols=[feature.symbol],
        features={feature.symbol: next_feature},
        bars_by_symbol={feature.symbol: bars},
        loaded=loaded,
        prior_transitions=triggered.transitions,
    )
    assert carried.transitions == (), "fixture assumption broke: scanner re-touched TRIGGERED"
    append_transitions(
        tmp_path,
        carried.transitions,
        as_of=next_day,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )

    # The defect: none of the three buckets contain the carried signal.
    all_transitions = read_transitions(tmp_path)
    active_by_symbol = active_transitions_by_symbol(all_transitions)
    naive = _naive_three_bucket_rows(carried.transitions, active_by_symbol)
    assert naive["new_trigger"] == []
    assert naive["watchlist"] == []
    assert naive["terminal_change"] == []

    # The information the naive buckets dropped does exist and is current.
    assert active_by_symbol[feature.symbol].to_state is SignalState.TRIGGERED
