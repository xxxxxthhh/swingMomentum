"""Config load, validation, and hash stability."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from smm.config.loader import compute_config_hash, load_config, load_config_from_mapping
from smm.core.errors import ConfigError

REPO = Path(__file__).resolve().parents[2]
DEFAULT_YAML = REPO / "configs" / "smm_v1_0_0.yaml"


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
