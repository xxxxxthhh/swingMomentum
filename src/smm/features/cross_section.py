"""Cross-sectional ranking and relative strength (ADR 2026-07-22 R1, §1–§2).

The ranking universe (ADR R1) excludes three groups, each for a different
reason:

- **Benchmarks** (SPY, sector ETFs). Their returns are needed to compute
  relative strength, so they are *computed*, but ranking an ETF against its own
  members compares different kinds of thing.
- **Symbols short of history.** Their features cannot be computed, and a symbol
  with no value must not occupy a percentile slot — that would shift every other
  symbol's score.
- **Symbols that failed validation.** Their data is not trustworthy.

Ranking is over the pool **before** hard filters (constitution §17.2). Ranking
survivors instead would let every score drift as the filters are tuned.

Missing propagates and is never renormalised (ADR §2). If ``RS_Sector`` is
absent, the whole ``RelativeStrengthScore`` is absent and the symbol drops with
a reason code. Rescaling the surviving 0.40/0.40 weights to 0.50/0.50 would put
two differently-scoped scores into one ranking table — that corrupts the entire
ranking, not just the row that was missing data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from smm.config.schema import StrategyConfig
from smm.features.engine import SymbolFeatures
from smm.features.rolling import percentile_ranks, weighted_score

REASON_RS_SECTOR_MISSING = "rs_sector_missing"
REASON_NO_SECTOR = "no_sector"
REASON_SECTOR_BENCHMARK_MISSING = "sector_benchmark_missing"
REASON_BENCHMARK_MISSING = "benchmark_missing"


@dataclass(frozen=True, slots=True)
class ScoredSymbol:
    """One symbol's cross-sectional scores. Any score may be ``None``."""

    symbol: str
    sector: str | None
    rs_spy_short: float | None
    rs_spy_long: float | None
    rs_sector: float | None
    momentum_score: float | None
    relative_strength_score: float | None
    reason_codes: list[str] = field(default_factory=list)

    @property
    def is_scored(self) -> bool:
        return self.momentum_score is not None and self.relative_strength_score is not None


@dataclass(frozen=True, slots=True)
class CrossSection:
    """The whole as-of cross-section, plus how its universe was defined."""

    as_of: date
    scored: dict[str, ScoredSymbol]
    ranking_universe: tuple[str, ...]
    excluded_from_ranking: dict[str, str]

    @property
    def candidates(self) -> list[ScoredSymbol]:
        return [s for s in self.scored.values() if s.is_scored]


def _relative(
    subject: SymbolFeatures, benchmark: SymbolFeatures | None, window: int
) -> float | None:
    """``return(subject) - return(benchmark)`` (constitution §18.1–§18.2)."""
    if benchmark is None:
        return None
    mine, theirs = subject.returns.get(window), benchmark.returns.get(window)
    if mine is None or theirs is None:
        return None
    return mine - theirs


def build_cross_section(
    features: dict[str, SymbolFeatures],
    *,
    as_of: date,
    sectors: dict[str, str],
    config: StrategyConfig,
    excluded: dict[str, str] | None = None,
) -> CrossSection:
    """Score every rankable symbol at ``as_of``.

    ``features`` must include the benchmark and any sector ETFs — their returns
    are the reference for relative strength — but they are dropped before
    ranking.
    """
    momentum_cfg = config.momentum
    rs_cfg = config.relative_strength
    short_window, long_window = config.features.return_windows[1:3]
    fast_window = config.features.return_windows[0]

    benchmark_symbol = config.market_regime.benchmark.upper()
    sector_etfs = {etf.upper() for etf in config.sector_benchmarks.values()}
    benchmarks = {benchmark_symbol} | sector_etfs

    market = features.get(benchmark_symbol)
    universe = tuple(sorted(s for s in features if s not in benchmarks))

    not_ranked: dict[str, str] = dict(excluded or {})
    for symbol in benchmarks & set(features):
        not_ranked[symbol] = "benchmark"

    # Raw component values, keyed by symbol. Only present values are ranked:
    # a missing input occupying a slot would shift everyone else's percentile.
    raw: dict[str, dict[str, float]] = {}
    context: dict[str, ScoredSymbol] = {}

    for symbol in universe:
        subject = features[symbol]
        sector = sectors.get(symbol) or None
        etf_symbol = config.sector_benchmarks.get(sector or "", "").upper()
        sector_features = features.get(etf_symbol) if etf_symbol else None

        reasons: list[str] = []
        if market is None:
            reasons.append(REASON_BENCHMARK_MISSING)
        if sector is None:
            reasons.append(REASON_NO_SECTOR)
        elif sector_features is None:
            reasons.append(REASON_SECTOR_BENCHMARK_MISSING)

        rs_short = _relative(subject, market, short_window)
        rs_long = _relative(subject, market, long_window)
        rs_sector = _relative(subject, sector_features, short_window)
        if rs_sector is None:
            reasons.append(REASON_RS_SECTOR_MISSING)

        values: dict[str, float] = {}
        for name, value in (
            (f"return_{fast_window}", subject.returns.get(fast_window)),
            (f"return_{short_window}", subject.returns.get(short_window)),
            (f"return_{long_window}", subject.returns.get(long_window)),
            ("rs_spy_short", rs_short),
            ("rs_spy_long", rs_long),
            ("rs_sector", rs_sector),
        ):
            if value is not None:
                values[name] = value
        raw[symbol] = values
        context[symbol] = ScoredSymbol(
            symbol=symbol,
            sector=sector,
            rs_spy_short=rs_short,
            rs_spy_long=rs_long,
            rs_sector=rs_sector,
            momentum_score=None,
            relative_strength_score=None,
            reason_codes=reasons,
        )

    component_names = [
        f"return_{fast_window}",
        f"return_{short_window}",
        f"return_{long_window}",
        "rs_spy_short",
        "rs_spy_long",
        "rs_sector",
    ]
    ranks: dict[str, dict[str, float]] = {
        name: percentile_ranks(
            {s: values[name] for s, values in raw.items() if name in values}
        )
        for name in component_names
    }

    scored: dict[str, ScoredSymbol] = {}
    for symbol, partial in context.items():
        symbol_ranks: dict[str, float | None] = {
            name: ranks[name].get(symbol) for name in component_names
        }
        momentum = weighted_score(
            symbol_ranks,
            {
                f"return_{fast_window}": momentum_cfg.return_21_weight,
                f"return_{short_window}": momentum_cfg.return_63_weight,
                f"return_{long_window}": momentum_cfg.return_126_weight,
            },
        )
        relative = weighted_score(
            symbol_ranks,
            {
                "rs_spy_short": rs_cfg.rs_spy_63_weight,
                "rs_spy_long": rs_cfg.rs_spy_126_weight,
                "rs_sector": rs_cfg.rs_sector_63_weight,
            },
        )
        scored[symbol] = ScoredSymbol(
            symbol=symbol,
            sector=partial.sector,
            rs_spy_short=partial.rs_spy_short,
            rs_spy_long=partial.rs_spy_long,
            rs_sector=partial.rs_sector,
            momentum_score=momentum,
            relative_strength_score=relative,
            reason_codes=partial.reason_codes,
        )

    return CrossSection(
        as_of=as_of,
        scored=scored,
        ranking_universe=universe,
        excluded_from_ranking=not_ranked,
    )
