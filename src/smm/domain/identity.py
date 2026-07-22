"""Stable identity helpers for signals and setups."""

from __future__ import annotations

import hashlib
from datetime import date


def make_setup_key(
    symbol: str,
    *,
    breakout_window: int,
    watchlist_entry: date,
) -> str:
    """Build a stable setup key for a breakout-style structure.

    Identity is anchored to the observation window, not to the rolling
    breakout level. The latter is a daily property and would manufacture a new
    logical signal whenever the trailing high changes.
    """
    return f"{symbol.upper()}|bw{breakout_window}|w{watchlist_entry.isoformat()}"


def make_logical_signal_id(
    *,
    symbol: str,
    setup_key: str,
    strategy_version: str,
) -> str:
    """Deterministic logical signal id (not a random UUID).

    Format: ``{version}:{symbol}:{sha256(setup_key)[:16]}`` so ids stay short
    and stable across re-runs for the same setup.
    """
    digest = hashlib.sha256(setup_key.encode("utf-8")).hexdigest()[:16]
    return f"{strategy_version}:{symbol.upper()}:{digest}"
