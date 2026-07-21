"""Error hierarchy. Fail-closed paths should raise subclasses of FailClosedError."""

from __future__ import annotations


class SmmError(Exception):
    """Base error for the SMM package."""


class FailClosedError(SmmError):
    """Raised when the system must stop producing executable actions."""


class ConfigError(FailClosedError):
    """Invalid or missing strategy configuration."""


class DataValidationError(FailClosedError):
    """Market data failed quality checks."""


class DomainError(SmmError):
    """Domain invariant violated (not always fail-closed for the whole run)."""


class StateTransitionError(DomainError):
    """Illegal signal or position state transition."""
