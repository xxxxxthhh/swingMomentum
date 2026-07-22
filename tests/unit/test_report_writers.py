"""Byte-determinism of the CSV/Markdown renderers (M4 ADR §6/§8)."""

from __future__ import annotations

from datetime import date

from smm.domain.enums import MarketRegime, SignalState
from smm.report.csv_writer import FIELDS, render_csv
from smm.report.markdown_writer import render_markdown
from smm.report.rows import BUCKET_WATCHLIST, ReportRow

_AS_OF = date(2024, 6, 10)


def _row(**overrides) -> ReportRow:
    base = dict(
        as_of=_AS_OF,
        bucket=BUCKET_WATCHLIST,
        symbol="NVDA",
        signal_id="NVDA|sig",
        state=SignalState.WATCHLISTED,
        watchlist_entry=_AS_OF,
        from_state=None,
        to_state=None,
        reason_codes=(),
        close=123.456789,
        breakout_level=120.0,
        relative_volume=1.5,
        extension_atr=0.8,
        momentum_score=85.0,
        relative_strength_score=72.3,
        regime=MarketRegime.RISK_ON,
        strategy_version="SMM-V1.0.0",
        config_hash="abc123",
    )
    base.update(overrides)
    return ReportRow(**base)


def test_render_csv_header_is_present_even_for_zero_rows() -> None:
    text = render_csv([])
    lines = text.strip("\n").split("\n")
    assert len(lines) == 1
    assert lines[0].split(",") == list(FIELDS)


def test_render_csv_is_byte_stable_across_calls() -> None:
    rows = [_row(symbol="AAA"), _row(symbol="BBB")]
    assert render_csv(rows) == render_csv(rows)


def test_render_csv_formats_floats_to_fixed_precision() -> None:
    text = render_csv([_row(close=123.456789)])
    assert "123.456789" in text


def test_render_csv_uses_explicit_blank_for_none_fields() -> None:
    text = render_csv([_row(from_state=None, to_state=None, close=None)])
    body = text.strip("\n").split("\n")[1]
    cells = body.split(",")
    from_state_index = FIELDS.index("from_state")
    close_index = FIELDS.index("close")
    assert cells[from_state_index] == ""
    assert cells[close_index] == ""


def test_render_csv_comma_joins_reason_codes() -> None:
    text = render_csv([_row(reason_codes=("hard_filters_passed", "breakout_not_confirmed"))])
    assert "hard_filters_passed,breakout_not_confirmed" in text


def test_render_markdown_states_an_explicit_zero_count_per_section() -> None:
    text = render_markdown(
        [],
        as_of=_AS_OF,
        strategy_version="SMM-V1.0.0",
        config_hash="abc123",
        regime=MarketRegime.RISK_ON,
    )
    assert "new_trigger" in text and "(0)" in text
    assert "open_trigger" in text
    assert "watchlist" in text
    assert "terminal_change" in text
    assert text.count("(0)") == 4


def test_render_markdown_is_byte_stable_across_calls() -> None:
    rows = [_row(symbol="AAA"), _row(symbol="BBB")]
    kwargs = dict(
        as_of=_AS_OF,
        strategy_version="SMM-V1.0.0",
        config_hash="abc123",
        regime=MarketRegime.RISK_ON,
    )
    assert render_markdown(rows, **kwargs) == render_markdown(rows, **kwargs)


def test_render_markdown_places_rows_in_their_own_bucket_section() -> None:
    from smm.report.rows import BUCKET_NEW_TRIGGER

    new_trigger_row = _row(
        symbol="ZZZ",
        bucket=BUCKET_NEW_TRIGGER,
        from_state=SignalState.DETECTED,
        to_state=SignalState.TRIGGERED,
    )
    text = render_markdown(
        [new_trigger_row],
        as_of=_AS_OF,
        strategy_version="SMM-V1.0.0",
        config_hash="abc123",
        regime=MarketRegime.RISK_ON,
    )
    new_trigger_section = text.split("## open_trigger")[0]
    assert "ZZZ" in new_trigger_section
    watchlist_section = text.split("## watchlist")[1].split("## terminal_change")[0]
    assert "ZZZ" not in watchlist_section
