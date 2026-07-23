"""Byte-stability proof for the report's serialization primitives.

Built and proven deterministic before any bucket/writer code exists on top
of it -- a formatter bug discovered at the N-day replay gate (§8) is much
more expensive to trace than one caught here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from smm.core.errors import DataValidationError
from smm.report.format import dump_json_deterministic, format_decimal, format_float


def test_format_float_is_stable_across_repeated_calls() -> None:
    assert format_float(1.0 / 3.0) == format_float(1.0 / 3.0)


def test_format_float_uses_six_decimal_places() -> None:
    assert format_float(1.5) == "1.500000"
    assert format_float(0.1234567) == "0.123457"


def test_format_float_none_stays_none() -> None:
    assert format_float(None) is None


def test_format_float_never_uses_scientific_notation() -> None:
    assert "e" not in format_float(1e-10).lower()
    assert "e" not in format_float(123456789.123).lower()


def test_dump_json_deterministic_sorts_keys_regardless_of_insertion_order() -> None:
    first = dump_json_deterministic({"b": 1, "a": 2, "c": 3})
    second = dump_json_deterministic({"c": 3, "a": 2, "b": 1})
    assert first == second
    assert first.index('"a"') < first.index('"b"') < first.index('"c"')


def test_dump_json_deterministic_uses_compact_fixed_separators() -> None:
    text = dump_json_deterministic({"a": 1, "b": 2})
    assert ", " not in text
    assert ": " not in text


def test_dump_json_deterministic_is_stable_across_calls() -> None:
    payload = {"as_of": "2024-01-02", "count": 3, "nested": {"x": 1.0}}
    assert dump_json_deterministic(payload) == dump_json_deterministic(payload)


def test_dump_json_deterministic_ends_with_a_single_trailing_newline() -> None:
    text = dump_json_deterministic({"a": 1})
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_dump_json_deterministic_accepts_a_top_level_array() -> None:
    assert dump_json_deterministic([{"b": 1, "a": 2}]) == '[{"a":2,"b":1}]\n'


def test_format_decimal_uses_fixed_six_places_without_float_conversion() -> None:
    assert format_decimal(Decimal("0.1234567")) == "0.123457"
    assert format_decimal(Decimal("100.1")) == "100.100000"


def test_format_decimal_rejects_nonfinite_values() -> None:
    with pytest.raises(DataValidationError, match="Decimal must be finite"):
        format_decimal(Decimal("NaN"))
