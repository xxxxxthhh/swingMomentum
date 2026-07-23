"""Pure M7 construction of D-anchored risk candidates for evaluation day X.

M7 Option 1b consumes an already-persisted trigger at ``D`` on a later
evaluation session ``X``.  The candidate therefore carries X identity for the
M5 batch, while this adapter keeps the price, stop, and ranking facts anchored
to D.  It intentionally accepts already-retrieved inputs only: runtime I/O,
snapshot discovery, ledger selection, lifecycle writes, and paper orders stay
outside this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, field_validator

from smm.config.schema import ExecutionSection, StopSection
from smm.core.errors import DataValidationError
from smm.domain.enums import MarketRegime, SignalState
from smm.domain.models import EligibleCandidate, PortfolioSnapshot, PrintBar
from smm.domain.views import to_tradeable
from smm.features.cross_section import ScoredSymbol
from smm.features.engine import SymbolFeatures
from smm.signals.lifecycle import SignalTransition

_ZERO = Decimal("0")
_BPS_DENOMINATOR = Decimal("10000")


class EvaluationFacts(BaseModel):
    """Identity and regime known on the actual M7 evaluation session X."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: date
    regime: MarketRegime
    strategy_version: str
    config_hash: str

    @field_validator("strategy_version", "config_hash")
    @classmethod
    def identity_is_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evaluation identity fields must be non-empty")
        return value


@dataclass(frozen=True, slots=True)
class TriggerCandidateSource:
    """Fixture-fed, retrievable D-side facts for one persisted trigger.

    ``sessions`` is exactly the expected provider-session window from
    ``watchlist_entry`` through the persisted trigger date.  Requiring it here
    makes a missing historical print observable rather than allowing a caller
    to substitute a current bar or silently shorten ``SetupLow`` history.
    """

    transition: SignalTransition
    sessions: tuple[date, ...]
    print_bars: tuple[PrintBar, ...]
    print_provenance_id: str
    trigger_features: SymbolFeatures
    trigger_score: ScoredSymbol
    feature_strategy_version: str
    feature_config_hash: str


@dataclass(frozen=True, slots=True)
class CandidateProvenance:
    """Explicit D/X provenance that does not fit in ``EligibleCandidate.as_of``."""

    signal_id: str
    trigger_as_of: date
    trigger_feature_as_of: date
    evaluation_as_of: date
    print_sessions: tuple[date, ...]
    print_provenance_id: str
    feature_strategy_version: str
    feature_config_hash: str
    source_strategy_version: str
    source_config_hash: str


@dataclass(frozen=True, slots=True)
class CandidateEvaluationInputs:
    """Validated inputs ready for the existing M5 risk engine.

    ``portfolio`` is returned unchanged after identity validation.  Deciding
    whether it came from an external bootstrap or a future paper ledger is an
    M7 runtime concern and is deliberately not represented here.
    """

    candidates: tuple[EligibleCandidate, ...]
    provenance: tuple[CandidateProvenance, ...]
    portfolio: PortfolioSnapshot


def build_candidate_evaluation_inputs(
    *,
    sources: Sequence[TriggerCandidateSource],
    evaluation: EvaluationFacts,
    portfolio: PortfolioSnapshot,
    stop: StopSection,
    execution: ExecutionSection,
) -> CandidateEvaluationInputs:
    """Build X-identity candidates from D-anchored, retrievable evidence.

    The adapter never re-triggers at X.  Its only accepted price source is the
    D ``PrintBar`` window; adjusted features are accepted only for D ATR and
    D batch-ranking scores.  Missing or non-retrievable evidence is a
    :class:`DataValidationError`, not a fallback to current facts.
    """
    if not isinstance(evaluation, EvaluationFacts):
        raise DataValidationError("candidate evaluation requires EvaluationFacts")
    _validate_portfolio(portfolio, evaluation)
    if not isinstance(stop, StopSection):
        raise DataValidationError("candidate evaluation requires StopSection")
    if not isinstance(execution, ExecutionSection):
        raise DataValidationError("candidate evaluation requires ExecutionSection")

    candidates: list[EligibleCandidate] = []
    provenance: list[CandidateProvenance] = []
    for source in sources:
        candidate, record = _candidate_from_source(
            source,
            evaluation=evaluation,
            stop=stop,
            execution=execution,
        )
        candidates.append(candidate)
        provenance.append(record)
    return CandidateEvaluationInputs(
        candidates=tuple(candidates),
        provenance=tuple(provenance),
        portfolio=portfolio,
    )


