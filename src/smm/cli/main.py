"""Minimal CLI: version and config inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from smm import __version__
from smm.config.loader import load_config
from smm.core.errors import ConfigError

app = typer.Typer(help="Swing Momentum (SMM) CLI", no_args_is_help=True)


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
