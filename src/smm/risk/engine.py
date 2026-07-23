"""M5 deterministic risk decisions with M7 circuit-aware whole-share sizing.

This module consumes already-validated domain inputs. It never reads a market
bar, creates an order/fill/position, writes lifecycle state, or mutates frozen
config; M7 supplies the immutable execution context explicitly.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_FLOOR, Decimal

from smm.config.schema import RiskSection
from smm.core.errors import FailClosedError
from smm.domain.enums import MarketRegime, RiskVerdict
from smm.domain.models import (
    EligibleCandidate,
    PortfolioSnapshot,
    RiskDecision,
    RiskExecutionContext,
)

_ZERO = Decimal(0)

_CAPACITY_CODES = (
    ("per_trade", "risk_per_trade_budget_exhausted", "risk_sized_by_per_trade"),
    ("position", "risk_position_cap_reached", "risk_sized_by_position_cap"),
    ("cash", "risk_cash_exhausted", "risk_sized_by_cash"),
    ("exposure", "risk_exposure_limit_reached", "risk_sized_by_exposure"),
    (
        "portfolio_heat",
        "risk_portfolio_heat_limit_reached",
        "risk_sized_by_portfolio_heat",
    ),
    ("sector", "risk_sector_limit_reached", "risk_sized_by_sector"),
    ("cluster", "risk_cluster_limit_reached", "risk_sized_by_cluster"),
)


class RiskValidationError(FailClosedError):
    """The whole M5 batch is invalid and must produce no executable plan."""


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _capacity(remaining: Decimal, per_share: Decimal) -> int:
    usable = max(remaining, _ZERO)
    return int((usable / per_share).to_integral_value(rounding=ROUND_FLOOR))


def _sort_key(candidate: EligibleCandidate) -> tuple[bool, float, bool, float, str]:
    return (
        candidate.momentum_score is None,
        -(candidate.momentum_score or 0.0),
        candidate.relative_strength_score is None,
        -(candidate.relative_strength_score or 0.0),
        candidate.symbol,
    )


def _validate_batch(
    candidates: Sequence[EligibleCandidate],
    portfolio: PortfolioSnapshot,
    risk: RiskSection,
    execution_context: RiskExecutionContext,
) -> None:
    if risk.risk_off_per_trade != 0:
        raise RiskValidationError("risk_off_per_trade must be zero")

    signal_ids = [candidate.signal_id for candidate in candidates]
    symbols = [candidate.symbol for candidate in candidates]
    if len(set(signal_ids)) != len(signal_ids):
        raise RiskValidationError("duplicate signal_id in risk batch")
    if len(set(symbols)) != len(symbols):
        raise RiskValidationError("duplicate symbol in risk batch")

    regimes = {candidate.regime for candidate in candidates}
    if len(regimes) > 1:
        raise RiskValidationError("risk batch contains mixed regimes")

    expected = (portfolio.as_of, portfolio.strategy_version, portfolio.config_hash)
    context_identity = (
        execution_context.as_of,
        execution_context.strategy_version,
        execution_context.config_hash,
    )
    if context_identity != expected:
        raise RiskValidationError("risk execution context identity mismatch")
    for candidate in candidates:
        identity = (candidate.as_of, candidate.strategy_version, candidate.config_hash)
        if identity != expected:
            raise RiskValidationError("candidate and portfolio identity mismatch")
        if candidate.signal_id in portfolio.reserved_signal_ids:
            raise RiskValidationError("candidate signal identity is already reserved")


def _decision(
    candidate: EligibleCandidate,
    *,
    execution_context: RiskExecutionContext,
    verdict: RiskVerdict,
    reason_codes: tuple[str, ...],
    quantity: int = 0,
) -> RiskDecision:
    planned_capital = candidate.capital_per_share * quantity
    planned_initial_risk = candidate.unit_risk * quantity
    return RiskDecision(
        signal_id=candidate.signal_id,
        symbol=candidate.symbol,
        as_of=candidate.as_of,
        strategy_version=candidate.strategy_version,
        config_hash=candidate.config_hash,
        entry_risk_multiplier=execution_context.entry_risk_multiplier,
        circuit_state_identity=execution_context.circuit_state_identity,
        verdict=verdict,
        reason_codes=reason_codes,
        quantity=quantity,
        entry_reference=candidate.entry_reference,
        stop_reference=candidate.stop_reference,
        unit_risk=candidate.unit_risk,
        planned_capital=planned_capital,
        planned_initial_risk=planned_initial_risk,
        sector=candidate.sector,
        risk_cluster=candidate.risk_cluster,
        regime=candidate.regime,
    )


def evaluate_risk_batch(
    candidates: Sequence[EligibleCandidate],
    portfolio: PortfolioSnapshot,
    risk: RiskSection,
    *,
    execution_context: RiskExecutionContext,
) -> tuple[RiskDecision, ...]:
    """Return deterministic decisions with an explicit circuit execution context."""
    if not isinstance(execution_context, RiskExecutionContext):
        raise RiskValidationError("risk execution context is required")
    _validate_batch(candidates, portfolio, risk, execution_context)

    cash = portfolio.available_cash
    gross_exposure = portfolio.gross_exposure_capital
    portfolio_risk = portfolio.portfolio_initial_risk
    sector_risk = dict(portfolio.sector_initial_risk)
    cluster_risk = dict(portfolio.cluster_initial_risk)
    open_symbols = set(portfolio.open_symbols)
    reserved_signal_ids = set(portfolio.reserved_signal_ids)
    decisions: list[RiskDecision] = []

    for candidate in sorted(candidates, key=_sort_key):
        if not risk.new_entries_enabled:
            decisions.append(
                _decision(
                    candidate,
                    execution_context=execution_context,
                    verdict=RiskVerdict.REJECT,
                    reason_codes=("risk_new_entries_kill_switch",),
                )
            )
            continue
        if execution_context.new_entries_blocked:
            decisions.append(
                _decision(
                    candidate,
                    execution_context=execution_context,
                    verdict=RiskVerdict.REJECT,
                    reason_codes=("risk_off_new_entries_blocked",),
                )
            )
            continue
        if candidate.regime is MarketRegime.RISK_OFF:
            decisions.append(
                _decision(
                    candidate,
                    execution_context=execution_context,
                    verdict=RiskVerdict.REJECT,
                    reason_codes=("risk_off_new_entries_blocked",),
                )
            )
            continue
        if candidate.symbol in open_symbols:
            decisions.append(
                _decision(
                    candidate,
                    execution_context=execution_context,
                    verdict=RiskVerdict.REJECT,
                    reason_codes=("risk_symbol_already_open",),
                )
            )
            continue

        if candidate.regime is MarketRegime.RISK_ON:
            risk_per_trade = (
                _decimal(risk.risk_on_per_trade)
                * execution_context.entry_risk_multiplier
            )
            max_exposure = _decimal(risk.risk_on_max_exposure)
        else:
            risk_per_trade = (
                _decimal(risk.neutral_per_trade)
                * execution_context.entry_risk_multiplier
            )
            max_exposure = _decimal(risk.neutral_max_exposure)

        equity = portfolio.account_equity
        capacities = {
            "per_trade": _capacity(equity * risk_per_trade, candidate.unit_risk),
            "position": _capacity(
                equity * _decimal(risk.max_position_capital),
                candidate.capital_per_share,
            ),
            "cash": _capacity(cash, candidate.capital_per_share),
            "exposure": _capacity(
                equity * max_exposure - gross_exposure,
                candidate.capital_per_share,
            ),
            "portfolio_heat": _capacity(
                equity * _decimal(risk.max_portfolio_heat) - portfolio_risk,
                candidate.unit_risk,
            ),
            "sector": _capacity(
                equity * _decimal(risk.max_sector_risk)
                - sector_risk.get(candidate.sector, _ZERO),
                candidate.unit_risk,
            ),
            "cluster": _capacity(
                equity * _decimal(risk.max_risk_cluster_risk)
                - cluster_risk.get(candidate.risk_cluster, _ZERO),
                candidate.unit_risk,
            ),
        }
        quantity = min(capacities.values())

        if quantity == 0:
            reasons = tuple(
                exhausted
                for name, exhausted, _ in _CAPACITY_CODES
                if capacities[name] == 0
            )
            decisions.append(
                _decision(
                    candidate,
                    execution_context=execution_context,
                    verdict=RiskVerdict.REJECT,
                    reason_codes=reasons,
                )
            )
            continue

        reasons = tuple(
            sized_by
            for name, _, sized_by in _CAPACITY_CODES
            if capacities[name] == quantity
        )
        decision = _decision(
            candidate,
            execution_context=execution_context,
            verdict=RiskVerdict.ACCEPT,
            reason_codes=reasons,
            quantity=quantity,
        )
        decisions.append(decision)

        cash -= decision.planned_capital
        gross_exposure += decision.planned_capital
        portfolio_risk += decision.planned_initial_risk
        sector_risk[candidate.sector] = (
            sector_risk.get(candidate.sector, _ZERO) + decision.planned_initial_risk
        )
        cluster_risk[candidate.risk_cluster] = (
            cluster_risk.get(candidate.risk_cluster, _ZERO) + decision.planned_initial_risk
        )
        open_symbols.add(candidate.symbol)
        reserved_signal_ids.add(candidate.signal_id)

    return tuple(decisions)