def _validate_portfolio(portfolio: PortfolioSnapshot, evaluation: EvaluationFacts) -> None:
    if not isinstance(portfolio, PortfolioSnapshot):
        raise DataValidationError("candidate evaluation requires PortfolioSnapshot")
    identity = (portfolio.as_of, portfolio.strategy_version, portfolio.config_hash)
    expected = (evaluation.as_of, evaluation.strategy_version, evaluation.config_hash)
    if identity != expected:
        raise DataValidationError("portfolio snapshot identity does not match evaluation")


def _candidate_from_source(
    source: TriggerCandidateSource,
    *,
    evaluation: EvaluationFacts,
    stop: StopSection,
    execution: ExecutionSection,
) -> tuple[EligibleCandidate, CandidateProvenance]:
    if not isinstance(source, TriggerCandidateSource):
        raise DataValidationError("candidate source must be TriggerCandidateSource")
    transition = source.transition
    if not isinstance(transition, SignalTransition):
        raise DataValidationError("candidate source requires SignalTransition")
    if transition.to_state is not SignalState.TRIGGERED:
        raise DataValidationError("candidate source transition must be TRIGGERED")
    if transition.as_of >= evaluation.as_of:
        raise DataValidationError("candidate evaluation must follow trigger as_of")
    if (
        transition.strategy_version != evaluation.strategy_version
        or transition.config_hash != evaluation.config_hash
    ):
        raise DataValidationError("trigger transition identity does not match evaluation")

    _validate_trigger_feature(source, transition)
    _validate_trigger_score(source.trigger_score, transition)
    print_bars = _validate_print_coverage(source, transition)
    tradeable_bars = tuple(to_tradeable(bar) for bar in print_bars)

    atr = _positive_finite_decimal(source.trigger_features.atr, label="trigger ATR20")
    entry_reference = _positive_finite_decimal(
        tradeable_bars[-1].close,
        label="trigger TradeableBar close",
    )
    setup_low = min(
        _positive_finite_decimal(bar.low, label="trigger TradeableBar low")
        for bar in tradeable_bars
    )
    atr_buffer = _finite_decimal(stop.atr_buffer, label="stop.atr_buffer")
    if atr_buffer < _ZERO:
        raise DataValidationError("stop.atr_buffer must be non-negative")
    stop_reference = setup_low - atr_buffer * atr
    if stop_reference <= _ZERO or entry_reference <= stop_reference:
        raise DataValidationError("D-anchored PrintBar setup does not yield a positive stop")

    min_distance = _positive_finite_decimal(
        stop.min_stop_distance_atr,
        label="stop.min_stop_distance_atr",
    )
    max_distance = _positive_finite_decimal(
        stop.max_stop_distance_atr,
        label="stop.max_stop_distance_atr",
    )
    if min_distance > max_distance:
        raise DataValidationError("stop.min_stop_distance_atr must be <= max_stop_distance_atr")
    stop_distance_atr = (entry_reference - stop_reference) / atr
    if not min_distance <= stop_distance_atr <= max_distance:
        raise DataValidationError("D-anchored stop distance is outside frozen ATR bounds")

    entry_cost, total_cost = _estimated_costs(
        entry_reference=entry_reference,
        stop_reference=stop_reference,
        execution=execution,
    )
    try:
        candidate = EligibleCandidate(
            signal_id=transition.signal_id,
            symbol=transition.symbol,
            as_of=evaluation.as_of,
            strategy_version=evaluation.strategy_version,
            config_hash=evaluation.config_hash,
            regime=evaluation.regime,
            sector=source.trigger_score.sector,
            risk_cluster="unclassified",
            entry_reference=entry_reference,
            stop_reference=stop_reference,
            estimated_entry_cost_per_share=entry_cost,
            estimated_total_cost_per_share=total_cost,
            momentum_score=source.trigger_score.momentum_score,
            relative_strength_score=source.trigger_score.relative_strength_score,
        )
    except ValueError as exc:
        raise DataValidationError("invalid D-anchored eligible candidate") from exc
    record = CandidateProvenance(
        signal_id=transition.signal_id,
        trigger_as_of=transition.as_of,
        trigger_feature_as_of=source.trigger_features.as_of,
        evaluation_as_of=evaluation.as_of,
        print_sessions=tuple(bar.date for bar in tradeable_bars),
        print_provenance_id=source.print_provenance_id,
        feature_strategy_version=source.feature_strategy_version,
        feature_config_hash=source.feature_config_hash,
        source_strategy_version=transition.strategy_version,
        source_config_hash=transition.config_hash,
    )
    return candidate, record


