"""Pydantic schema for frozen strategy YAML configs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrategySection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    direction: Literal["long_only"] = "long_only"
    timeframe: Literal["daily"] = "daily"
    execution_mode: Literal["shadow", "paper_trading", "live_manual"] = "paper_trading"
    auto_live_orders: bool = False

    @field_validator("auto_live_orders")
    @classmethod
    def no_auto_live(cls, v: bool) -> bool:
        if v:
            msg = "auto_live_orders must be false in Phase 1"
            raise ValueError(msg)
        return v


class UniverseSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: list[str]
    min_price: float = Field(gt=0)
    min_avg_dollar_volume_20d: float = Field(gt=0)
    # Data-governance guard, not a strategy rule: changes config_hash but does
    # not bump the strategy version (ADR 2026-07-22 §2.4).
    max_snapshot_age_days: int = Field(gt=0)


class MarketRegimeSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark: str
    risk_on_conditions: list[str]


class HardFiltersSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[str]
    max_distance_from_52w_high: float = Field(gt=0, le=1)


class MomentumSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    return_21_weight: float = Field(ge=0, le=1)
    return_63_weight: float = Field(ge=0, le=1)
    return_126_weight: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> MomentumSection:
        total = self.return_21_weight + self.return_63_weight + self.return_126_weight
        if abs(total - 1.0) > 1e-9:
            msg = f"momentum weights must sum to 1.0, got {total}"
            raise ValueError(msg)
        return self


class RelativeStrengthSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rs_spy_63_weight: float = Field(ge=0, le=1)
    rs_spy_126_weight: float = Field(ge=0, le=1)
    rs_sector_63_weight: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> RelativeStrengthSection:
        total = self.rs_spy_63_weight + self.rs_spy_126_weight + self.rs_sector_63_weight
        if abs(total - 1.0) > 1e-9:
            msg = f"relative_strength weights must sum to 1.0, got {total}"
            raise ValueError(msg)
        return self


class ScoringSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_as_filter: bool = True
    momentum_weight: float = Field(ge=0, le=1)
    relative_strength_weight: float = Field(ge=0, le=1)
    trend_trigger_weight: float = Field(ge=0, le=1)
    fundamental_weight: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> ScoringSection:
        total = (
            self.momentum_weight
            + self.relative_strength_weight
            + self.trend_trigger_weight
            + self.fundamental_weight
        )
        if abs(total - 1.0) > 1e-9:
            msg = f"scoring weights must sum to 1.0, got {total}"
            raise ValueError(msg)
        if self.fund_as_filter and self.fundamental_weight > 0:
            msg = "fund_as_filter=true requires fundamental_weight=0"
            raise ValueError(msg)
        return self


class SignalSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    breakout_window: int = Field(ge=1)
    relative_volume_min: float = Field(gt=0)
    watchlist_expire_bars: int = Field(ge=1)
    extension_filter_enabled: bool = True
    max_extension_atr: float = Field(gt=0)


class EarningsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_new_entries_before_days: int = Field(ge=0)
    hold_through_earnings: bool = False


class StopSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    atr_buffer: float = Field(ge=0)
    min_stop_distance_atr: float = Field(gt=0)
    max_stop_distance_atr: float = Field(gt=0)

    @model_validator(mode="after")
    def min_le_max(self) -> StopSection:
        if self.min_stop_distance_atr > self.max_stop_distance_atr:
            msg = "min_stop_distance_atr must be <= max_stop_distance_atr"
            raise ValueError(msg)
        return self


class RiskSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_on_per_trade: float = Field(ge=0, lt=1)
    neutral_per_trade: float = Field(ge=0, lt=1)
    risk_off_per_trade: float = Field(ge=0, lt=1)
    max_position_capital: float = Field(gt=0, le=1)
    max_portfolio_heat: float = Field(gt=0, le=1)
    max_sector_risk: float = Field(gt=0, le=1)
    max_risk_cluster_risk: float = Field(gt=0, le=1)
    risk_on_max_exposure: float = Field(ge=0, le=1)
    neutral_max_exposure: float = Field(ge=0, le=1)
    risk_off_max_exposure: float = Field(ge=0, le=1)


class ExitSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixed_profit_target: bool = False
    time_stop_days: int = Field(ge=1)
    time_stop_min_mfe_r: float = Field(ge=0)
    trailing_exit: str
    pyramiding: bool = False


class ExecutionSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_day_open: bool = True
    max_open_gap_atr: float = Field(gt=0)


class ValidationSection(BaseModel):
    """Data-quality thresholds (constitution §12.4).

    These are data-governance guards, not strategy rules: changing them moves
    ``config_hash`` but does not bump the strategy version (ADR §2.4). They live
    here rather than in code because ADR §「对 M1 实现的约束」 §6 forbids
    hardcoding "how big a jump is abnormal" in business logic.
    """

    model_config = ConfigDict(extra="forbid")

    max_abs_daily_return: float = Field(gt=0, le=10)
    max_volume_spike_ratio: float = Field(gt=1)
    max_session_gap_weekdays: int = Field(ge=0)
    min_adj_factor: float = Field(gt=0, le=1)
    adj_factor_tolerance: float = Field(gt=0, lt=1)
    split_ratio_tolerance: float = Field(gt=0, lt=1)


class StrategyConfig(BaseModel):
    """Root strategy configuration (frozen YAML)."""

    model_config = ConfigDict(extra="forbid")

    strategy: StrategySection
    universe: UniverseSection
    validation: ValidationSection
    market_regime: MarketRegimeSection
    hard_filters: HardFiltersSection
    momentum: MomentumSection
    relative_strength: RelativeStrengthSection
    scoring: ScoringSection
    signal: SignalSection
    earnings: EarningsSection
    stop: StopSection
    risk: RiskSection
    exit: ExitSection
    execution: ExecutionSection
