"""Shared type aliases."""

from __future__ import annotations

from datetime import date
from typing import NewType

AsOfDate = NewType("AsOfDate", date)
Symbol = NewType("Symbol", str)
