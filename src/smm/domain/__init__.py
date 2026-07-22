"""Domain models — core trading objects (not DataFrames)."""

from smm.domain.enums import (
    MarketRegime,
    OrderSide,
    PositionState,
    RiskVerdict,
    SignalState,
)
from smm.domain.identity import make_logical_signal_id, make_setup_key
from smm.domain.models import (
    ALLOWED_SIGNAL_TRANSITIONS,
    Bar,
    OrderPlan,
    Position,
    PrintBar,
    RiskDecision,
    Signal,
    StrategyIdentity,
    Trade,
    assert_signal_transition,
)
from smm.domain.views import AdjustedBar, TradeableBar, to_adjusted, to_tradeable

__all__ = [
    "Bar",
    "PrintBar",
    "AdjustedBar",
    "TradeableBar",
    "to_adjusted",
    "to_tradeable",
    "Signal",
    "OrderPlan",
    "Position",
    "Trade",
    "RiskDecision",
    "StrategyIdentity",
    "SignalState",
    "MarketRegime",
    "OrderSide",
    "PositionState",
    "RiskVerdict",
    "ALLOWED_SIGNAL_TRANSITIONS",
    "assert_signal_transition",
    "make_setup_key",
    "make_logical_signal_id",
]
