"""Append-only M6 paper-order ledger contract."""

from __future__ import annotations

from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from smm.core.errors import DataValidationError
from smm.paper.orders import (
    PaperOrder,
    PaperOrderPurpose,
    PaperOrderStatus,
    append_paper_orders,
    paper_order_path,
    read_paper_orders,
)


def paper_order(**updates: object) -> PaperOrder:
    values: dict[str, object] = {
        "signal_id": "signal-nvda-2024-06-18",
        "symbol": "NVDA",
        "purpose": PaperOrderPurpose.ENTRY,
        "as_of": date(2024, 6, 18),
        "scheduled_session": date(2024, 6, 20),
        "planned_quantity": 10,
        "actual_quantity": 0,
        "status": PaperOrderStatus.SCHEDULED,
        "reason_codes": ("paper_order_entry_scheduled",),
        "strategy_version": "SMM-V1.1.0",
        "config_hash": "frozen-config-hash",
    }
    values.update(updates)
    return PaperOrder(**values)


def test_append_paper_orders_is_idempotent_by_adr_business_key(tmp_path) -> None:
    order = paper_order()

    target = append_paper_orders(tmp_path, [order])
    first_bytes = target.read_bytes()
    second_target = append_paper_orders(tmp_path, [order])

    assert target == paper_order_path(tmp_path)
    assert second_target == target
    assert target.read_bytes() == first_bytes
    assert read_paper_orders(tmp_path) == [order]
    assert order.business_key == (
        "signal-nvda-2024-06-18",
        PaperOrderPurpose.ENTRY,
        date(2024, 6, 20),
        "frozen-config-hash",
    )


def test_same_paper_order_business_key_with_different_payload_fails_closed(tmp_path) -> None:
    original = paper_order()
    append_paper_orders(tmp_path, [original])

    conflicting = paper_order(reason_codes=("paper_entry_gap_exceeds_limit",))

    with pytest.raises(DataValidationError, match="conflicting paper order"):
        append_paper_orders(tmp_path, [conflicting])

    assert read_paper_orders(tmp_path) == [original]


def test_config_hash_is_part_of_the_paper_order_business_key(tmp_path) -> None:
    original = paper_order()
    next_config = paper_order(config_hash="next-frozen-config-hash")

    append_paper_orders(tmp_path, [original, next_config])

    assert read_paper_orders(tmp_path) == [original, next_config]


def test_same_batch_conflict_fails_before_writing_any_order(tmp_path) -> None:
    original = paper_order()
    conflicting = paper_order(planned_quantity=9)

    with pytest.raises(DataValidationError, match="conflicting paper order"):
        append_paper_orders(tmp_path, [original, conflicting])

    assert not paper_order_path(tmp_path).exists()


def test_unknown_ledger_column_fails_closed_instead_of_being_ignored(tmp_path) -> None:
    order = paper_order()
    target = append_paper_orders(tmp_path, [order])
    tampered = pq.read_table(target).append_column("unexpected", pa.array(["value"]))
    pq.write_table(tampered, target)

    with pytest.raises(DataValidationError, match="unexpected paper order ledger schema"):
        read_paper_orders(tmp_path)


@pytest.mark.parametrize(
    ("updates", "match"),
    [
        ({"as_of": date(2024, 6, 21)}, "cannot follow scheduled_session"),
        ({"actual_quantity": 1}, "unfilled paper order must have zero actual quantity"),
        ({"reason_codes": ("duplicate", "duplicate")}, "reason codes must be unique"),
    ],
)
def test_unfilled_paper_orders_reject_invalid_audit_facts(
    updates: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        paper_order(**updates)
