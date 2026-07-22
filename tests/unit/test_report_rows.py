"""Bucket assignment (M4 ADR §4/§5) against the real M3 scanner seam.

These are the positive counterparts to test_report_regressions.py: where
that file proves the naive alternatives drop a signal, this file proves the
real seam (build_report_rows) keeps it, in the right bucket, with the right
fields populated or blank.
"""

from __future__ import annotations

from datetime import date

from smm.config.loader import load_config
from smm.data.generator import breakout_success
from smm.domain.enums import MarketRegime, SignalState
from smm.features.cross_section import CrossSection, ScoredSymbol
from smm.features.engine import SymbolFeatures, compute_features
from smm.report.rows import (
    BUCKET_NEW_TRIGGER,
    BUCKET_OPEN_TRIGGER,
    BUCKET_TERMINAL_CHANGE,
    BUCKET_WATCHLIST,
    build_report_rows,
)
from smm.scanner.engine import scan_session


def _loaded():
    return load_config()


def _cross_section(as_of: date, scored: dict[str, ScoredSymbol]) -> CrossSection:
    return CrossSection(
        as_of=as_of,
        scored=scored,
        ranking_universe=tuple(scored),
        excluded_from_ranking={},
    )


def _scored(symbol: str, momentum: float | None, relative_strength: float | None) -> ScoredSymbol:
    return ScoredSymbol(
        symbol=symbol,
        sector=None,
        rs_spy_short=None,
        rs_spy_long=None,
        rs_sector=None,
        momentum_score=momentum,
        relative_strength_score=relative_strength,
    )


def test_silent_watchlist_continuation_appears_with_todays_reading() -> None:
    loaded = _loaded()
    path = breakout_success()
    bars = list(path.bars)
    assert path.breakout_index is not None
    as_of = bars[path.breakout_index].date
    as_of_index = path.breakout_index
    entry_as_of = bars[as_of_index - 1].date
    entry_feature = compute_features(bars, as_of=entry_as_of, cfg=loaded.config.features)
    feature = compute_features(bars, as_of=as_of, cfg=loaded.config.features)
    assert isinstance(entry_feature, SymbolFeatures)
    assert isinstance(feature, SymbolFeatures)
    symbol = feature.symbol

    initial = scan_session(
        as_of=entry_as_of,
        sessions=[bar.date for bar in bars],
        symbols=[symbol],
        features={symbol: entry_feature},
        bars_by_symbol={symbol: bars},
        loaded=loaded,
        prior_transitions=[],
    )
    current = bars[as_of_index]
    quiet = current.model_copy(update={"volume": current.volume * 0.10})
    quiet_bars = [*bars[:as_of_index], quiet, *bars[as_of_index + 1 :]]
    silent = scan_session(
        as_of=as_of,
        sessions=[bar.date for bar in bars],
        symbols=[symbol],
        features={symbol: feature},
        bars_by_symbol={symbol: quiet_bars},
        loaded=loaded,
        prior_transitions=initial.transitions,
    )
    assert silent.transitions == ()

    rows = build_report_rows(
        as_of=as_of,
        scan_result=silent,
        all_transitions=[*initial.transitions, *silent.transitions],
        features={symbol: feature},
        cross_section=_cross_section(as_of, {symbol: _scored(symbol, 80.0, 60.0)}),
        regime=MarketRegime.RISK_ON,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )

    matches = [row for row in rows if row.symbol == symbol]
    assert len(matches) == 1
    row = matches[0]
    assert row.bucket == BUCKET_WATCHLIST
    assert row.from_state is None
    assert row.to_state is None
    assert row.reason_codes == ()
    # Today's reading, not day 1's -- day 1 used the un-quieted bar.
    expected_observation = silent.observations[symbol]
    assert row.relative_volume == expected_observation.relative_volume
    assert row.breakout_level == expected_observation.breakout_level
    assert row.close == feature.close


