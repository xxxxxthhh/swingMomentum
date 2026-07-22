"""Deterministic serialization primitives (M4 ADR §6/§8).

Byte-for-byte reproducibility needs exactly one code path for turning a
float into text. `repr`/`str`/an f-string without an explicit format spec
can each pick a different digit count for the same value depending on
Python version or platform float-printing internals -- picking one fixed
format here, and never touching a float any other way in the report path,
is what makes the N-day replay gate (§8) mean anything.

Precision matches the existing CSV-serialization precedent in
``smm.data.generator`` (six decimal places), not a new convention.
"""

from __future__ import annotations

import json
from typing import Any

_FLOAT_PRECISION = 6


def format_float(value: float | None) -> str | None:
    """The one legal way to turn a float into text anywhere in the report.

    Returns ``None`` (not a formatted string) when ``value`` is ``None`` --
    every writer must pick its own explicit missing-value marker rather than
    this function inventing one, since "no value" means different things in
    a CSV cell vs. a Markdown table cell.
    """
    if value is None:
        return None
    return f"{value:.{_FLOAT_PRECISION}f}"


def dump_json_deterministic(payload: dict[str, Any]) -> str:
    """Canonical JSON text: sorted keys, fixed separators, trailing newline.

    No wall-clock, random run id, or absolute temp path may appear anywhere
    in ``payload`` -- that is the caller's responsibility, not this
    function's; this only fixes *how* whatever is given gets serialized.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
