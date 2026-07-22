"""M3 hard filters and breakout trigger at the public scanner seam."""

from __future__ import annotations

from dataclasses import replace

import pytest

from smm.config.loader import load_config
from smm.core.errors import FailClosedError
from smm.data.generator import breakout_success
from smm.features.engine import SymbolFeatures, compute_features
from smm.scanner.engine import evaluate_hard_filters, evaluate_trigger


@pytest.fixture(scope="module")
def loaded():
    return load_config()


@pytest.fixture(scope="module")
def breakout_case(loaded):
    path = breakout_success()
    bars = list(path.bars)
    assert path.breakout_index is not None
    as_of = bars[path.breakout_index].date
    features = compute_features(bars, as_of=as_of, cfg=loaded.config.features)
    assert isinstance(features, SymbolFeatures)
    return bars, as_of, features


def test_all_eight_hard_filters_pass_for_breakout_fixture(loaded, breakout_case) -> None:
    _, _, features = breakout_case

    result = evaluate_hard_filters(features, loaded.config)

    assert result.passed
    assert result.failed_rules == ()


@pytest.mark.parametrize(
    "rule",
    [
        "close_above_sma_50",
        "close_above_sma_200",
        "sma_50_above_sma_200",
        "return_63_positive",
        "return_126_positive",
        "within_15_percent_of_52w_high",
        "min_price",
        "min_avg_dollar_volume_20d",
    ],
)
def test_each_hard_filter_failure_records_its_exact_rule(rule, loaded, breakout_case) -> None:
    _, _, features = breakout_case
    updates = {
        "close_above_sma_50": {"close": features.sma_fast},
        "close_above_sma_200": {"close": features.sma_slow},
        "sma_50_above_sma_200": {"sma_fast": features.sma_slow},
        "return_63_positive": {"returns": {**features.returns, 63: 0.0}},
        "return_126_positive": {"returns": {**features.returns, 126: 0.0}},
        "within_15_percent_of_52w_high": {
            "distance_from_high": loaded.config.hard_filters.max_distance_from_52w_high
            + 0.001
        },
        "min_price": {"close": loaded.config.universe.min_price},
        "min_avg_dollar_volume_20d": {
            "avg_dollar_volume": loaded.config.universe.min_avg_dollar_volume_20d - 1.0
        },
    }
    failed = replace(features, **updates[rule])

    result = evaluate_hard_filters(failed, loaded.config)

    assert not result.passed
    assert rule in result.failed_rules
    assert f"hard_filter_failed:{rule}" in result.reason_codes


def test_trigger_uses_prior_highs_and_prior_volume_only(loaded, breakout_case) -> None:
    bars, as_of, features = breakout_case

    result = evaluate_trigger(
        bars,
        features=features,
        as_of=as_of,
        sessions=[bar.date for bar in bars],
        cfg=loaded.config.signal,
    )

    current_index = next(i for i, bar in enumerate(bars) if bar.date == as_of)
    prior = bars[current_index - loaded.config.signal.breakout_window : current_index]
    assert result.breakout_level == pytest.approx(max(bar.high for bar in prior))
    assert result.relative_volume == pytest.approx(
        bars[current_index].volume / (sum(bar.volume for bar in prior) / len(prior))
    )
    assert result.triggered


def test_current_volume_cannot_dilute_its_own_reference_window(loaded, breakout_case) -> None:
    bars, as_of, features = breakout_case
    current_index = next(i for i, bar in enumerate(bars) if bar.date == as_of)
    current = bars[current_index]
    quiet = current.model_copy(update={"volume": current.volume * 0.10})
    changed = [*bars[:current_index], quiet, *bars[current_index + 1 :]]

    result = evaluate_trigger(
        changed,
        features=features,
        as_of=as_of,
        sessions=[bar.date for bar in bars],
        cfg=loaded.config.signal,
    )

    prior = bars[current_index - loaded.config.signal.breakout_window : current_index]
    expected = quiet.volume / (sum(bar.volume for bar in prior) / len(prior))
    assert result.relative_volume == pytest.approx(expected)
    assert not result.triggered


def test_frozen_extension_guard_blocks_overextended_breakout(loaded, breakout_case) -> None:
    bars, as_of, features = breakout_case
    overextended = replace(
        features, extension_atr=loaded.config.signal.max_extension_atr + 0.001
    )

    result = evaluate_trigger(
        bars,
        features=overextended,
        as_of=as_of,
        sessions=[bar.date for bar in bars],
        cfg=loaded.config.signal,
    )

    assert not result.triggered
    assert "extension_above_max" in result.failed_conditions


def test_future_bars_do_not_change_trigger_result(loaded, breakout_case) -> None:
    bars, as_of, features = breakout_case
    through_as_of = [bar for bar in bars if bar.date <= as_of]

    sessions = [bar.date for bar in bars]
    full = evaluate_trigger(
        bars, features=features, as_of=as_of, sessions=sessions, cfg=loaded.config.signal
    )
    truncated = evaluate_trigger(
        through_as_of,
        features=features,
        as_of=as_of,
        sessions=sessions,
        cfg=loaded.config.signal,
    )

    assert full == truncated


def test_missing_reference_session_fails_closed(loaded, breakout_case) -> None:
    bars, as_of, features = breakout_case
    sessions = [bar.date for bar in bars]
    current_index = sessions.index(as_of)
    missing = sessions[current_index - 5]
    incomplete = [bar for bar in bars if bar.date != missing]

    with pytest.raises(FailClosedError, match="missing 1 trigger session"):
        evaluate_trigger(
            incomplete,
            features=features,
            as_of=as_of,
            sessions=sessions,
            cfg=loaded.config.signal,
        )