def test_carried_triggered_signal_appears_as_open_trigger_with_todays_reading() -> None:
    loaded = _loaded()
    path = breakout_success()
    bars = list(path.bars)
    assert path.breakout_index is not None
    trigger_as_of = bars[path.breakout_index].date
    feature = compute_features(bars, as_of=trigger_as_of, cfg=loaded.config.features)
    assert isinstance(feature, SymbolFeatures)
    symbol = feature.symbol

    triggered = scan_session(
        as_of=trigger_as_of,
        sessions=[bar.date for bar in bars],
        symbols=[symbol],
        features={symbol: feature},
        bars_by_symbol={symbol: bars},
        loaded=loaded,
        prior_transitions=[],
    )
    assert triggered.transitions[0].to_state is SignalState.TRIGGERED

    trigger_index = next(i for i, bar in enumerate(bars) if bar.date == trigger_as_of)
    next_day = bars[trigger_index + 1].date
    next_feature = compute_features(bars, as_of=next_day, cfg=loaded.config.features)
    assert isinstance(next_feature, SymbolFeatures)

    carried = scan_session(
        as_of=next_day,
        sessions=[bar.date for bar in bars],
        symbols=[symbol],
        features={symbol: next_feature},
        bars_by_symbol={symbol: bars},
        loaded=loaded,
        prior_transitions=triggered.transitions,
    )
    assert carried.transitions == ()

    rows = build_report_rows(
        as_of=next_day,
        scan_result=carried,
        all_transitions=[*triggered.transitions, *carried.transitions],
        features={symbol: next_feature},
        cross_section=_cross_section(next_day, {symbol: _scored(symbol, 90.0, 55.0)}),
        regime=MarketRegime.RISK_ON,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )

    matches = [row for row in rows if row.symbol == symbol]
    assert len(matches) == 1
    row = matches[0]
    assert row.bucket == BUCKET_OPEN_TRIGGER
    assert row.from_state is None
    assert row.to_state is None
    expected_observation = carried.observations[symbol]
    assert row.breakout_level == expected_observation.breakout_level
    assert row.relative_volume == expected_observation.relative_volume
    assert row.close == next_feature.close


def test_new_trigger_is_exclusive_of_open_trigger_and_watchlist() -> None:
    loaded = _loaded()
    path = breakout_success()
    bars = list(path.bars)
    assert path.breakout_index is not None
    as_of = bars[path.breakout_index].date
    feature = compute_features(bars, as_of=as_of, cfg=loaded.config.features)
    assert isinstance(feature, SymbolFeatures)
    symbol = feature.symbol

    result = scan_session(
        as_of=as_of,
        sessions=[bar.date for bar in bars],
        symbols=[symbol],
        features={symbol: feature},
        bars_by_symbol={symbol: bars},
        loaded=loaded,
        prior_transitions=[],
    )
    assert result.transitions[0].to_state is SignalState.TRIGGERED

    rows = build_report_rows(
        as_of=as_of,
        scan_result=result,
        all_transitions=result.transitions,
        features={symbol: feature},
        cross_section=_cross_section(as_of, {symbol: _scored(symbol, 88.0, 77.0)}),
        regime=MarketRegime.RISK_ON,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )

    buckets = {row.bucket for row in rows if row.symbol == symbol}
    assert buckets == {BUCKET_NEW_TRIGGER}
    row = next(row for row in rows if row.symbol == symbol)
    assert row.from_state is SignalState.DETECTED
    assert row.to_state is SignalState.TRIGGERED
    assert row.reason_codes == ("breakout_confirmed",)


