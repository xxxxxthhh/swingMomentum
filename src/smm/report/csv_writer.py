"""Deterministic CSV rendering for the daily report (M4 ADR §4/§6)."""

from __future__ import annotations

import csv
import io

from smm.report.format import format_float
from smm.report.rows import ReportRow

FIELDS = (
    "as_of",
    "bucket",
    "symbol",
    "signal_id",
    "state",
    "watchlist_entry",
    "from_state",
    "to_state",
    "reason_codes",
    "close",
    "breakout_level",
    "relative_volume",
    "extension_atr",
    "momentum_score",
    "relative_strength_score",
    "regime",
    "strategy_version",
    "config_hash",
)


def render_csv(rows: list[ReportRow]) -> str:
    """Render report rows to CSV text.

    Always writes the header, even for zero rows -- a day with no signals
    is a valid, complete result (§4), not a missing artifact.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(FIELDS)
    for row in rows:
        writer.writerow(_render_row(row))
    return buffer.getvalue()


def _render_row(row: ReportRow) -> list[str]:
    return [
        row.as_of.isoformat(),
        row.bucket,
        row.symbol,
        row.signal_id,
        row.state.value,
        row.watchlist_entry.isoformat(),
        row.from_state.value if row.from_state is not None else "",
        row.to_state.value if row.to_state is not None else "",
        ",".join(row.reason_codes),
        format_float(row.close) or "",
        format_float(row.breakout_level) or "",
        format_float(row.relative_volume) or "",
        format_float(row.extension_atr) or "",
        format_float(row.momentum_score) or "",
        format_float(row.relative_strength_score) or "",
        row.regime.value,
        row.strategy_version,
        row.config_hash,
    ]
