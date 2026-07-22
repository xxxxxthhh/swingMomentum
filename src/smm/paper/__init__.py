"""Pure M6 paper-domain seams.

This package deliberately does not contain task orchestration or broker I/O.
"""

from smm.paper.prints import SplitAction, SplitActionHistory, rebuild_print_bars

__all__ = ["SplitAction", "SplitActionHistory", "rebuild_print_bars"]