def _validate_trigger_feature(
    source: TriggerCandidateSource,
    transition: SignalTransition,
) -> None:
    feature = source.trigger_features
    if not isinstance(feature, SymbolFeatures):
        raise DataValidationError("candidate source requires SymbolFeatures")
    if feature.symbol != transition.symbol or feature.as_of != transition.as_of:
        raise DataValidationError("trigger feature identity does not match transition")
    if (
        not source.feature_strategy_version.strip()
        or not source.feature_config_hash.strip()
        or source.feature_strategy_version != transition.strategy_version
        or source.feature_config_hash != transition.config_hash
    ):
        raise DataValidationError("trigger feature snapshot identity does not match transition")


def _validate_trigger_score(score: ScoredSymbol, transition: SignalTransition) -> None:
    if not isinstance(score, ScoredSymbol):
        raise DataValidationError("candidate source requires ScoredSymbol")
    if score.symbol != transition.symbol:
        raise DataValidationError("trigger score symbol does not match transition")
    if score.sector is None or not score.sector.strip():
        raise DataValidationError("trigger score must provide a sector")


def _validate_print_coverage(
    source: TriggerCandidateSource,
    transition: SignalTransition,
) -> tuple[PrintBar, ...]:
    sessions = tuple(source.sessions)
    if not sessions or sessions != tuple(sorted(set(sessions))):
        raise DataValidationError("trigger print sessions must be sorted and unique")
    if sessions[0] != transition.watchlist_entry or sessions[-1] != transition.as_of:
        raise DataValidationError(
            "trigger print sessions must cover watchlist entry through trigger"
        )
    bars = tuple(source.print_bars)
    if not source.print_provenance_id.strip():
        raise DataValidationError("candidate source requires PrintBar provenance identity")
    if any(not isinstance(bar, PrintBar) for bar in bars):
        raise DataValidationError("candidate source requires retrievable PrintBar coverage")
    if tuple(bar.date for bar in bars) != sessions:
        raise DataValidationError("candidate source requires retrievable PrintBar coverage")
    if any(bar.symbol != transition.symbol for bar in bars):
        raise DataValidationError("trigger PrintBar symbol does not match transition")
    return bars


def _estimated_costs(
    *,
    entry_reference: Decimal,
    stop_reference: Decimal,
    execution: ExecutionSection,
) -> tuple[Decimal, Decimal]:
    """Estimate M5's two costs from the accepted M6 buy/sell model.

    A future entry spends buy spread/entry-slippage plus commission at the D
    reference; initial unit risk also includes a conservative stop-side sell
    spread/exit-slippage plus commission.  This is an estimate only, never a
    fill, and M6 still re-quotes at the true-print next open.
    """
    half_spread = _positive_finite_decimal(
        execution.half_spread_bps,
        label="execution.half_spread_bps",
    )
    entry_slippage = _positive_finite_decimal(
        execution.entry_slippage_bps,
        label="execution.entry_slippage_bps",
    )
    exit_slippage = _positive_finite_decimal(
        execution.exit_slippage_bps,
        label="execution.exit_slippage_bps",
    )
    commission = _finite_decimal(
        execution.commission_per_share,
        label="execution.commission_per_share",
    )
    if commission < _ZERO:
        raise DataValidationError("execution.commission_per_share must be non-negative")
    entry_cost = entry_reference * (half_spread + entry_slippage) / _BPS_DENOMINATOR + commission
    exit_cost = stop_reference * (half_spread + exit_slippage) / _BPS_DENOMINATOR + commission
    total_cost = entry_cost + exit_cost
    return _validate_cost_estimate(entry_cost=entry_cost, total_cost=total_cost)


def _validate_cost_estimate(
    *,
    entry_cost: Decimal,
    total_cost: Decimal,
) -> tuple[Decimal, Decimal]:
    if entry_cost <= _ZERO or total_cost < entry_cost:
        raise DataValidationError("M6 cost estimate must be positive and complete")
    return entry_cost, total_cost


def _positive_finite_decimal(value: object, *, label: str) -> Decimal:
    decimal = _finite_decimal(value, label=label)
    if decimal <= _ZERO:
        raise DataValidationError(f"{label} must be positive")
    return decimal


def _finite_decimal(value: object, *, label: str) -> Decimal:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DataValidationError(f"invalid {label}") from exc
    if not decimal.is_finite():
        raise DataValidationError(f"non-finite {label}")
    return decimal
