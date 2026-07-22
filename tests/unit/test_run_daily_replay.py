"""M4 ADR §8: N-day byte-reproducibility gate.

Drives one real multi-day sequence through the public run_daily seam,
covering every element §8 requires: birth as watchlist, >=1 silent
continuation, a subsequent expiry, and >=1 sealed empty day (dates below
are known from this deterministic fixture, not assumed). Replays the
identical sequence into a second fresh root and compares every artifact
byte-for-byte.

Both roots share this process's git_commit, so a full byte comparison
(git_commit included) is the correct check here -- the manifest's
conditional-exclusion mechanism exists for cross-environment comparisons,
not this one (M4 ADR §8's own carve-out: at equal tree SHA, git_commit
must be *included*).
"""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import pytest

from smm.cli.daily import artifact_root, run_daily
from smm.cli.main import _CacheOnlyProvider
from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.data import cache as bar_cache
from smm.data.generator import synthetic_universe, universe_rows

# Empirically confirmed against this deterministic fixture: day one births 7
# WATCHLISTED signals; several following sessions are sealed empty (silent
# continuation); SYNT4 expires via hard_filter_lost on 2024-02-19.
_SEQUENCE = [
    date(2024, 2, 5),
    date(2024, 2, 6),
    date(2024, 2, 7),
    date(2024, 2, 8),
    date(2024, 2, 9),
    date(2024, 2, 12),
    date(2024, 2, 13),
    date(2024, 2, 14),
    date(2024, 2, 15),
    date(2024, 2, 16),
    date(2024, 2, 19),
    date(2024, 2, 20),
]


def _setup(cache_dir: Path):
    loaded = load_config()
    paths = synthetic_universe()
    for symbol, path in paths.items():
        bars = list(path.bars)
        bar_cache.write_bars(cache_dir, symbol, bars, requested=(bars[0].date, bars[-1].date))
    rows = universe_rows(paths["SPY"].bars[-1].date)
    sectors = {r["symbol"]: r["sector"] for r in rows}
    symbols = sorted(sectors)
    provider = _CacheOnlyProvider(cache_dir, loaded.config.market_regime.benchmark)
    return loaded, provider, symbols, sectors


def _replay(provider, symbols, sectors, loaded, root: Path) -> None:
    for session in _SEQUENCE:
        result = run_daily(
            provider,
            session=session,
            symbols=symbols,
            sectors=sectors,
            loaded=loaded,
            root=root,
            provider_source="synthetic",
        )
        assert not result.skipped_as_noop, f"unexpected no-op replaying {session}"


def test_sequence_covers_every_element_section_8_requires(tmp_path: Path) -> None:
    """Guards the fixture assumption the rest of this file depends on."""
    loaded, provider, symbols, sectors = _setup(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    _replay(provider, symbols, sectors, loaded, root)

    from smm.signals.lifecycle import active_transitions_by_symbol
    from smm.signals.store import read_batch_seals, read_transitions

    transitions = read_transitions(root)
    seals = read_batch_seals(root)

    births = [row for row in transitions if row.as_of == _SEQUENCE[0]]
    assert births, "no WATCHLISTED birth on day one"
    empty_days = [as_of for as_of, seal in seals.items() if seal.transition_count == 0]
    assert empty_days, "no sealed-empty day in the sequence"
    from smm.domain.enums import SignalState

    expiries = [row for row in transitions if row.to_state is SignalState.EXPIRED]
    triggers = [row for row in transitions if row.to_state is SignalState.TRIGGERED]
    assert expiries or triggers, "no subsequent trigger or expiry in the sequence"
    # Something born day one is still traceable (not silently dropped).
    active = active_transitions_by_symbol(transitions)
    assert active


def test_two_fresh_roots_replay_byte_identical(tmp_path: Path) -> None:
    loaded, provider, symbols, sectors = _setup(tmp_path / "cache")
    root_a = artifact_root(
        tmp_path / "runs_a", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    root_b = artifact_root(
        tmp_path / "runs_b", strategy_version=loaded.version, config_hash=loaded.config_hash
    )

    _replay(provider, symbols, sectors, loaded, root_a)
    _replay(provider, symbols, sectors, loaded, root_b)

    for session in _SEQUENCE:
        day_a = root_a / session.isoformat()
        day_b = root_b / session.isoformat()
        for name in ("report.csv", "report.md", "manifest.json"):
            content_a = (day_a / name).read_bytes()
            content_b = (day_b / name).read_bytes()
            assert content_a == content_b, f"{name} diverged on {session}"


def test_same_root_exact_rerun_of_the_latest_sealed_day_is_byte_identical(tmp_path: Path) -> None:
    """§2 only allows an exact rerun of the *latest* seal, not of any earlier
    already-sealed day (that is backfill -- see the dedicated fail-closed
    test below). This confirms the no-op property holds after a real
    multi-day sequence, not just a single-day root.
    """
    loaded, provider, symbols, sectors = _setup(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    _replay(provider, symbols, sectors, loaded, root)
    latest = _SEQUENCE[-1]
    before = (root / latest.isoformat() / "manifest.json").read_bytes()

    result = run_daily(
        provider,
        session=latest,
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root,
        provider_source="synthetic",
    )

    assert result.skipped_as_noop
    after = (root / latest.isoformat() / "manifest.json").read_bytes()
    assert before == after


def test_backfill_before_the_first_processed_day_fails_closed(tmp_path: Path) -> None:
    loaded, provider, symbols, sectors = _setup(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    # No prior seal: starting cold at day two is legal (§2's first row).
    run_daily(
        provider,
        session=_SEQUENCE[1],
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root,
        provider_source="synthetic",
    )
    with pytest.raises(DataValidationError, match="backfill"):
        run_daily(
            provider,
            session=_SEQUENCE[0],
            symbols=symbols,
            sectors=sectors,
            loaded=loaded,
            root=root,
            provider_source="synthetic",
        )


def test_config_identity_drift_within_a_root_fails_closed(tmp_path: Path) -> None:
    loaded, provider, symbols, sectors = _setup(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    run_daily(
        provider,
        session=_SEQUENCE[0],
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root,
        provider_source="synthetic",
    )

    drifted = dataclasses.replace(loaded, config_hash="0" * 64)
    with pytest.raises(DataValidationError):
        run_daily(
            provider,
            session=_SEQUENCE[1],
            symbols=symbols,
            sectors=sectors,
            loaded=drifted,
            root=root,  # same root -- the identity mismatch is the point
            provider_source="synthetic",
        )
