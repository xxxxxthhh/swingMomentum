"""`smm ingest` (Plan v1.1 M1). Offline only — the synthetic source is the point."""

from __future__ import annotations

from datetime import date
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


def test_market_ingest_always_includes_the_benchmark(tmp_path: Path) -> None:
    """SPY is not a universe member (§10: common stock only) but must be fetched.

    The regime and the session calendar both read it; if ingest skipped it the
    calendar check would silently degrade to a no-op.
    """
    import smm.data.yfinance_provider as yp
    from smm.cli.main import _ingest_market
    from smm.config.loader import load_config

    requested: list[str] = []

    class StubProvider:
        def __init__(self, **_: object) -> None: ...

        def get_universe(self, as_of: date) -> list[str]:
            return ["AAPL", "MSFT"]

        def get_daily_bars(self, symbol: str, start: date, end: date) -> list[object]:
            requested.append(symbol)
            return []

    monkeypatch_target = yp.YFinanceProvider
    yp.YFinanceProvider = StubProvider  # type: ignore[misc]
    try:
        _ingest_market(load_config(None), tmp_path, date(2026, 7, 22), date(2025, 7, 22), None)
    finally:
        yp.YFinanceProvider = monkeypatch_target  # type: ignore[misc]

    assert requested[0] == "SPY", f"benchmark must be ingested first, got {requested[:3]}"
    assert set(requested) == {"SPY", "AAPL", "MSFT"}


def test_market_ingest_passes_the_configured_benchmark(tmp_path: Path) -> None:
    """A half-wired parameter is worse than a hardcoded one: ingest would fetch
    the configured benchmark while get_calendar kept reading the SPY default."""
    import smm.data.yfinance_provider as yp
    from smm.cli.main import _ingest_market
    from smm.config.loader import load_config

    seen: dict[str, object] = {}

    class StubProvider:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

        def get_universe(self, as_of: date) -> list[str]:
            return ["AAPL"]

        def get_daily_bars(self, symbol: str, start: date, end: date) -> list[object]:
            return []

    original = yp.YFinanceProvider
    yp.YFinanceProvider = StubProvider  # type: ignore[misc]
    try:
        loaded = load_config(None)
        _ingest_market(loaded, tmp_path, date(2026, 7, 22), date(2025, 7, 22), None)
    finally:
        yp.YFinanceProvider = original  # type: ignore[misc]

    assert seen["benchmark"] == loaded.config.market_regime.benchmark
