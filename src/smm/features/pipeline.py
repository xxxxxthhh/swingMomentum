"""M2 orchestration: bars in, scored cross-section out.

Deliberately provider-agnostic — it takes a :class:`~smm.data.protocol.DataProvider`
and a sector map, so the offline synthetic path and the market path run the same
code. A separate "test-only" pipeline would prove nothing about the real one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from smm.config.loader import LoadedConfig
from smm.domain.enums import MarketRegime
from smm.features.cross_section import CrossSection, build_cross_section
from smm.features.engine import ExcludedSymbol, SymbolFeatures, compute_features
from smm.features.regime import resolve_regime


@dataclass(frozen=True, slots=True)
class FeatureRun:
    as_of: date
    regime: MarketRegime
    features: dict[str, SymbolFeatures]
    excluded: dict[str, ExcludedSymbol]
    cross_section: CrossSection


def _lookback_start(as_of: date, bars_needed: int) -> date:
    """Calendar days to cover ``bars_needed`` sessions, with slack for holidays."""
    return as_of - timedelta(days=int(bars_needed * 1.6) + 30)


def run_features(
    provider,
    *,
    as_of: date,
    symbols: Sequence[str],
    sectors: dict[str, str],
    loaded: LoadedConfig,
) -> FeatureRun:
    """Compute features, regime and the cross-section for ``as_of``.

    Benchmarks are fetched alongside the members because relative strength is
    measured against them; they are excluded at the ranking step, not here.
    """
    config = loaded.config
    benchmark = config.market_regime.benchmark.upper()
    sector_etfs = sorted({etf.upper() for etf in config.sector_benchmarks.values()})
    wanted = list(dict.fromkeys([benchmark, *sector_etfs, *(s.upper() for s in symbols)]))

    start = _lookback_start(as_of, config.features.min_history_bars)
    features: dict[str, SymbolFeatures] = {}
    excluded: dict[str, ExcludedSymbol] = {}
    for symbol in wanted:
        bars = provider.get_daily_bars(symbol, start, as_of)
        result = compute_features(bars, as_of=as_of, cfg=config.features)
        if isinstance(result, SymbolFeatures):
            features[symbol] = result
        else:
            # Preserve the symbol name: the engine cannot know it when the
            # series is empty.
            excluded[symbol] = ExcludedSymbol(
                symbol=symbol,
                as_of=as_of,
                reason=result.reason,
                bar_count=result.bar_count,
            )

    # Raises when the benchmark is unusable (ADR R2) — before any scoring, so a
    # partial artifact is never produced.
    regime = resolve_regime(features, benchmark_symbol=benchmark, excluded=excluded)

    cross_section = build_cross_section(
        features,
        as_of=as_of,
        sectors=sectors,
        config=config,
        excluded={s: e.reason for s, e in excluded.items()},
    )
    return FeatureRun(
        as_of=as_of,
        regime=regime,
        features=features,
        excluded=excluded,
        cross_section=cross_section,
    )
