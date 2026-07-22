"""Deterministic Markdown rendering for the daily report (M4 ADR §4/§6)."""

from __future__ import annotations

from datetime import date

from smm.domain.enums import MarketRegime
from smm.report.format import format_float
from smm.report.rows import (
    BUCKET_NEW_TRIGGER,
    BUCKET_OPEN_TRIGGER,
    BUCKET_ORDER,
    BUCKET_TERMINAL_CHANGE,
    BUCKET_WATCHLIST,
    ReportRow,
)

_SECTION_TITLES = {
    BUCKET_NEW_TRIGGER: "new_trigger — 新触发",
    BUCKET_OPEN_TRIGGER: "open_trigger — 未处理触发",
    BUCKET_WATCHLIST: "watchlist — 观察池",
    BUCKET_TERMINAL_CHANGE: "terminal_change — 终态变化",
}

_ROW_COLUMNS = (
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
)


def render_markdown(
    rows: list[ReportRow],
    *,
    as_of: date,
    strategy_version: str,
    config_hash: str,
    regime: MarketRegime,
) -> str:
    """Render the fixed four-section daily report.

    Every section header states its row count explicitly, even when zero
    (§4) -- a section with no signals must read as a confirmed zero, not as
    a missing or truncated report.
    """
    by_bucket: dict[str, list[ReportRow]] = {bucket: [] for bucket in BUCKET_ORDER}
    for row in rows:
        by_bucket[row.bucket].append(row)

    lines = [
        f"# Daily Signal Report — {as_of.isoformat()}",
        "",
        f"- strategy_version: {strategy_version}",
        f"- config_hash: {config_hash}",
        f"- regime: {regime.value}",
        "",
    ]
    for bucket in BUCKET_ORDER:
        bucket_rows = by_bucket[bucket]
        lines.append(f"## {_SECTION_TITLES[bucket]} ({len(bucket_rows)})")
        lines.append("")
        lines.append("| " + " | ".join(_ROW_COLUMNS) + " |")
        lines.append("|" + "|".join("---" for _ in _ROW_COLUMNS) + "|")
        for row in bucket_rows:
            lines.append("| " + " | ".join(_render_cells(row)) + " |")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_cells(row: ReportRow) -> list[str]:
    return [
        row.symbol,
        row.signal_id,
        row.state.value,
        row.watchlist_entry.isoformat(),
        row.from_state.value if row.from_state is not None else "N/A",
        row.to_state.value if row.to_state is not None else "N/A",
        ",".join(row.reason_codes) or "N/A",
        format_float(row.close) or "N/A",
        format_float(row.breakout_level) or "N/A",
        format_float(row.relative_volume) or "N/A",
        format_float(row.extension_atr) or "N/A",
        format_float(row.momentum_score) or "N/A",
        format_float(row.relative_strength_score) or "N/A",
    ]
