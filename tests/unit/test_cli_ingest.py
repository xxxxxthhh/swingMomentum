"""`smm ingest` (Plan v1.1 M1). Offline only — the synthetic source is the point."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from smm.cli.main import app
from smm.data import cache

runner = CliRunner()


def test_ingest_synthetic_runs_offline(tmp_path: Path) -> None:
    result = runner.invoke(app, ["ingest", "--as-of", "2024-01-26", "--cache-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "NVDA: 280 bars cached" in result.output
    assert len(cache.read_bars(tmp_path, "NVDA")) == 280


def test_ingest_reports_the_audit_identity(tmp_path: Path) -> None:
    """Every run must state which version and config produced the cache."""
    result = runner.invoke(app, ["ingest", "--as-of", "2024-01-26", "--cache-dir", str(tmp_path)])
    assert "SMM-V1.0.0" in result.output
    assert "config_hash:" in result.output


def test_ingest_is_idempotent(tmp_path: Path) -> None:
    for _ in range(3):
        result = runner.invoke(
            app, ["ingest", "--as-of", "2024-01-26", "--cache-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
    assert len(cache.read_bars(tmp_path, "NVDA")) == 280
    assert len(cache.read_bars(tmp_path, "SPY")) == 300


def test_symbol_filter(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["ingest", "--as-of", "2024-01-26", "--cache-dir", str(tmp_path), "-s", "NVDA"],
    )
    assert result.exit_code == 0
    assert cache.read_bars(tmp_path, "NVDA")
    assert cache.read_bars(tmp_path, "SPY") == []


def test_bad_as_of_is_rejected(tmp_path: Path) -> None:
    result = runner.invoke(app, ["ingest", "--as-of", "26-01-2024", "--cache-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "YYYY-MM-DD" in result.output


def test_bad_config_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "ingest",
            "--as-of",
            "2024-01-26",
            "--cache-dir",
            str(tmp_path),
            "--config",
            str(tmp_path / "absent.yaml"),
        ],
    )
    assert result.exit_code == 1
    assert "config error" in result.output


def test_show_config_still_works() -> None:
    result = runner.invoke(app, ["show-config"])
    assert result.exit_code == 0
    assert "SMM-V1.0.0" in result.output
