"""Pure M6 paper-domain seams.

This package deliberately does not contain task orchestration or broker I/O.
"""

from smm.paper.costs import ExecutionQuote, quote_next_open
from smm.paper.prints import SplitAction, SplitActionHistory, rebuild_print_bars

__all__ = [
    "ExecutionQuote",
    "SplitAction",
    "SplitActionHistory",
    "quote_next_open",
    "rebuild_print_bars",
]
