"""M4 `run-daily` orchestration: commit ordering, idempotency, continuity."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from smm.cli.daily import (
    DailyRunResult,
    artifact_root,
    git_commit_sha,
    run_daily,
    sanitize_path_segment,
)
from smm.cli.main import _CacheOnlyProvider
from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.data import cache as bar_cache
from smm.data.generator import synthetic_universe, universe_rows
from smm.domain.enums import SignalState
from smm.report.rows import BUCKET_WATCHLIST
from smm.signals.lifecycle import active_transitions_by_symbol
from smm.signals.store import read_transitions


def _synthetic_setup(cache_dir: Path):
    loaded = load_config()
    paths = synthetic_universe()
    for symbol, path in paths.items():
        bars = list(path.bars)
        bar_cache.write_bars(cache_dir, symbol, bars, requested=(bars[0].date, bars[-1].date))
    rows = universe_rows(paths[next(iter(paths))].bars[-1].date)
    sectors = {row["symbol"]: row["sector"] for row in rows}
    symbols = sorted(sectors)
    provider = _CacheOnlyProvider(cache_dir, loaded.config.market_regime.benchmark)
    session = paths[next(iter(paths))].bars[-1].date
    return loaded, provider, symbols, sectors, session


def test_run_daily_writes_a_complete_bundle_and_manifest(tmp_path: Path) -> None:
    loaded, provider, symbols, sectors, session = _synthetic_setup(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )

    result = run_daily(
        provider,
        session=session,
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root,
        provider_source="synthetic",
    )

    assert isinstance(result, DailyRunResult)
    assert not result.skipped_as_noop
    day_dir = root / session.isoformat()
    assert (day_dir / "report.csv").exists()
    assert (day_dir / "report.md").exists()
    assert (day_dir / "manifest.json").exists()
    assert sum(result.bucket_counts.values()) == result.row_count

    manifest = json.loads((day_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["execution_mode"] == "mvp_a_signal"
    assert manifest["as_of"] == session.isoformat()
    assert set(manifest["artifacts"]) == {"report_csv", "report_markdown", "features_snapshot"}


def test_run_daily_exact_rerun_is_a_noop(tmp_path: Path) -> None:
    loaded, provider, symbols, sectors, session = _synthetic_setup(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    kwargs = dict(
        session=session,
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root,
        provider_source="synthetic",
    )

    first = run_daily(provider, **kwargs)
    manifest_bytes_before = (root / session.isoformat() / "manifest.json").read_bytes()
    second = run_daily(provider, **kwargs)

    assert not first.skipped_as_noop
    assert second.skipped_as_noop
    manifest_bytes_after = (root / session.isoformat() / "manifest.json").read_bytes()
    assert manifest_bytes_before == manifest_bytes_after


def test_run_daily_conflicting_rerun_fails_closed(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    loaded, provider, symbols, sectors, session = _synthetic_setup(cache_dir)
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    run_daily(
        provider,
        session=session,
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root,
        provider_source="synthetic",
    )

    # Perturb a member's cached series so the same as_of now scans differently.
    changed_symbol = symbols[0]
    original = bar_cache.read_bars(cache_dir, changed_symbol, date(2000, 1, 1), date(2100, 1, 1))
    perturbed = [
        bar.model_copy(update={"volume": bar.volume * 5})
        if bar.date == session
        else bar
        for bar in original
    ]
    bar_cache.write_bars(
        cache_dir,
        changed_symbol,
        perturbed,
        requested=(perturbed[0].date, perturbed[-1].date),
    )

    with pytest.raises(DataValidationError):
        run_daily(
            provider,
            session=session,
            symbols=symbols,
            sectors=sectors,
            loaded=loaded,
            root=root,
            provider_source="synthetic",
        )


def test_run_daily_skipping_a_session_fails_closed(tmp_path: Path) -> None:
    loaded, provider, symbols, sectors, session = _synthetic_setup(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    run_daily(
        provider,
        session=session,
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root,
        provider_source="synthetic",
    )

    far_future = session + timedelta(days=60)
    with pytest.raises(DataValidationError, match="skips|not a provider session"):
        run_daily(
            provider,
            session=far_future,
            symbols=symbols,
            sectors=sectors,
            loaded=loaded,
            root=root,
            provider_source="synthetic",
        )


def test_zero_symbols_still_produces_a_complete_manifest_and_header_only_csv(
    tmp_path: Path,
) -> None:
    loaded, provider, _symbols, _sectors, session = _synthetic_setup(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )

    result = run_daily(
        provider,
        session=session,
        symbols=[],
        sectors={},
        loaded=loaded,
        root=root,
        provider_source="synthetic",
    )

    assert result.row_count == 0
    assert all(count == 0 for count in result.bucket_counts.values())
    day_dir = root / session.isoformat()
    csv_lines = (day_dir / "report.csv").read_text(encoding="utf-8").strip("\n").split("\n")
    assert len(csv_lines) == 1  # header only
    assert (day_dir / "manifest.json").exists()


def test_forward_session_advance_carries_a_silent_watchlist_signal(tmp_path: Path) -> None:
    """The motion the N-day replay gate stands on: seal day D, then run
    D+1 successfully, with a D-born WATCHLISTED signal that doesn't
    transition on D+1 replayed forward as a silent continuation -- not
    lost, per M4 ADR §4/§5.
    """
    loaded, provider, symbols, sectors, _last_session = _synthetic_setup(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    # Known from this deterministic fixture: day one births several
    # WATCHLISTED signals; day two has zero transitions for all of them.
    day_one = date(2024, 2, 5)
    day_two = date(2024, 2, 6)

    first = run_daily(
        provider,
        session=day_one,
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root,
        provider_source="synthetic",
    )
    second = run_daily(
        provider,
        session=day_two,
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root,
        provider_source="synthetic",
    )

    assert not first.skipped_as_noop
    assert not second.skipped_as_noop

    all_transitions = read_transitions(root)
    active_after_day_one = active_transitions_by_symbol(
        [row for row in all_transitions if row.as_of <= day_one]
    )
    watchlisted_symbols = {
        symbol
        for symbol, row in active_after_day_one.items()
        if row.to_state is SignalState.WATCHLISTED
    }
    assert watchlisted_symbols, "fixture assumption broke: day one birthed no WATCHLISTED signal"
    day_two_transition_symbols = {row.symbol for row in all_transitions if row.as_of == day_two}
    silently_carried = watchlisted_symbols - day_two_transition_symbols
    assert silently_carried, "fixture assumption broke: day two transitioned every symbol"

    import csv

    with (root / day_two.isoformat() / "report.csv").open(newline="", encoding="utf-8") as fh:
        by_symbol = {row["symbol"]: row for row in csv.DictReader(fh)}
    for symbol in silently_carried:
        row = by_symbol[symbol]
        assert row["bucket"] == BUCKET_WATCHLIST
        assert row["from_state"] == ""
        assert row["to_state"] == ""
        assert row["close"] != ""  # today's reading, not blank


def test_sanitize_path_segment_rejects_path_traversal() -> None:
    with pytest.raises(DataValidationError, match="strategy_version"):
        sanitize_path_segment("../escape", label="strategy_version")
    with pytest.raises(DataValidationError, match="config_hash"):
        sanitize_path_segment("a/b", label="config_hash")


def test_sanitize_path_segment_accepts_the_real_config_identity() -> None:
    loaded = load_config()
    assert sanitize_path_segment(loaded.version, label="strategy_version") == loaded.version
    assert sanitize_path_segment(loaded.config_hash, label="config_hash") == loaded.config_hash


def test_artifact_root_nests_by_strategy_version_then_config_hash(tmp_path: Path) -> None:
    root = artifact_root(tmp_path, strategy_version="SMM-V1.0.0", config_hash="abc123")
    assert root == tmp_path / "SMM-V1.0.0" / "abc123"


def test_git_commit_sha_is_unknown_outside_a_git_repository(tmp_path: Path) -> None:
    assert git_commit_sha(cwd=tmp_path) == "unknown"
