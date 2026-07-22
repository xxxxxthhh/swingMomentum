"""Pure M6 paper-domain seams.

This package deliberately does not contain task orchestration or broker I/O.
"""

from smm.paper.circuits import CircuitInputs, CircuitState, evaluate_circuit_state
from smm.paper.costs import ExecutionQuote, quote_next_open
from smm.paper.entries import EntryAssessment, EntryStatus, assess_next_open_entry
from smm.paper.exits import (
    CloseExitAssessment,
    CloseExitStatus,
    assess_close_exit,
)
from smm.paper.prints import SplitAction, SplitActionHistory, rebuild_print_bars
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
    "OpenPaperPosition",
    "SplitAction",
    "SplitActionHistory",
    "StopAssessmentStatus",
    "StopExitAssessment",
    "assess_close_exit",
    "assess_next_open_entry",
    "assess_long_stop",
    "evaluate_circuit_state",
    "quote_next_open",
    "rebuild_print_bars",
]
