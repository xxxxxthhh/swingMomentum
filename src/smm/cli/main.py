"""CLI: config inspection and market-data ingest."""

from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from smm import __version__
from smm.config.loader import load_config
from smm.core.errors import ConfigError, FailClosedError

app = typer.Typer(help="Swing Momentum (SMM) CLI", no_args_is_help=True)


class Source(StrEnum):
    """Where `ingest` gets its bars."""

    SYNTHETIC = "synthetic"
    MARKET = "market"


@app.command("version")
def version_cmd() -> None:
    """Print package version."""
    typer.echo(__version__)


@app.command("show-config")
def show_config(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Path to strategy YAML (default: configs/smm_v1_0_0.yaml)",
        ),
    ] = None,
) -> None:
    """Load config and print strategy version + config_hash."""
    try:
        loaded = load_config(path)
    except ConfigError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"version: {loaded.version}")
    typer.echo(f"config_hash: {loaded.config_hash}")
    if loaded.path is not None:
        typer.echo(f"path: {loaded.path}")


@app.command("ingest")
def ingest(
    as_of: Annotated[
        str, typer.Option("--as-of", help="Session date to ingest up to (YYYY-MM-DD)")
    ],
    source: Annotated[
        Source,
        typer.Option("--source", help="synthetic runs fully offline; market hits yfinance"),
    ] = Source.SYNTHETIC,
    cache_dir: Annotated[
        Path, typer.Option("--cache-dir", help="Parquet cache root")
    ] = Path("data/cache"),
    lookback_days: Annotated[
        int, typer.Option("--lookback-days", help="Calendar days of history to request")
    ] = 500,
    symbol: Annotated[
        list[str] | None,
        typer.Option("--symbol", "-s", help="Restrict to these symbols (repeatable)"),
    ] = None,
    config_path: Annotated[
        Path | None, typer.Option("--config", "-c", help="Strategy YAML")
    ] = None,
) -> None:
    """Fetch, validate and cache daily bars up to ``--as-of``.

    Fails closed: any §12.4 violation aborts the run rather than caching a
    series the scanner would later treat as clean.
    """
    try:
        loaded = load_config(config_path)
    except ConfigError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    try:
        end = date.fromisoformat(as_of)
    except ValueError as exc:
        typer.secho(f"--as-of must be YYYY-MM-DD, got {as_of!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    start = end - timedelta(days=lookback_days)

    typer.echo(f"as_of: {end}  source: {source.value}")
    typer.echo(f"version: {loaded.version}  config_hash: {loaded.config_hash}")

    try:
        written = (
            _ingest_synthetic(loaded, cache_dir, symbol)
            if source is Source.SYNTHETIC
            else _ingest_market(loaded, cache_dir, end, start, symbol)
        )
    except FailClosedError as exc:
        typer.secho(f"fail-closed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    for name, count in written:
        typer.echo(f"  {name}: {count} bars cached")
    typer.echo(f"cache: {cache_dir.resolve()}")


def _ingest_synthetic(
    loaded, cache_dir: Path, symbols: list[str] | None
) -> list[tuple[str, int]]:
    """Offline path: deterministic generated paths straight into the cache.

    Generated bars go through the same validation as real ones — a fixture the
    validator would reject is a fixture that proves nothing.
    """
    from smm.data import cache
    from smm.data.generator import SYNTHETIC_PATHS
    from smm.data.validation import validate_bars

    written: list[tuple[str, int]] = []
    wanted = {s.upper() for s in symbols} if symbols else None
    for build in SYNTHETIC_PATHS.values():
        path = build()
        if wanted and path.symbol not in wanted:
            continue
        bars = list(path.bars)
        validate_bars(bars, cfg=loaded.config.validation)
        cache.write_bars(cache_dir, path.symbol, bars)
        written.append((path.symbol, len(bars)))
    return written


def _ingest_market(
    loaded,
    cache_dir: Path,
    end: date,
    start: date,
    symbols: list[str] | None,
) -> list[tuple[str, int]]:
    from smm.data.yfinance_provider import YFinanceProvider

    universe_dir = Path(__file__).resolve().parents[3] / "configs" / "universe"
    provider = YFinanceProvider(
        cache_dir=cache_dir,
        universe_dir=universe_dir,
        validation=loaded.config.validation,
        max_snapshot_age_days=loaded.config.universe.max_snapshot_age_days,
    )
    wanted = [s.upper() for s in symbols] if symbols else provider.get_universe(end)
    # The benchmark is not a universe member — the universe is common stock only
    # (constitution §10), and SPY is an ETF. It still has to be ingested: the
    # market regime and the session calendar both read it, and without it the
    # calendar check silently degrades to a no-op.
    benchmark = loaded.config.market_regime.benchmark.upper()
    if benchmark not in wanted:
        wanted = [benchmark, *wanted]

    written: list[tuple[str, int]] = []
    for sym in wanted:
        bars = provider.get_daily_bars(sym, start, end)
        written.append((sym, len(bars)))
    return written


def main() -> None:
    app()


if __name__ == "__main__":
    main()
