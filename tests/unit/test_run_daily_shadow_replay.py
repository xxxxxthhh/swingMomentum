"""M7 Slice 2: N-day byte-reproducibility gate for the shadow runtime."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from smm.cli.daily import artifact_root, run_daily
from smm.cli.main import _CacheOnlyProvider
from smm.config.loader import load_config
from smm.data import cache as bar_cache
from smm.data.generator import breakout_success, trending
from smm.domain.enums import SignalState
from smm.domain.models import PortfolioSnapshot
from smm.report.manifest import ExecutionMode
from smm.signals.store import read_transitions

REPO = Path(__file__).resolve().parents[2]
M7_CONFIG = REPO / "configs" / "smm_v1_1_0.yaml"


def _setup_shadow_replay_fixture(cache_dir: Path):
    """Return a sequence with a persisted trigger consumed on a later day."""

    loaded = load_config(M7_CONFIG)
    nvda = breakout_success(symbol="NVDA", total_bars=280, seed="shadow-nvda-2")
    msft = breakout_success(symbol="MSFT", total_bars=281)
    spy = trending(
        "SPY",
        total_bars=281,
        start_price=400.0,
        base_volume=50_000_000,
        drift=0.001,
        seed="shadow-spy",
    )
    xlk = trending(
        "XLK",
        total_bars=281,
        start_price=150.0,
        base_volume=50_000_000,
        drift=0.001,
        seed="shadow-xlk",
    )
    for path in (nvda, msft, spy, xlk):
        bar_cache.write_bars(
            cache_dir,
            path.symbol,
            path.bars,
            requested=(path.bars[0].date, path.bars[-1].date),
        )

    prior_sessions = tuple(
        bar.date for bar in nvda.bars[nvda.breakout_index - 8 : nvda.breakout_index]
    )
    trigger_day = nvda.bars[nvda.breakout_index].date
    evaluation_day = msft.bars[msft.breakout_index].date
    assert evaluation_day > trigger_day
    return (
        loaded,
        _CacheOnlyProvider(cache_dir, loaded.config.market_regime.benchmark),
        ["MSFT", "NVDA"],
        {"MSFT": "information_technology", "NVDA": "information_technology"},
        (*prior_sessions, trigger_day, evaluation_day),
        trigger_day,
        evaluation_day,
    )


def _write_snapshot(path: Path, *, as_of: date, loaded) -> Path:
    snapshot = PortfolioSnapshot(
        as_of=as_of,
        account_equity="100000",
        available_cash="100000",
        gross_exposure_capital="0",
        portfolio_initial_risk="0",
        sector_initial_risk={},
        cluster_initial_risk={},
        open_symbols=frozenset(),
        reserved_signal_ids=frozenset(),
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _shadow_replay(
    provider,
    *,
    symbols: list[str],
    sectors: dict[str, str],
    loaded,
    root: Path,
    source_dir: Path,
    sessions: tuple[date, ...],
) -> dict[date, Path]:
    sources: dict[date, Path] = {}
    for session in sessions:
        source = _write_snapshot(
            source_dir / f"{session.isoformat()}.json",
            as_of=session,
            loaded=loaded,
        )
        result = run_daily(
            provider,
            session=session,
            symbols=symbols,
            sectors=sectors,
            loaded=loaded,
            root=root,
            provider_source="synthetic",
            mode=ExecutionMode.SHADOW,
            portfolio_snapshot=source,
        )
        assert not result.skipped_as_noop, f"unexpected no-op replaying {session}"
        sources[session] = source
    return sources


def _assert_equal_shadow_roots(root_a: Path, root_b: Path, sessions: tuple[date, ...]) -> None:
    assert (root_a / "signal_transitions.parquet").read_bytes() == (
        root_b / "signal_transitions.parquet"
    ).read_bytes()
    for session in sessions:
        day_a = root_a / session.isoformat()
        day_b = root_b / session.isoformat()
        assert sorted(path.name for path in day_a.iterdir()) == sorted(
            path.name for path in day_b.iterdir()
        )
        for name in sorted(path.name for path in day_a.iterdir()):
            assert (day_a / name).read_bytes() == (day_b / name).read_bytes(), (
                f"{name} diverged on {session}"
            )


def test_shadow_n_day_replay_is_byte_identical_across_fresh_roots(tmp_path: Path) -> None:
    (
        loaded,
        provider,
        symbols,
        sectors,
        sessions,
        trigger_day,
        evaluation_day,
    ) = _setup_shadow_replay_fixture(tmp_path / "cache")
    root_a = artifact_root(
        tmp_path / "runs_a", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    root_b = artifact_root(
        tmp_path / "runs_b", strategy_version=loaded.version, config_hash=loaded.config_hash
    )

    sources_a = _shadow_replay(
        provider,
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root_a,
        source_dir=tmp_path / "operator_a",
        sessions=sessions,
    )
    _shadow_replay(
        provider,
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root_b,
        source_dir=tmp_path / "operator_b",
        sessions=sessions,
    )

    _assert_equal_shadow_roots(root_a, root_b, sessions)
    transitions = read_transitions(root_a)
    nvda_trigger = next(
        row
        for row in transitions
        if row.symbol == "NVDA"
        and row.as_of == trigger_day
        and row.to_state is SignalState.TRIGGERED
    )
    assert any(
        row.signal_id == nvda_trigger.signal_id
        and row.as_of == evaluation_day
        and row.to_state in {SignalState.RISK_ACCEPTED, SignalState.RISK_REJECTED}
        for row in transitions
    )
    for session in sessions:
        manifest_text = (root_a / session.isoformat() / "manifest.json").read_text(
            encoding="utf-8"
        )
        assert json.loads(manifest_text)["execution_mode"] == "shadow"
        assert str(sources_a[session]) not in manifest_text


def test_shadow_n_day_latest_session_exact_rerun_is_a_noop(tmp_path: Path) -> None:
    (
        loaded,
        provider,
        symbols,
        sectors,
        sessions,
        _trigger_day,
        _evaluation_day,
    ) = _setup_shadow_replay_fixture(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    sources = _shadow_replay(
        provider,
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root,
        source_dir=tmp_path / "operator",
        sessions=sessions,
    )
    latest = sessions[-1]
    before = {
        path.name: path.read_bytes() for path in sorted((root / latest.isoformat()).iterdir())
    }

    result = run_daily(
        provider,
        session=latest,
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root,
        provider_source="synthetic",
        mode=ExecutionMode.SHADOW,
        portfolio_snapshot=sources[latest],
    )

    assert result.skipped_as_noop
    after = {
        path.name: path.read_bytes() for path in sorted((root / latest.isoformat()).iterdir())
    }
    assert after == before
