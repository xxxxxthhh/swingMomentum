"""Pure M6 next-open execution-cost quote contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from smm.config.loader import load_config
from smm.core.errors import DataValidationError
from smm.domain.enums import OrderSide
from smm.domain.models import Bar, PrintBar
from smm.domain.views import TradeableBar
from smm.paper.costs import quote_next_open

REPO = Path(__file__).resolve().parents[2]
M6_CONFIG = load_config(REPO / "configs" / "smm_v1_1_0.yaml").config
V1_CONFIG = load_config(REPO / "configs" / "smm_v1_0_0.yaml").config
AS_OF = date(2024, 6, 17)
CONFIG_HASH = "a" * 64


def bar(**updates: object) -> TradeableBar:
    values: dict[str, object] = {
        "symbol": "NVDA",
        "date": AS_OF,
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 1_000_000.0,
    }
    values.update(updates)
    return TradeableBar(**values)


def quote(*, side: OrderSide, **updates: object):
    values: dict[str, object] = {
        "bar": bar(),
        "side": side,
        "execution": M6_CONFIG.execution,
        "strategy_version": M6_CONFIG.strategy.version,
        "config_hash": CONFIG_HASH,
    }
    values.update(updates)
    return quote_next_open(**values)


def test_buy_open_quote_uses_frozen_spread_slippage_and_commission() -> None:
    result = quote(side=OrderSide.BUY)

    assert result.symbol == "NVDA"
    assert result.as_of == AS_OF
    assert result.strategy_version == "SMM-V1.1.0"
    assert result.config_hash == CONFIG_HASH
    assert result.base_price == Decimal("100.0")
    assert result.half_spread_bps == Decimal("2.5")
    assert result.slippage_bps == Decimal("5.0")
    assert result.fill_price == Decimal("100.07500")
    assert result.commission_per_share == Decimal("0.005")
    assert result.cash_per_share == Decimal("100.08000")


def test_sell_open_quote_uses_exit_slippage_and_nets_commission_from_proceeds() -> None:
    result = quote(side=OrderSide.SELL)

    assert result.base_price == Decimal("100.0")
    assert result.half_spread_bps == Decimal("2.5")
    assert result.slippage_bps == Decimal("5.0")
    assert result.fill_price == Decimal("99.92500")
    assert result.commission_per_share == Decimal("0.005")
    assert result.cash_per_share == Decimal("99.92000")


def test_quote_rejects_provider_or_print_bar_without_tradeable_projection() -> None:
    print_bar = PrintBar(
        symbol="NVDA",
        date=AS_OF,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1_000_000.0,
    )
    provider_bar = Bar(
        symbol="NVDA",
        date=AS_OF,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1_000_000.0,
        adj_close=101.0,
        adj_factor=1.0,
    )

    for non_tradeable_bar in (print_bar, provider_bar):
        with pytest.raises(DataValidationError, match="TradeableBar"):
            quote(side=OrderSide.BUY, bar=non_tradeable_bar)


def test_quote_rejects_non_order_side() -> None:
    with pytest.raises(DataValidationError, match="OrderSide"):
        quote(side="buy")


def test_quote_rejects_non_execution_section() -> None:
    with pytest.raises(DataValidationError, match="ExecutionSection"):
        quote(side=OrderSide.BUY, execution=None)


@pytest.mark.parametrize(
    "field",
    [
        "half_spread_bps",
        "entry_slippage_bps",
        "exit_slippage_bps",
        "commission_per_share",
    ],
)
def test_quote_rejects_missing_m6_cost_parameters(field: str) -> None:
    execution = M6_CONFIG.execution.model_copy(update={field: None})

    with pytest.raises(DataValidationError, match="missing frozen M6 execution cost"):
        quote(side=OrderSide.BUY, execution=execution)


def test_quote_rejects_v1_0_execution_config_without_m6_costs() -> None:
    with pytest.raises(DataValidationError, match="missing frozen M6 execution cost"):
        quote(side=OrderSide.BUY, execution=V1_CONFIG.execution)


@pytest.mark.parametrize(
    ("side", "field"),
    [
        (OrderSide.BUY, "entry_slippage_bps"),
        (OrderSide.SELL, "exit_slippage_bps"),
    ],
)
def test_quote_rejects_non_finite_cost_components(side: OrderSide, field: str) -> None:
    execution = M6_CONFIG.execution.model_copy(update={field: Decimal("NaN")})

    with pytest.raises(DataValidationError, match="non-finite M6 execution cost"):
        quote(side=side, execution=execution)


def test_quote_rejects_non_finite_tradeable_open() -> None:
    with pytest.raises(DataValidationError, match="non-finite TradeableBar.open"):
        quote(side=OrderSide.BUY, bar=bar(open=float("nan")))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("half_spread_bps", Decimal("0"), "must be positive"),
        ("commission_per_share", Decimal("-0.001"), "must be non-negative"),
    ],
)
def test_quote_rejects_non_positive_or_negative_costs(
    field: str, value: Decimal, message: str
) -> None:
    execution = M6_CONFIG.execution.model_copy(update={field: value})

    with pytest.raises(DataValidationError, match=message):
        quote(side=OrderSide.BUY, execution=execution)


def test_quote_rejects_non_positive_tradeable_open() -> None:
    with pytest.raises(DataValidationError, match="TradeableBar.open must be positive"):
        quote(side=OrderSide.BUY, bar=bar(open=0.0))


def test_quote_rejects_blank_tradeable_symbol() -> None:
    with pytest.raises(DataValidationError, match="TradeableBar symbol"):
        quote(side=OrderSide.BUY, bar=bar(symbol=""))


def test_sell_quote_rejects_non_positive_net_proceeds() -> None:
    execution = M6_CONFIG.execution.model_copy(
        update={"commission_per_share": Decimal("100")}
    )

    with pytest.raises(DataValidationError, match="non-positive net cash per share"):
        quote(side=OrderSide.SELL, execution=execution)


def test_sell_quote_rejects_non_positive_fill_price() -> None:
    execution = M6_CONFIG.execution.model_copy(
        update={"half_spread_bps": Decimal("10000")}
    )

    with pytest.raises(DataValidationError, match="non-positive fill price"):
        quote(side=OrderSide.SELL, execution=execution)


def test_quote_rejects_blank_strategy_identity() -> None:
    with pytest.raises(DataValidationError, match="identity fields"):
        quote(side=OrderSide.BUY, strategy_version="")
