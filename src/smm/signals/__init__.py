"""Signal lifecycle and append-only transition persistence."""

from smm.signals.lifecycle import SignalTransition, current_states
from smm.signals.store import append_transitions, read_transitions

__all__ = ["SignalTransition", "append_transitions", "current_states", "read_transitions"]
