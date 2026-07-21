"""Domain enumerations."""

from __future__ import annotations

from enum import StrEnum


class SignalState(StrEnum):
    """Signal lifecycle states (Plan v1.1)."""

    DETECTED = "detected"
    WATCHLISTED = "watchlisted"
    TRIGGERED = "triggered"
    ELIGIBLE = "eligible"
    RISK_ACCEPTED = "risk_accepted"
    RISK_REJECTED = "risk_rejected"
    ENTERED = "entered"
    CANCELLED = "cancelled"
    ACTIVE = "active"
    EXITED = "exited"
    STOPPED = "stopped"
    EXPIRED = "expired"


class MarketRegime(StrEnum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class PositionState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class RiskVerdict(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
