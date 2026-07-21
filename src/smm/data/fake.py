"""In-process FakeProvider loading synthetic OHLCV CSV fixtures (no network)."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from smm.core.errors import DataValidationError
from smm.domain.models import Bar

# tests/fixtures/ohlcv relative to repo root
_DEFAULT_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "ohlcv"


class FakeProvider:
    """Load bars from CSV files under a fixtures directory.

    CSV columns (header required):
    ``symbol,date,open,high,low,close,volume`` with ``date`` as ``YYYY-MM-DD``.
    """

    def __init__(
        self,
        fixtures_dir: Path | str | None = None,
        *,
        universe: list[str] | None = None,
    ) -> None:
        self._dir = Path(fixtures_dir) if fixtures_dir else _DEFAULT_FIXTURES
        self._bars_by_symbol: dict[str, list[Bar]] = {}
        self._load_all()
        if universe is not None:
            self._universe = list(universe)
        else:
            self._universe = sorted(self._bars_by_symbol.keys())

    def _load_all(self) -> None:
        if not self._dir.is_dir():
            raise DataValidationError(f"fixtures directory not found: {self._dir}")
        for path in sorted(self._dir.glob("*.csv")):
            self._load_file(path)

    def _load_file(self, path: Path) -> None:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            required = {"symbol", "date", "open", "high", "low", "close", "volume"}
            if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
                raise DataValidationError(
                    f"{path.name}: CSV must have columns {sorted(required)}"
                )
            for row in reader:
                bar = Bar(
                    symbol=row["symbol"].strip().upper(),
                    date=date.fromisoformat(row["date"].strip()),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
                self._bars_by_symbol.setdefault(bar.symbol, []).append(bar)
        for symbol, bars in self._bars_by_symbol.items():
            bars.sort(key=lambda b: b.date)
            # de-dupe same day last-wins within file load order already sorted
            deduped: list[Bar] = []
            for b in bars:
                if deduped and deduped[-1].date == b.date and deduped[-1].symbol == b.symbol:
                    deduped[-1] = b
                else:
                    deduped.append(b)
            self._bars_by_symbol[symbol] = deduped

    def get_universe(self, as_of: date) -> list[str]:
        del as_of  # fixtures are static
        return list(self._universe)

    def get_daily_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        symbol_u = symbol.upper()
        bars = self._bars_by_symbol.get(symbol_u, [])
        return [b for b in bars if start <= b.date <= end]

    def get_calendar(self, start: date, end: date) -> list[date]:
        dates: set[date] = set()
        for bars in self._bars_by_symbol.values():
            for b in bars:
                if start <= b.date <= end:
                    dates.add(b.date)
        return sorted(dates)
