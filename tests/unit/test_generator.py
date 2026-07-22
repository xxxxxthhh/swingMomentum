"""Deterministic synthetic paths (ADR 2026-07-22 §4)."""

from __future__ import annotations

from smm.data.generator import (
    SYNTHETIC_PATHS,
    breakout_success,
    false_breakout,
    risk_off_spy,
)

# Regenerating these means the generator changed. That is allowed, but it must
# be a deliberate, reviewed act — every downstream fixture expectation moves
# with it.
GOLDEN_DIGESTS = {
    "breakout_success": "5ca6ceec4fd4af1bcd62e48d6476c42f2510b0381f0f5b0499a2e78c778b428a",
    "false_breakout": "8a8ff125338cbb656ecbcfd666c009312a1e1025ce4aae55105f640b86f46764",
    "risk_off_spy": "1897475c1599e7e416a0c837b5d923be6787772cba838e90fb7d8d5919425fe9",
}


def sma(bars, n: int) -> float:
    return sum(b.close for b in bars[-n:]) / n


def test_paths_are_deterministic() -> None:
    for build in SYNTHETIC_PATHS.values():
        assert build().digest() == build().digest()


def test_golden_digests() -> None:
    for name, build in SYNTHETIC_PATHS.items():
        assert build().digest() == GOLDEN_DIGESTS[name], f"{name} generator output changed"


def test_paths_are_long_enough_for_hard_filters() -> None:
    """SMA200 / Return_126 / 52w-high need at least a year of bars."""
    for build in SYNTHETIC_PATHS.values():
        assert len(build().bars) >= 252


def test_dates_are_ordered_unique_weekdays() -> None:
    for build in SYNTHETIC_PATHS.values():
        dates = [b.date for b in build().bars]
        assert dates == sorted(dates)
        assert len(set(dates)) == len(dates)
        assert all(d.weekday() < 5 for d in dates)


def test_synthetic_bars_declare_no_corporate_action() -> None:
    """adj_factor 1.0 is a stated known value, not a defaulted missing one."""
    for build in SYNTHETIC_PATHS.values():
        for bar in build().bars:
            assert bar.adj_factor == 1.0
            assert bar.adj_close == bar.close


def _hard_filters_pass(history) -> bool:
    last = history[-1]
    hi52 = max(b.high for b in history[-252:])
    return all(
        (
            last.close > sma(history, 50),
            last.close > sma(history, 200),
            sma(history, 50) > sma(history, 200),
            last.close / history[-64].close - 1 > 0,
            last.close / history[-127].close - 1 > 0,
            (hi52 - last.close) / hi52 <= 0.15,
        )
    )


def _triggers_at(bars, t: int, window: int = 20, min_rel_vol: float = 1.30) -> bool:
    """Evaluate the trigger at index ``t`` using only ``bars[t - window : t + 1]``."""
    bar = bars[t]
    prior = bars[t - window : t]
    avg_volume = sum(b.volume for b in prior) / window
    return bar.close > max(b.high for b in prior) and bar.volume / avg_volume >= min_rel_vol


def _triggers(history, window: int = 20, min_rel_vol: float = 1.30) -> bool:
    return _triggers_at(history, len(history) - 1, window, min_rel_vol)


def test_breakout_success_triggers_and_follows_through() -> None:
    path = breakout_success()
    bars, i = list(path.bars), path.breakout_index
    assert i is not None
    history = bars[: i + 1]
    assert _hard_filters_pass(history)
    assert _triggers(history)
    level = max(b.high for b in bars[i - 20 : i])
    assert bars[i + 5].close > level


def test_breakout_fixtures_respect_the_frozen_extension_guard() -> None:
    """Both paths must be eligible on the trigger day; only their future differs."""
    from smm.config.loader import load_config
    from smm.features.engine import compute_features

    loaded = load_config()
    for build in (breakout_success, false_breakout):
        path = build()
        assert path.breakout_index is not None
        as_of = path.bars[path.breakout_index].date
        feature = compute_features(path.bars, as_of=as_of, cfg=loaded.config.features)
        assert feature.extension_atr is not None
        assert feature.extension_atr <= loaded.config.signal.max_extension_atr


def test_false_breakout_is_indistinguishable_on_the_trigger_day() -> None:
    """The whole point of this fixture: only later bars separate the two.

    If the failing case could be told apart using information available on the
    trigger day, the fixture would be quietly teaching the scanner to look
    ahead.
    """
    path = false_breakout()
    bars, i = list(path.bars), path.breakout_index
    assert i is not None
    history = bars[: i + 1]
    assert _hard_filters_pass(history)
    assert _triggers(history)
    level = max(b.high for b in bars[i - 20 : i])
    assert bars[i + 5].close < level


def test_breakout_level_excludes_the_trigger_bar() -> None:
    """Its own high must not be in the level it has to clear."""
    path = breakout_success()
    bars, i = list(path.bars), path.breakout_index
    assert i is not None
    trigger = bars[i]
    level = max(b.high for b in bars[i - 20 : i])
    assert trigger.close > level
    assert trigger.high > level  # would trivially satisfy a window that included itself


def test_trigger_at_index_ignores_future_bars() -> None:
    """Evaluating at ``i`` must not depend on whether bars after ``i`` exist.

    Truncating the path right after the trigger has to give the same answer as
    evaluating it inside the full path — a trailing window that peeked forward
    would diverge here.
    """
    path = breakout_success()
    bars, i = list(path.bars), path.breakout_index
    assert i is not None
    assert i < len(bars) - 1, "need trailing bars for this to prove anything"
    assert _triggers_at(bars, i) == _triggers_at(bars[: i + 1], i) is True


def test_risk_off_spy_ends_risk_off() -> None:
    bars = list(risk_off_spy().bars)
    last = bars[-1]
    assert last.close < sma(bars, 200)
    assert sma(bars, 50) < sma(bars, 200)
