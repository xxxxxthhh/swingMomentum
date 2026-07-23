"""Pure projection from M5 decisions to M3 lifecycle transitions."""

from __future__ import annotations

from collections.abc import Sequence

from smm.core.errors import DataValidationError
from smm.domain.enums import RiskVerdict, SignalState
from smm.domain.models import RiskDecision
from smm.risk.batches import validate_risk_decision_batch
from smm.signals.lifecycle import SignalTransition, latest_transitions

_TARGET_STATES = {
    RiskVerdict.ACCEPT: SignalState.RISK_ACCEPTED,
    RiskVerdict.REJECT: SignalState.RISK_REJECTED,
}


def project_risk_decisions_to_transitions(
    decisions: Sequence[RiskDecision],
    source_transitions: Sequence[SignalTransition],
) -> tuple[SignalTransition, ...]:
    """Project one validated M5 batch onto its latest persisted triggers.

    The caller remains responsible for any later append/seal operation. This
    pure seam only establishes that every M5 decision can be represented as
    one fail-closed M3 transition after its actual trigger session.
    """
    batch = validate_risk_decision_batch(decisions)
    sources = tuple(source_transitions)
    if any(not isinstance(item, SignalTransition) for item in sources):
        raise DataValidationError(
            "risk transition projection requires SignalTransition source items"
        )
    latest = latest_transitions(sources)

    projected: list[SignalTransition] = []
    for decision in batch:
        source = latest.get(decision.signal_id)
        if source is None:
            raise DataValidationError(
                f"risk decision missing source transition for signal_id={decision.signal_id}"
            )
        if source.to_state is not SignalState.TRIGGERED:
            raise DataValidationError(
                "risk decision source transition must be triggered for "
                f"signal_id={decision.signal_id}"
            )
        if decision.symbol != source.symbol:
            raise DataValidationError(
                f"risk decision symbol mismatch for signal_id={decision.signal_id}"
            )
        if decision.strategy_version != source.strategy_version:
            raise DataValidationError(
                f"risk decision strategy version mismatch for signal_id={decision.signal_id}"
            )
        if decision.config_hash != source.config_hash:
            raise DataValidationError(
                f"risk decision config hash mismatch for signal_id={decision.signal_id}"
            )
        if decision.as_of <= source.as_of:
            raise DataValidationError(
                "risk decision as_of must follow source transition for "
                f"signal_id={decision.signal_id}"
            )

        projected.append(
            SignalTransition(
                signal_id=source.signal_id,
                symbol=source.symbol,
                setup_key=source.setup_key,
                watchlist_entry=source.watchlist_entry,
                from_state=SignalState.TRIGGERED,
                to_state=_TARGET_STATES[decision.verdict],
                as_of=decision.as_of,
                reason_codes=decision.reason_codes,
                strategy_version=decision.strategy_version,
                config_hash=decision.config_hash,
                breakout_level=source.breakout_level,
                relative_volume=source.relative_volume,
                extension_atr=source.extension_atr,
            )
        )
    return tuple(projected)