def test_hard_filter_expiry_lands_in_terminal_change() -> None:
    loaded = _loaded()
    path = breakout_success()
    bars = list(path.bars)
    assert path.breakout_index is not None
    as_of = bars[path.breakout_index].date
    entry_as_of = bars[path.breakout_index - 1].date
    entry_feature = compute_features(bars, as_of=entry_as_of, cfg=loaded.config.features)
    assert isinstance(entry_feature, SymbolFeatures)
    symbol = entry_feature.symbol

    initial = scan_session(
        as_of=entry_as_of,
        sessions=[bar.date for bar in bars],
        symbols=[symbol],
        features={symbol: entry_feature},
        bars_by_symbol={symbol: bars},
        loaded=loaded,
        prior_transitions=[],
    )
    assert initial.transitions[0].to_state is SignalState.WATCHLISTED

    # Symbol drops out of the universe entirely -> hard_filter_lost expiry.
    dropped = scan_session(
        as_of=as_of,
        sessions=[bar.date for bar in bars],
        symbols=[],
        features={},
        bars_by_symbol={},
        loaded=loaded,
        prior_transitions=initial.transitions,
    )
    assert dropped.transitions[0].to_state is SignalState.EXPIRED

    rows = build_report_rows(
        as_of=as_of,
        scan_result=dropped,
        all_transitions=[*initial.transitions, *dropped.transitions],
        features={},
        cross_section=_cross_section(as_of, {}),
        regime=MarketRegime.RISK_ON,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )

    matches = [row for row in rows if row.symbol == symbol]
    assert len(matches) == 1
    row = matches[0]
    assert row.bucket == BUCKET_TERMINAL_CHANGE
    assert row.from_state is SignalState.WATCHLISTED
    assert row.to_state is SignalState.EXPIRED
    assert "hard_filter_lost" in row.reason_codes
    # No feature/observation available for an expired, dropped symbol.
    assert row.close is None
    assert row.breakout_level is None


def test_zero_signals_returns_an_empty_list() -> None:
    loaded = _loaded()
    as_of = date(2024, 6, 10)
    from smm.scanner.engine import ScanResult

    rows = build_report_rows(
        as_of=as_of,
        scan_result=ScanResult(as_of=as_of, transitions=(), observations={}),
        all_transitions=[],
        features={},
        cross_section=_cross_section(as_of, {}),
        regime=MarketRegime.RISK_ON,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )

    assert rows == []


def test_sort_order_is_momentum_desc_then_rs_desc_then_symbol_asc_missing_last() -> None:
    loaded = _loaded()
    as_of = date(2024, 6, 10)
    from smm.domain.identity import make_logical_signal_id, make_setup_key
    from smm.scanner.engine import ScanResult
    from smm.signals.lifecycle import SignalTransition

    def watchlisted(symbol: str, watchlist_entry: date) -> SignalTransition:
        setup_key = make_setup_key(symbol, breakout_window=20, watchlist_entry=watchlist_entry)
        signal_id = make_logical_signal_id(
            symbol=symbol, setup_key=setup_key, strategy_version=loaded.version
        )
        return SignalTransition(
            signal_id=signal_id,
            symbol=symbol,
            setup_key=setup_key,
            watchlist_entry=watchlist_entry,
            from_state=SignalState.DETECTED,
            to_state=SignalState.WATCHLISTED,
            as_of=watchlist_entry,
            reason_codes=["hard_filters_passed"],
            strategy_version=loaded.version,
            config_hash=loaded.config_hash,
        )

    transitions = [watchlisted(symbol, as_of) for symbol in ("AAA", "BBB", "CCC", "DDD")]
    scored = {
        "AAA": _scored("AAA", 50.0, 50.0),
        "BBB": _scored("BBB", 90.0, 10.0),
        "CCC": _scored("CCC", 90.0, 20.0),
        "DDD": _scored("DDD", None, None),
    }

    rows = build_report_rows(
        as_of=as_of,
        scan_result=ScanResult(as_of=as_of, transitions=tuple(transitions), observations={}),
        all_transitions=transitions,
        features={},
        cross_section=_cross_section(as_of, scored),
        regime=MarketRegime.RISK_ON,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )

    assert [row.symbol for row in rows] == ["CCC", "BBB", "AAA", "DDD"]
