"""Append-only M6 paper-order facts and idempotent Parquet persistence.

This module records planned or cancelled paper orders only. It does not create
fills, positions, trades, cash mutations, manual decisions, or task orchestration.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from smm.core.errors import DataValidationError


class PaperOrderPurpose(StrEnum):
    """The two M6 order intents that a later fill seam may consume."""

    ENTRY = "entry"
    EXIT = "exit"


class PaperOrderStatus(StrEnum):
    """Unfilled terminal or pending states for this ledger-only slice."""

    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


class PaperOrder(BaseModel):
    """Immutable paper-order fact keyed by the accepted ADR business key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: str
    symbol: str
    purpose: PaperOrderPurpose
    as_of: date
    scheduled_session: date
    planned_quantity: int = Field(gt=0)
    actual_quantity: int = Field(ge=0)
    status: PaperOrderStatus
    reason_codes: tuple[str, ...] = Field(min_length=1)
    strategy_version: str
    config_hash: str

    @field_validator("signal_id", "symbol", "strategy_version", "config_hash")
    @classmethod
    def identity_fields_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("paper order identity fields must be non-empty")
        return value

    @field_validator("reason_codes")
    @classmethod
    def reason_codes_are_unique_and_nonempty(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(not code.strip() for code in value) or len(set(value)) != len(value):
            raise ValueError("paper order reason codes must be unique and non-empty")
        return value

    @model_validator(mode="after")
    def preserves_unfilled_order_contract(self) -> PaperOrder:
        if self.as_of > self.scheduled_session:
            raise ValueError("paper order as_of cannot follow scheduled_session")
        if self.actual_quantity != 0:
            raise ValueError("unfilled paper order must have zero actual quantity")
        return self

    @property
    def business_key(self) -> tuple[str, PaperOrderPurpose, date, str]:
        """ADR §6 uniqueness boundary; exact duplicates are replay no-ops."""

        return (
            self.signal_id,
            self.purpose,
            self.scheduled_session,
            self.config_hash,
        )


_SCHEMA = pa.schema(
    [
        ("signal_id", pa.string()),
        ("symbol", pa.string()),
        ("purpose", pa.string()),
        ("as_of", pa.date32()),
        ("scheduled_session", pa.date32()),
        ("planned_quantity", pa.int64()),
        ("actual_quantity", pa.int64()),
        ("status", pa.string()),
        ("reason_codes", pa.list_(pa.string())),
        ("strategy_version", pa.string()),
        ("config_hash", pa.string()),
    ]
)
_ORDER_FIELDS = tuple(PaperOrder.model_fields)


def paper_order_path(root: Path | str) -> Path:
    """Return the only storage location owned by this paper-order slice."""

    return Path(root) / "paper_orders.parquet"


def read_paper_orders(root: Path | str) -> list[PaperOrder]:
    """Read an append-only order ledger, rejecting malformed or duplicate rows."""

    target = paper_order_path(root)
    if not target.exists():
        return []
    try:
        table = pq.read_table(target)
    except Exception as exc:  # pyarrow errors vary by malformed-file shape.
        raise DataValidationError("cannot read paper order ledger") from exc
    if table.schema != _SCHEMA:
        raise DataValidationError("unexpected paper order ledger schema")
    raw_orders = table.to_pylist()

    orders: list[PaperOrder] = []
    seen_keys: set[tuple[str, PaperOrderPurpose, date, str]] = set()
    for raw in raw_orders:
        try:
            order = PaperOrder(**{name: raw.get(name) for name in _ORDER_FIELDS})
        except (TypeError, ValidationError) as exc:
            raise DataValidationError("invalid paper order ledger record") from exc
        if order.business_key in seen_keys:
            raise DataValidationError("duplicate paper order business key in ledger")
        seen_keys.add(order.business_key)
        orders.append(order)
    return sorted(orders, key=_order_sort_key)


def append_paper_orders(root: Path | str, orders: Sequence[PaperOrder]) -> Path:
    """Atomically append unique paper orders; exact replays are no-ops.

    The store does not overwrite an existing fact. A caller who presents the
    same ADR business key with a different payload receives a fail-closed
    validation error instead of an implicit status update.
    """

    target = paper_order_path(root)
    incoming = _deduplicate_batch(orders)
    existing = read_paper_orders(root)
    existing_by_key = {order.business_key: order for order in existing}

    new_orders: list[PaperOrder] = []
    for order in incoming:
        previous = existing_by_key.get(order.business_key)
        if previous is None:
            new_orders.append(order)
            continue
        if previous != order:
            raise DataValidationError("conflicting paper order business key")

    if new_orders:
        _write_paper_orders(target, [*existing, *new_orders])
    return target


def _deduplicate_batch(orders: Sequence[PaperOrder]) -> list[PaperOrder]:
    by_key: dict[tuple[str, PaperOrderPurpose, date, str], PaperOrder] = {}
    for order in orders:
        if not isinstance(order, PaperOrder):
            raise DataValidationError("paper order append requires PaperOrder records")
        previous = by_key.get(order.business_key)
        if previous is not None and previous != order:
            raise DataValidationError("conflicting paper order business key")
        by_key[order.business_key] = order
    return sorted(by_key.values(), key=_order_sort_key)


def _write_paper_orders(target: Path, orders: Sequence[PaperOrder]) -> None:
    records = [_order_row(order) for order in sorted(orders, key=_order_sort_key)]
    table = pa.Table.from_pylist(records, schema=_SCHEMA)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        pq.write_table(table, temporary, compression="snappy")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _order_row(order: PaperOrder) -> dict[str, object]:
    values = order.model_dump()
    values["purpose"] = order.purpose.value
    values["status"] = order.status.value
    values["reason_codes"] = list(order.reason_codes)
    return values


def _order_sort_key(order: PaperOrder) -> tuple[date, str, str, str]:
    return (
        order.scheduled_session,
        order.signal_id,
        order.purpose.value,
        order.config_hash,
    )
