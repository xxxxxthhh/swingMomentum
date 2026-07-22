"""Feature-snapshot persistence (Plan v1.1 M2 §5).

One file per ``as_of``. Every snapshot carries the identity needed to reproduce
it — ``as_of``, ``strategy_version``, ``config_hash`` — plus the **definition of
the ranking universe** it was scored against. Percentiles are only meaningful
relative to the set they were computed over, so a snapshot that recorded scores
without recording that set would not be reproducible even with the same code.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from smm.domain.enums import MarketRegime
from smm.features.cross_section import CrossSection
from smm.features.engine import ExcludedSymbol, SymbolFeatures

_FLOAT_FIELDS = (
    "close",
    "sma_fast",
    "sma_slow",
    "ema",
    "sma_fast_slope",
    "sma_slow_slope",
    "atr",
    "high_52w",
    "distance_from_high",
    "drawdown",
    "extension_atr",
    "avg_dollar_volume",
)


def snapshot_path(root: Path | str, as_of: date) -> Path:
    return Path(root) / f"features_{as_of.isoformat()}.parquet"


def write_snapshot(
    root: Path | str,
    *,
    as_of: date,
    cross_section: CrossSection,
    features: dict[str, SymbolFeatures],
    excluded: dict[str, ExcludedSymbol],
    regime: MarketRegime,
    strategy_version: str,
    config_hash: str,
    return_windows: list[int],
) -> Path:
    """Write the as-of snapshot, including the symbols that were excluded.

    Excluded symbols are recorded rather than dropped: "why is this name absent
    from today's report" is an auditing question the snapshot should answer by
    itself.

    Benchmarks get rows too, marked ``benchmark``. They are not ranked, but the
    regime is derived from the benchmark's close and moving averages — without
    those values the snapshot reports a regime that cannot be re-checked from
    the snapshot alone, which contradicts the point of recording identity at
    all.
    """
    rows: list[dict[str, object]] = []
    for symbol in sorted(set(cross_section.scored) | set(excluded) | set(features)):
        scored = cross_section.scored.get(symbol)
        feature = features.get(symbol)
        gap = excluded.get(symbol)
        is_benchmark = symbol in cross_section.excluded_from_ranking and (
            cross_section.excluded_from_ranking[symbol] == "benchmark"
        )
        row: dict[str, object] = {
            "symbol": symbol,
            "role": "benchmark" if is_benchmark else "member",
            "sector": (scored.sector if scored else None),
            "bar_count": (feature.bar_count if feature else (gap.bar_count if gap else 0)),
            "excluded_reason": (gap.reason if gap else None),
            "reason_codes": ",".join(scored.reason_codes) if scored else "",
            "rs_spy_short": scored.rs_spy_short if scored else None,
            "rs_spy_long": scored.rs_spy_long if scored else None,
            "rs_sector": scored.rs_sector if scored else None,
            "momentum_score": scored.momentum_score if scored else None,
            "relative_strength_score": (
                scored.relative_strength_score if scored else None
            ),
        }
        for name in _FLOAT_FIELDS:
            row[name] = getattr(feature, name) if feature else None
        for window in return_windows:
            row[f"return_{window}"] = (
                feature.returns.get(window) if feature else None
            )
        rows.append(row)

    table = pa.Table.from_pylist(rows) if rows else pa.table({"symbol": pa.array([], pa.string())})
    table = table.replace_schema_metadata(
        {
            b"smm_as_of": as_of.isoformat().encode(),
            b"smm_strategy_version": strategy_version.encode(),
            b"smm_config_hash": config_hash.encode(),
            b"smm_regime": regime.value.encode(),
            # Percentiles only mean something relative to the set they were
            # computed over, so the set is part of the artifact.
            b"smm_ranking_universe": ",".join(cross_section.ranking_universe).encode(),
            b"smm_ranking_universe_size": str(len(cross_section.ranking_universe)).encode(),
            b"smm_excluded_from_ranking": ",".join(
                f"{s}:{r}" for s, r in sorted(cross_section.excluded_from_ranking.items())
            ).encode(),
        }
    )

    target = snapshot_path(root, as_of)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, target, compression="snappy")
    return target


def read_metadata(root: Path | str, as_of: date) -> dict[str, str]:
    """Audit identity of a written snapshot."""
    target = snapshot_path(root, as_of)
    raw = pq.read_schema(target).metadata or {}
    return {
        key.decode().removeprefix("smm_"): value.decode()
        for key, value in raw.items()
        if key.startswith(b"smm_")
    }
