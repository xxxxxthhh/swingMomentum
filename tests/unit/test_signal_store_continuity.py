"""M4 ADR §2: read-only batch metadata and the session continuity gate.

Sealed-empty days must be a first-class processed state -- these tests
build seals with zero transitions throughout, on purpose, to prove nothing
here is inferring "processed" from row presence.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from smm.core.errors import DataValidationError
from smm.signals.store import (
    append_transitions,
    assert_session_continuity,
    latest_sealed_as_of,
    read_batch_seals,
)

_VERSION = "SMM-V1.0.0"
_HASH = "deadbeef"


def _weekdays(start: date, count: int) -> list[date]:
    sessions: list[date] = []
    cursor = start
    while len(sessions) < count:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return sessions


def _seal(root, as_of: date) -> None:
    append_transitions(root, [], as_of=as_of, strategy_version=_VERSION, config_hash=_HASH)


def test_latest_sealed_as_of_is_none_for_an_empty_store(tmp_path) -> None:
    assert latest_sealed_as_of(tmp_path) is None
    assert read_batch_seals(tmp_path) == {}


def test_sealed_empty_day_is_recorded_with_zero_transitions(tmp_path) -> None:
    sessions = _weekdays(date(2024, 1, 2), 5)
    _seal(tmp_path, sessions[0])

    seals = read_batch_seals(tmp_path)
    assert seals[sessions[0]].transition_count == 0
    assert latest_sealed_as_of(tmp_path) == sessions[0]


def test_no_seal_allows_any_valid_session(tmp_path) -> None:
    sessions = _weekdays(date(2024, 1, 2), 5)
    for as_of in sessions:
        assert_session_continuity(tmp_path, as_of=as_of, sessions=sessions)


def test_exact_rerun_of_latest_seal_is_allowed(tmp_path) -> None:
    sessions = _weekdays(date(2024, 1, 2), 5)
    _seal(tmp_path, sessions[2])
    assert_session_continuity(tmp_path, as_of=sessions[2], sessions=sessions)


def test_next_provider_session_after_latest_seal_is_allowed(tmp_path) -> None:
    sessions = _weekdays(date(2024, 1, 2), 5)
    _seal(tmp_path, sessions[2])
    assert_session_continuity(tmp_path, as_of=sessions[3], sessions=sessions)


def test_backfill_before_latest_seal_fails_closed(tmp_path) -> None:
    sessions = _weekdays(date(2024, 1, 2), 5)
    _seal(tmp_path, sessions[2])
    with pytest.raises(DataValidationError, match="backfill"):
        assert_session_continuity(tmp_path, as_of=sessions[1], sessions=sessions)


def test_skipping_a_session_fails_closed(tmp_path) -> None:
    sessions = _weekdays(date(2024, 1, 2), 6)
    _seal(tmp_path, sessions[2])
    with pytest.raises(DataValidationError, match="skips"):
        assert_session_continuity(tmp_path, as_of=sessions[4], sessions=sessions)


def test_sealed_empty_day_still_gates_continuity(tmp_path) -> None:
    """The point of §2: an empty seal is not invisible to the gate."""
    sessions = _weekdays(date(2024, 1, 2), 5)
    _seal(tmp_path, sessions[2])  # zero transitions -- still a real seal

    # Re-requesting the sealed-empty day, or the day right after it, is fine.
    assert_session_continuity(tmp_path, as_of=sessions[2], sessions=sessions)
    assert_session_continuity(tmp_path, as_of=sessions[3], sessions=sessions)
    # Jumping past it or behind it is not.
    with pytest.raises(DataValidationError, match="skips"):
        assert_session_continuity(tmp_path, as_of=sessions[4], sessions=sessions)
    with pytest.raises(DataValidationError, match="backfill"):
        assert_session_continuity(tmp_path, as_of=sessions[0], sessions=sessions)


def test_as_of_must_be_a_provider_session(tmp_path) -> None:
    sessions = _weekdays(date(2024, 1, 2), 5)
    outside_calendar = sessions[-1] + timedelta(days=30)
    with pytest.raises(DataValidationError, match="not a provider session"):
        assert_session_continuity(tmp_path, as_of=outside_calendar, sessions=sessions)


def test_calendar_must_cover_the_latest_seal(tmp_path) -> None:
    full = _weekdays(date(2024, 1, 2), 6)
    _seal(tmp_path, full[2])
    truncated = full[3:]  # does not include the sealed date
    with pytest.raises(DataValidationError, match="does not cover"):
        assert_session_continuity(tmp_path, as_of=full[4], sessions=truncated)


def test_calendar_must_be_sorted_and_unique(tmp_path) -> None:
    sessions = _weekdays(date(2024, 1, 2), 5)
    with pytest.raises(DataValidationError, match="sorted with unique sessions"):
        assert_session_continuity(tmp_path, as_of=sessions[0], sessions=[*sessions, sessions[0]])


def test_no_session_follows_the_latest_seal_fails_closed(tmp_path) -> None:
    sessions = _weekdays(date(2024, 1, 2), 3)
    _seal(tmp_path, sessions[-1])  # sealed date is the calendar's last session
    with pytest.raises(DataValidationError, match="no provider session follows"):
        assert_session_continuity(tmp_path, as_of=sessions[0], sessions=sessions)
