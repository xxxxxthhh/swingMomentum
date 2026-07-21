"""Stable identity helpers for signals and setups."""

from __future__ import annotations

import hashlib
from datetime import date


def make_setup_key(
    symbol: str,
    *,
    breakout_window: int,
    breakout_level: float,
    anchor_date: date,
) -> str:
    """Build a stable setup key for a breakout-style structure.

    Same symbol, window, level (rounded), and anchor date ⇒ same key.
    ``breakout_level`` is rounded to 4 decimal places to avoid float noise.
    """
    level = f"{breakout_level:.4f}"
    raw = f"{symbol.upper()}|bw{breakout_window}|lvl{level}|a{anchor_date.isoformat()}"
    return raw


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
