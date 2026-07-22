"""Config load, validation, and hash stability."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from smm.config.loader import compute_config_hash, load_config, load_config_from_mapping
from smm.core.errors import ConfigError

REPO = Path(__file__).resolve().parents[2]
DEFAULT_YAML = REPO / "configs" / "smm_v1_0_0.yaml"
M6_YAML = REPO / "configs" / "smm_v1_1_0.yaml"
V1_0_CONFIG_HASH = "8fe69452ca42d1b8effb7221d6c07ddd067e10739e2338dcd51034e35bb836ef"


def _m6_mapping() -> dict:
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    raw["strategy"]["version"] = "SMM-V1.1.0"
    raw["execution"].update(
        {
            "half_spread_bps": 2.5,
            "entry_slippage_bps": 5.0,
            "exit_slippage_bps": 5.0,
            "commission_per_share": 0.005,
        }
    )
    raw["risk"].update(
        {
            "daily_loss_pause_r": 4,
            "drawdown_reduce_at": 0.06,
            "drawdown_stop_at": 0.10,
        }
    )
    return raw


def test_load_default_config() -> None:
    loaded = load_config(DEFAULT_YAML)
    assert loaded.version == "SMM-V1.0.0"
    assert loaded.config.strategy.auto_live_orders is False
    assert loaded.config.scoring.fund_as_filter is True
    assert loaded.config.scoring.fundamental_weight == 0.0
    assert loaded.config.risk.new_entries_enabled is True
    assert len(loaded.config_hash) == 64
    assert all(c in "0123456789abcdef" for c in loaded.config_hash)


def test_config_hash_stable() -> None:
    a = load_config(DEFAULT_YAML)
    b = load_config(DEFAULT_YAML)
    assert a.config_hash == b.config_hash
    assert compute_config_hash(a.config) == a.config_hash


def test_v1_0_hash_remains_the_frozen_identity() -> None:
    assert load_config(DEFAULT_YAML).config_hash == V1_0_CONFIG_HASH


def test_load_v1_1_config_freezes_m6_cost_and_circuit_values() -> None:
    loaded = load_config(M6_YAML)

    assert loaded.version == "SMM-V1.1.0"
    assert loaded.config.execution.half_spread_bps == Decimal("2.5")
    assert loaded.config.execution.entry_slippage_bps == Decimal("5.0")
    assert loaded.config.execution.exit_slippage_bps == Decimal("5.0")
    assert loaded.config.execution.commission_per_share == Decimal("0.005")
    assert loaded.config.risk.daily_loss_pause_r == Decimal("4")
    assert loaded.config.risk.drawdown_reduce_at == Decimal("0.06")
    assert loaded.config.risk.drawdown_stop_at == Decimal("0.10")


def test_v1_1_m6_values_are_decimals() -> None:
    loaded = load_config(M6_YAML).config
    values = (
        loaded.execution.half_spread_bps,
        loaded.execution.entry_slippage_bps,
        loaded.execution.exit_slippage_bps,
        loaded.execution.commission_per_share,
        loaded.risk.daily_loss_pause_r,
        loaded.risk.drawdown_reduce_at,
        loaded.risk.drawdown_stop_at,
    )
    assert all(isinstance(value, Decimal) for value in values)


def test_v1_1_mapping_accepts_the_reviewed_m6_fields() -> None:
    loaded = load_config_from_mapping(_m6_mapping())
    assert loaded.version == "SMM-V1.1.0"


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("execution", "half_spread_bps"),
        ("execution", "entry_slippage_bps"),
        ("execution", "exit_slippage_bps"),
        ("execution", "commission_per_share"),
        ("risk", "daily_loss_pause_r"),
        ("risk", "drawdown_reduce_at"),
        ("risk", "drawdown_stop_at"),
    ],
)
def test_v1_1_rejects_missing_m6_required_keys(section: str, key: str) -> None:
    raw = _m6_mapping()
    del raw[section][key]

    with pytest.raises(ConfigError, match="SMM-V1.1.0 requires"):
        load_config_from_mapping(raw)


@pytest.mark.parametrize(
    ("section", "key", "value", "message"),
    [
        ("execution", "half_spread_bps", 0.0, "greater than 0"),
        ("execution", "entry_slippage_bps", -0.1, "greater than 0"),
        ("execution", "exit_slippage_bps", 0.0, "greater than 0"),
        ("execution", "commission_per_share", -0.001, "greater than or equal to 0"),
        ("risk", "daily_loss_pause_r", 0, "greater than 0"),
        ("risk", "drawdown_reduce_at", 0.0, "greater than 0"),
        ("risk", "drawdown_stop_at", 1.0, "less than 1"),
    ],
)
def test_v1_1_rejects_illegal_m6_values(
    section: str, key: str, value: float, message: str
) -> None:
    raw = _m6_mapping()
    raw[section][key] = value

    with pytest.raises(ConfigError, match=message):
        load_config_from_mapping(raw)


def test_v1_1_allows_zero_commission() -> None:
    raw = _m6_mapping()
    raw["execution"]["commission_per_share"] = 0.0

    assert load_config_from_mapping(raw).config.execution.commission_per_share == Decimal("0")


def test_v1_1_requires_drawdown_reduce_before_stop() -> None:
    raw = _m6_mapping()
    raw["risk"]["drawdown_reduce_at"] = raw["risk"]["drawdown_stop_at"]

    with pytest.raises(ConfigError, match="drawdown_reduce_at must be < drawdown_stop_at"):
        load_config_from_mapping(raw)


def test_invalid_risk_rejected() -> None:
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    raw["risk"]["risk_on_per_trade"] = 1.5  # not in [0, 1)
    with pytest.raises(ConfigError):
        load_config_from_mapping(raw)


def test_risk_kill_switch_is_required() -> None:
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    del raw["risk"]["new_entries_enabled"]
    with pytest.raises(ConfigError):
        load_config_from_mapping(raw)


def test_risk_off_budget_must_remain_zero() -> None:
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    raw["risk"]["risk_off_per_trade"] = 0.001
    with pytest.raises(ConfigError):
        load_config_from_mapping(raw)


def test_momentum_weights_must_sum() -> None:
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    raw["momentum"]["return_21_weight"] = 0.5
    with pytest.raises(ConfigError):
        load_config_from_mapping(raw)


def test_auto_live_orders_forbidden() -> None:
    raw = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))
    raw["strategy"]["auto_live_orders"] = True
    with pytest.raises(ConfigError):
        load_config_from_mapping(raw)


def test_missing_file() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(REPO / "configs" / "does_not_exist.yaml")
