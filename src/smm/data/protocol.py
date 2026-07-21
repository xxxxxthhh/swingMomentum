"""DataProvider protocol — swappable market data sources."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol, runtime_checkable

from smm.domain.models import Bar


@runtime_checkable
class DataProvider(Protocol):
    """Minimal market-data surface for Phase 0+."""

    def get_universe(self, as_of: date) -> list[str]:
        """Return tradeable symbols known as of ``as_of``."""
        ...

    def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Bar]:
        """Return daily bars for ``symbol`` in ``[start, end]`` inclusive."""
        ...

    def get_calendar(self, start: date, end: date) -> list[date]:
        """Return trading dates in ``[start, end]`` inclusive (provider-defined)."""
        ...
