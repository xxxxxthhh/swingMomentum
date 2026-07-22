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
        # Must be passed, not left to the default: otherwise ingest fetches the
        # configured benchmark while get_calendar keeps reading SPY, and the two
        # diverge the moment market_regime.benchmark changes. A half-wired
        # parameter is worse than a hardcoded one — it looks configured.
        benchmark=loaded.config.market_regime.benchmark,
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


@app.command("features")
def features_cmd(
    as_of: Annotated[str, typer.Option("--as-of", help="Session date (YYYY-MM-DD)")],
    source: Annotated[
        Source,
        typer.Option("--source", help="synthetic runs fully offline; market hits yfinance"),
    ] = Source.SYNTHETIC,
    cache_dir: Annotated[Path, typer.Option("--cache-dir")] = Path("data/cache"),
    out_dir: Annotated[Path, typer.Option("--out-dir")] = Path("data/features"),
    top: Annotated[int, typer.Option("--top", help="Rows to print")] = 10,
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Compute features, market regime and the scored cross-section for ``--as-of``."""
    try:
        loaded = load_config(config_path)
    except ConfigError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    try:
        session = date.fromisoformat(as_of)
    except ValueError as exc:
        typer.secho(f"--as-of must be YYYY-MM-DD, got {as_of!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    try:
        run, written = _run_features(loaded, session, source, cache_dir, out_dir)
    except FailClosedError as exc:
        typer.secho(f"fail-closed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    cs = run.cross_section
    typer.echo(f"as_of: {run.as_of}  regime: {run.regime.value}  source: {source.value}")
    typer.echo(f"version: {loaded.version}  config_hash: {loaded.config_hash}")
    typer.echo(
        f"ranked: {len(cs.ranking_universe)}  scored: {len(cs.candidates)}  "
        f"excluded: {len(run.excluded)}"
    )
    ordered = sorted(cs.candidates, key=lambda s: -(s.momentum_score or 0.0))
    if ordered:
        typer.echo(f"{'symbol':<8}{'sector':<24}{'mom':>7}{'rs':>7}")
        for row in ordered[:top]:
            typer.echo(
                f"{row.symbol:<8}{str(row.sector):<24}"
                f"{row.momentum_score:7.1f}{row.relative_strength_score:7.1f}"
            )
    else:
        typer.secho("no scored candidates", fg=typer.colors.YELLOW)
    typer.echo(f"snapshot: {written}")


def _run_features(loaded, session, source: Source, cache_dir: Path, out_dir: Path):
    from smm.data import cache as bar_cache
    from smm.data.generator import synthetic_universe, universe_rows
    from smm.features.pipeline import run_features
    from smm.features.snapshot import write_snapshot

    if source is Source.SYNTHETIC:
        paths = synthetic_universe()
        for symbol, path in paths.items():
            bars = list(path.bars)
            bar_cache.write_bars(
                cache_dir, symbol, bars, requested=(bars[0].date, bars[-1].date)
            )
        rows = universe_rows(session)
        sectors = {r["symbol"]: r["sector"] for r in rows}
        symbols = sorted(sectors)
        provider = _CacheOnlyProvider(cache_dir, loaded.config.market_regime.benchmark)
    else:
        from smm.data.universe import load_universe
        from smm.data.yfinance_provider import YFinanceProvider

        universe_dir = Path(__file__).resolve().parents[3] / "configs" / "universe"
        snapshot = load_universe(
            universe_dir, session, max_age_days=loaded.config.universe.max_snapshot_age_days
        )
        sectors = _snapshot_sectors(snapshot.path)
        symbols = list(snapshot.symbols)
        provider = YFinanceProvider(
            cache_dir=cache_dir,
            universe_dir=universe_dir,
            validation=loaded.config.validation,
            max_snapshot_age_days=loaded.config.universe.max_snapshot_age_days,
            benchmark=loaded.config.market_regime.benchmark,
        )

    run = run_features(
        provider, as_of=session, symbols=symbols, sectors=sectors, loaded=loaded
    )
    written = write_snapshot(
        out_dir,
        as_of=session,
        cross_section=run.cross_section,
        features=run.features,
        excluded=run.excluded,
        regime=run.regime,
        strategy_version=loaded.version,
        config_hash=loaded.config_hash,
        return_windows=loaded.config.features.return_windows,
        benchmarks={loaded.config.market_regime.benchmark.upper()}
        | {etf.upper() for etf in loaded.config.sector_benchmarks.values()},
    )
    return run, written


def _snapshot_sectors(path: Path | None) -> dict[str, str]:
    """Read `symbol -> sector` from a universe snapshot, skipping blanks.

    A blank sector is meaningful — it drops the symbol via rs_sector_missing
    rather than being guessed at (see configs/universe/README.md).
    """
    import csv

    if path is None:
        return {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return {
            row["symbol"].strip().upper(): row["sector"].strip()
            for row in csv.DictReader(fh)
            if row.get("sector", "").strip()
        }


class _CacheOnlyProvider:
    """Reads what ingest already cached. No network, no fallback."""

    def __init__(self, cache_dir: Path, benchmark: str) -> None:
        self._cache_dir = cache_dir
        self._benchmark = benchmark.upper()

    def get_daily_bars(self, symbol: str, start: date, end: date):
        from smm.data import cache as bar_cache

        return bar_cache.read_bars(self._cache_dir, symbol, start, end)

    def get_calendar(self, start: date, end: date) -> list[date]:
        """Sessions from the cached benchmark, same contract as the real provider.

        Implemented even though the M2 pipeline does not call it yet: a stub
        missing half the protocol fails the moment anything reaches for it, and
        the calendar wiring is a live follow-up.
        """
        from smm.data import cache as bar_cache

        return [b.date for b in bar_cache.read_bars(self._cache_dir, self._benchmark, start, end)]


def main() -> None:
    app()


if __name__ == "__main__":
    main()
