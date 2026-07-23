"""Pure M6 paper-domain seams.

This package deliberately does not contain task orchestration or broker I/O.
"""

from smm.paper.circuits import CircuitInputs, CircuitState, evaluate_circuit_state
from smm.paper.costs import ExecutionQuote, quote_next_open
from smm.paper.entries import EntryAssessment, EntryStatus, assess_next_open_entry
from smm.paper.excursions import PositionExcursionState, update_position_excursion
from smm.paper.exits import (
    CloseExitAssessment,
    CloseExitStatus,
    assess_close_exit,
)
from smm.paper.manual_decisions import (
    ManualDecision,
    ManualDecisionType,
    ManualSkipRequest,
    append_manual_skips,
    manual_decision_path,
    read_manual_decisions,
)
from smm.paper.orders import (
    PaperOrder,
    PaperOrderPurpose,
    PaperOrderStatus,
    append_paper_orders,
    paper_order_path,
    read_paper_orders,
)
from smm.paper.prints import SplitAction, SplitActionHistory, rebuild_print_bars
from smm.paper.rebases import (
    PaperPositionCorporateAction,
    PositionSplitRebase,
    rebase_open_position_for_split,
)
from smm.paper.stops import (
    OpenPaperPosition,
    StopAssessmentStatus,
    StopExitAssessment,
    assess_long_stop,
)

__all__ = [
    "CircuitInputs",
    "CircuitState",
    "CloseExitAssessment",
    "CloseExitStatus",
    "EntryAssessment",
    "EntryStatus",
    "ExecutionQuote",
    "ManualDecision",
    "ManualDecisionType",
    "ManualSkipRequest",
    "OpenPaperPosition",
    "PaperOrder",
    "PaperOrderPurpose",
    "PaperOrderStatus",
    "PaperPositionCorporateAction",
    "PositionExcursionState",
    "PositionSplitRebase",
    "SplitAction",
    "SplitActionHistory",
    "StopAssessmentStatus",
    "StopExitAssessment",
    "assess_close_exit",
    "assess_next_open_entry",
    "assess_long_stop",
    "append_manual_skips",
    "append_paper_orders",
    "evaluate_circuit_state",
    "manual_decision_path",
    "quote_next_open",
    "read_manual_decisions",
    "paper_order_path",
    "read_paper_orders",
    "rebase_open_position_for_split",
    "rebuild_print_bars",
    "update_position_excursion",
]
