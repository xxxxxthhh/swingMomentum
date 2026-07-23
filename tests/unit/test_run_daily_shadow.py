"""M7 Slice 1 shadow runtime assembly through the public daily seam."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from smm.cli.daily import artifact_root, run_daily
from smm.cli.main import _CacheOnlyProvider
from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.data import cache as bar_cache
from smm.data.generator import breakout_success, trending
from smm.domain.enums import SignalState
from smm.domain.models import PortfolioSnapshot
from smm.report.manifest import ExecutionMode
from smm.signals.store import read_transitions

REPO = Path(__file__).resolve().parents[2]
M7_CONFIG = REPO / "configs" / "smm_v1_1_0.yaml"


def _setup_shadow_fixture(cache_dir: Path):
    """Build D=NVDA trigger and X=MSFT trigger on adjacent sessions.

    NVDA's persisted D trigger must be consumed by X risk evaluation, while
    MSFT's X-close scanner trigger proves that the later scan cannot feed the
    earlier risk batch.
    """

    loaded = load_config(M7_CONFIG)
    # This seed creates an M4-valid D trigger whose immutable D facts also
    # produce a stop inside the frozen ATR distance bounds.  The later MSFT
    # trigger remains independent evidence for the X ordering assertion.
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

    provider = _CacheOnlyProvider(cache_dir, loaded.config.market_regime.benchmark)
    prior_sessions = tuple(
        bar.date for bar in nvda.bars[nvda.breakout_index - 8 : nvda.breakout_index]
    )
    trigger_day = nvda.bars[nvda.breakout_index].date
    evaluation_day = msft.bars[msft.breakout_index].date
    assert evaluation_day > trigger_day
    return (
        loaded,
        provider,
        ["MSFT", "NVDA"],
        {"MSFT": "information_technology", "NVDA": "information_technology"},
        prior_sessions,
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
    path.write_text(
        json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=False),
        encoding="utf-8",
    )
    return path


def test_shadow_consumes_only_start_of_run_backlog_and_seals_the_strict_bundle(
    tmp_path: Path,
) -> None:
    (
        loaded,
        provider,
        symbols,
        sectors,
        prior_sessions,
        trigger_day,
        evaluation_day,
    ) = _setup_shadow_fixture(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    common = dict(
        symbols=symbols,
        sectors=sectors,
        loaded=loaded,
        root=root,
        provider_source="synthetic",
    )

    for prior_session in prior_sessions:
        run_daily(provider, session=prior_session, **common)
    run_daily(provider, session=trigger_day, **common)
    d_trigger = next(
        row
        for row in read_transitions(root)
        if row.symbol == "NVDA"
        and row.as_of == trigger_day
        and row.to_state is SignalState.TRIGGERED
    )
    snapshot_source = _write_snapshot(
        tmp_path / "operator-snapshot.json",
        as_of=evaluation_day,
        loaded=loaded,
    )

    first = run_daily(
        provider,
        session=evaluation_day,
        mode=ExecutionMode.SHADOW,
        portfolio_snapshot=snapshot_source,
        **common,
    )

    assert not first.skipped_as_noop
    day_dir = root / evaluation_day.isoformat()
    assert {entry.name for entry in day_dir.iterdir()} == {
        "report.csv",
        "report.md",
        f"features_{evaluation_day.isoformat()}.parquet",
        "portfolio_snapshot.json",
        "circuit_state.json",
        "risk_decisions.json",
        "manifest.json",
    }
    manifest_text = (day_dir / "manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["execution_mode"] == "shadow"
    assert set(manifest["artifacts"]) == {
        "report_csv",
        "report_markdown",
        "features_snapshot",
        "portfolio_snapshot",
        "circuit_state",
        "risk_decisions",
    }
    assert str(snapshot_source) not in manifest_text

    # Slice 1 has no Paper/ledger fact source.  Its CircuitState contract is
    # deliberately neutral and must remain auditable rather than becoming an
    # implicit assumption in the runtime path.
    circuit_state = json.loads(
        (day_dir / "circuit_state.json").read_text(encoding="utf-8")
    )
    assert circuit_state["realized_loss_r_for_session"] == "0.000000"
    assert circuit_state["marked_equity"] == "100000.000000"
    assert circuit_state["high_water_equity"] == "100000.000000"
    assert circuit_state["drawdown"] == "0.000000"
    assert circuit_state["new_entries_blocked"] is False
    assert circuit_state["entry_risk_multiplier"] == "1.000000"
    assert circuit_state["reason_codes"] == []
    assert manifest["circuit_state_identity"] == circuit_state["circuit_state_identity"]

    decisions = json.loads((day_dir / "risk_decisions.json").read_text(encoding="utf-8"))
    assert [decision["signal_id"] for decision in decisions] == [d_trigger.signal_id]

    transitions = read_transitions(root)
    assert any(
        row.signal_id == d_trigger.signal_id
        and row.as_of == evaluation_day
        and row.to_state in {SignalState.RISK_ACCEPTED, SignalState.RISK_REJECTED}
        for row in transitions
    )
    same_session_trigger = next(
        row
        for row in transitions
        if row.symbol == "MSFT"
        and row.as_of == evaluation_day
        and row.to_state is SignalState.TRIGGERED
    )
    assert same_session_trigger.signal_id not in {decision["signal_id"] for decision in decisions}

    before = (day_dir / "manifest.json").read_bytes()
    second = run_daily(
        provider,
        session=evaluation_day,
        mode=ExecutionMode.SHADOW,
        portfolio_snapshot=snapshot_source,
        **common,
    )
    assert second.skipped_as_noop
    assert (day_dir / "manifest.json").read_bytes() == before

    changed_snapshot = json.loads(snapshot_source.read_text(encoding="utf-8"))
    changed_snapshot["available_cash"] = "99999"
    snapshot_source.write_text(json.dumps(changed_snapshot), encoding="utf-8")
    with pytest.raises(DataValidationError, match="different content"):
        run_daily(
            provider,
            session=evaluation_day,
            mode=ExecutionMode.SHADOW,
            portfolio_snapshot=snapshot_source,
            **common,
        )
    assert (day_dir / "manifest.json").read_bytes() == before


def test_shadow_requires_a_snapshot_before_creating_any_root(tmp_path: Path) -> None:
    (
        loaded,
        provider,
        symbols,
        sectors,
        _prior_sessions,
        _trigger_day,
        evaluation_day,
    ) = _setup_shadow_fixture(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )

    with pytest.raises(DataValidationError, match="portfolio snapshot"):
        run_daily(
            provider,
            session=evaluation_day,
            symbols=symbols,
            sectors=sectors,
            loaded=loaded,
            root=root,
            provider_source="synthetic",
            mode=ExecutionMode.SHADOW,
        )
    assert not root.exists()


def test_shadow_rejects_a_mismatched_snapshot_before_creating_any_root(tmp_path: Path) -> None:
    (
        loaded,
        provider,
        symbols,
        sectors,
        _prior_sessions,
        trigger_day,
        evaluation_day,
    ) = _setup_shadow_fixture(tmp_path / "cache")
    root = artifact_root(
        tmp_path / "runs", strategy_version=loaded.version, config_hash=loaded.config_hash
    )
    snapshot_source = _write_snapshot(
        tmp_path / "wrong-session.json",
        as_of=trigger_day,
        loaded=loaded,
    )

    with pytest.raises(DataValidationError, match="identity"):
        run_daily(
            provider,
            session=evaluation_day,
            symbols=symbols,
            sectors=sectors,
            loaded=loaded,
            root=root,
            provider_source="synthetic",
            mode=ExecutionMode.SHADOW,
            portfolio_snapshot=snapshot_source,
        )
    assert not root.exists()
