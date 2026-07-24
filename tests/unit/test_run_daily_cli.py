"""`smm run-daily` CLI wiring. Offline only."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from smm.cli.main import app
from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.data.generator import synthetic_universe

runner = CliRunner()
AS_OF = synthetic_universe()["SPY"].bars[-1].date
CONFIG = load_config(None)


def run(tmp_path: Path, *extra: str):
    return runner.invoke(
        app,
        [
            "run-daily",
            "--as-of",
            AS_OF.isoformat(),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--runs-dir",
            str(tmp_path / "runs"),
            *extra,
        ],
    )


def test_offline_run_writes_the_bundle_and_prints_bucket_counts(tmp_path: Path) -> None:
    result = run(tmp_path)
    assert result.exit_code == 0, result.output
    assert "regime:" in result.output
    assert "new_trigger:" in result.output
    assert "manifest:" in result.output

    root = tmp_path / "runs" / CONFIG.version / CONFIG.config_hash / AS_OF.isoformat()
    assert (root / "report.csv").exists()
    assert (root / "report.md").exists()
    assert (root / "manifest.json").exists()


def test_rerun_is_reported_as_a_noop(tmp_path: Path) -> None:
    first = run(tmp_path)
    assert first.exit_code == 0, first.output
    second = run(tmp_path)
    assert second.exit_code == 0, second.output
    assert "no-op" in second.output


def test_fail_closed_run_exits_nonzero_and_leaves_no_manifest(tmp_path: Path) -> None:
    """M4 ADR §211 item 6: every failure path is non-zero exit and never
    produces a manifest that would look like a completed daily task.
    """
    first = run(tmp_path)
    assert first.exit_code == 0, first.output

    backfill_day = date(2024, 1, 3)  # a valid, but already-passed, session
    result = run(tmp_path, "--as-of", backfill_day.isoformat())

    assert result.exit_code == 1, result.output
    assert "fail-closed" in result.output
    root = tmp_path / "runs" / CONFIG.version / CONFIG.config_hash / backfill_day.isoformat()
    assert not (root / "manifest.json").exists()
    assert not root.exists()


def test_exhausted_market_provider_leaves_no_completion_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    import smm.data.yfinance_provider as provider_module

    class ExhaustedProvider:
        def __init__(self, **kwargs) -> None:
            pass

        def get_daily_bars(self, symbol, start, end):
            raise DataValidationError(
                f"{symbol}: provider attempts exhausted; attempts: "
                "1/provider_empty | 2/provider_empty | 3/provider_empty"
            )

        def get_calendar(self, start, end):
            raise DataValidationError(
                "SPY: provider attempts exhausted; attempts: "
                "1/provider_empty | 2/provider_empty | 3/provider_empty"
            )

    monkeypatch.setattr(provider_module, "YFinanceProvider", ExhaustedProvider)
    as_of = date(2026, 7, 23)
    result = runner.invoke(
        app,
        [
            "run-daily",
            "--source",
            "market",
            "--as-of",
            as_of.isoformat(),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )

    assert result.exit_code == 1, result.output
    assert "provider attempts exhausted" in result.output
    root = tmp_path / "runs" / CONFIG.version / CONFIG.config_hash / as_of.isoformat()
    assert not (root / "manifest.json").exists()
    assert not root.exists()


def test_bad_as_of_is_rejected(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run-daily",
            "--as-of",
            "not-a-date",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert result.exit_code == 2


def test_portfolio_snapshot_requires_explicit_shadow_mode(tmp_path: Path) -> None:
    result = run(tmp_path, "--portfolio-snapshot", str(tmp_path / "snapshot.json"))

    assert result.exit_code == 1
    assert "portfolio snapshot" in result.output
    assert not (tmp_path / "runs").exists()


def test_shadow_mode_requires_portfolio_snapshot(tmp_path: Path) -> None:
    result = run(tmp_path, "--mode", "shadow")

    assert result.exit_code == 1
    assert "portfolio snapshot" in result.output
    assert not (tmp_path / "runs").exists()
