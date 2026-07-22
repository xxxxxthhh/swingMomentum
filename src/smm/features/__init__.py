"""Feature engine (Plan v1.1 M2)."""

from smm.features.engine import (
    REASON_INSUFFICIENT_HISTORY,
    REASON_NO_BARS_AS_OF,
    ExcludedSymbol,
    SymbolFeatures,
    compute_features,
)
from smm.features.rolling import (
    atr,
    ema,
    highest,
    max_drawdown,
    percentile_ranks,
    slope,
    sma,
    total_return,
    true_range,
    weighted_score,
)

__all__ = [
    "compute_features",
    "SymbolFeatures",
    "ExcludedSymbol",
    "REASON_INSUFFICIENT_HISTORY",
    "REASON_NO_BARS_AS_OF",
    "sma",
    "ema",
    "atr",
    "true_range",
    "total_return",
    "slope",
    "highest",
    "max_drawdown",
    "percentile_ranks",
    "weighted_score",
]
