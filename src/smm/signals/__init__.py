"""Signal lifecycle and append-only transition persistence."""

from smm.signals.lifecycle import SignalTransition, current_states
from smm.signals.store import (
    BatchSeal,
    append_transitions,
    assert_session_continuity,
    latest_sealed_as_of,
    read_batch_seals,
    read_transitions,
)

__all__ = [
    "BatchSeal",
    "SignalTransition",
    "append_transitions",
    "assert_session_continuity",
    "current_states",
    "latest_sealed_as_of",
    "read_batch_seals",
    "read_transitions",
]
