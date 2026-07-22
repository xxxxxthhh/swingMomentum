"""Market regime from the benchmark (constitution §14, ADR R2).

The three states are a **total partition** — every input lands in exactly one,
with no gap and no overlap. Risk-Off and Risk-On are strict predicates and
Neutral is their explicit complement, rather than a third independent test that
could leave a case unclassified or claim two at once.

``close == SMA200`` exactly is therefore **Neutral**: it is not below the slow
average, so not Risk-Off, and not above it, so not Risk-On.

There is no fourth "unknown" state. Missing benchmark data raises, because ADR
R2 requires the whole run to fail rather than emit a regime nobody can act on
— and displaying missing as Risk-On, or quietly continuing as Neutral, is the
specific failure this rule exists to prevent.
"""

from __future__ import annotations

from smm.core.errors import DataValidationError
from smm.domain.enums import MarketRegime
from smm.features.engine import ExcludedSymbol, SymbolFeatures


def classify_regime(benchmark: SymbolFeatures) -> MarketRegime:
    """Risk-On / Neutral / Risk-Off for one benchmark feature vector."""
    close, fast, slow = benchmark.close, benchmark.sma_fast, benchmark.sma_slow
    if fast is None or slow is None:
        raise DataValidationError(
            f"{benchmark.symbol}: moving averages unavailable, cannot classify regime"
        )

    if close < slow:
        return MarketRegime.RISK_OFF
    if close > fast and close > slow and fast > slow:
        return MarketRegime.RISK_ON
    return MarketRegime.NEUTRAL


def resolve_regime(
    features: dict[str, SymbolFeatures],
    *,
    benchmark_symbol: str,
    excluded: dict[str, ExcludedSymbol] | None = None,
) -> MarketRegime:
    """Classify the regime, or fail the run (ADR R2).

    Failing is not a severity choice. Without the benchmark, relative strength
    is missing for *every* symbol, so the candidate set is empty by
    construction — a report that looks normal but can never contain a candidate
    is a misreading risk, not a useful artifact.
    """
    symbol = benchmark_symbol.upper()
    benchmark = features.get(symbol)
    if benchmark is None:
        detail = ""
        if excluded and symbol in excluded:
            entry = excluded[symbol]
            detail = f" ({entry.reason}, {entry.bar_count} bars)"
        raise DataValidationError(
            f"benchmark {symbol} has no features{detail} — cannot determine market "
            f"regime, and every relative-strength score depends on it"
        )
    return classify_regime(benchmark)
