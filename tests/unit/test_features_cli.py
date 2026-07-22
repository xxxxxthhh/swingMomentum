"""`smm features` and snapshot persistence (Plan v1.1 M2). Offline only."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smm.cli.main import app
from smm.config.loader import load_config
from smm.data.generator import synthetic_universe
from smm.features.snapshot import read_metadata, snapshot_path

runner = CliRunner()
AS_OF = synthetic_universe()["SPY"].bars[-1].date
CONFIG = load_config(None)


def run(tmp_path: Path, *extra: str):
    return runner.invoke(
        app,
        [
            "features",
            "--as-of",
            AS_OF.isoformat(),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out-dir",
            str(tmp_path / "feat"),
            *extra,
        ],
    )


def test_offline_run_produces_scored_candidates(tmp_path: Path) -> None:
    """The offline path must yield a real result, not an all-missing report."""
    result = run(tmp_path)
    assert result.exit_code == 0, result.output
    assert "regime: risk_on" in result.output
    assert "scored: 8" in result.output
    assert "SYNT1" in result.output


def test_leader_is_ranked_above_laggard_in_the_output(tmp_path: Path) -> None:
    output = run(tmp_path).output
    assert output.index("SYNT1") < output.index("SYNT4")


def test_run_reports_the_audit_identity(tmp_path: Path) -> None:
    output = run(tmp_path).output
    assert CONFIG.version in output
    assert CONFIG.config_hash in output


def test_snapshot_records_reproduction_identity(tmp_path: Path) -> None:
    run(tmp_path)
    meta = read_metadata(tmp_path / "feat", AS_OF)
    assert meta["as_of"] == AS_OF.isoformat()
    assert meta["strategy_version"] == CONFIG.version
    assert meta["config_hash"] == CONFIG.config_hash
    assert meta["regime"] == "risk_on"


def test_snapshot_records_the_ranking_universe(tmp_path: Path) -> None:
    """Percentiles mean nothing without the set they were computed over."""
    run(tmp_path)
    meta = read_metadata(tmp_path / "feat", AS_OF)
    assert meta["ranking_universe_size"] == "8"
    assert "SYNT1" in meta["ranking_universe"]
    assert "SPY" not in meta["ranking_universe"].split(",")
    assert "SPY:benchmark" in meta["excluded_from_ranking"]


def test_snapshot_keeps_excluded_symbols(tmp_path: Path) -> None:
    """'Why is this name absent today' must be answerable from the artifact."""
    import pyarrow.parquet as pq

    run(tmp_path)
    table = pq.read_table(snapshot_path(tmp_path / "feat", AS_OF))
    reasons = table.column("excluded_reason").to_pylist()
    assert any(r is not None for r in reasons)


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    first = run(tmp_path).output
    second = run(tmp_path).output
    assert first == second


def test_bad_as_of_is_rejected(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["features", "--as-of", "23-02-2024", "--out-dir", str(tmp_path)]
    )
    assert result.exit_code == 2
    assert "YYYY-MM-DD" in result.output


def test_as_of_without_history_fails_closed(tmp_path: Path) -> None:
    """No benchmark features => no regime => the run must fail, not emit a
    normal-looking report that can never contain a candidate (ADR R2)."""
    result = runner.invoke(
        app,
        [
            "features",
            "--as-of",
            date(2019, 1, 2).isoformat(),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out-dir",
            str(tmp_path / "feat"),
        ],
    )
    assert result.exit_code == 1
    assert "fail-closed" in result.output
    assert not (tmp_path / "feat").exists()


@pytest.mark.parametrize("flag", ["--top"])
def test_top_limits_printed_rows(tmp_path: Path, flag: str) -> None:
    output = run(tmp_path, flag, "2").output
    assert "SYNT1" in output
    assert "SYNT4" not in output


def test_snapshot_records_benchmark_feature_rows(tmp_path: Path) -> None:
    """The regime must be re-checkable from the snapshot alone.

    Reporting a regime without the close and moving averages that produced it
    leaves an artifact nobody can verify after the fact.
    """
    import pyarrow.parquet as pq

    run(tmp_path)
    rows = {
        r["symbol"]: r
        for r in pq.read_table(snapshot_path(tmp_path / "feat", AS_OF)).to_pylist()
    }
    spy = rows["SPY"]
    assert spy["role"] == "benchmark"
    assert spy["close"] is not None
    assert spy["sma_fast"] is not None
    assert spy["sma_slow"] is not None
    # Risk-On, recomputed from the snapshot's own numbers.
    assert spy["close"] > spy["sma_fast"] > spy["sma_slow"]


def test_snapshot_distinguishes_members_from_benchmarks(tmp_path: Path) -> None:
    import pyarrow.parquet as pq

    run(tmp_path)
    rows = {
        r["symbol"]: r
        for r in pq.read_table(snapshot_path(tmp_path / "feat", AS_OF)).to_pylist()
    }
    assert rows["SYNT1"]["role"] == "member"
    assert rows["XLK"]["role"] == "benchmark"
