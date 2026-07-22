"""M5 pure risk-engine contract regressions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from smm.config.loader import load_config
from smm.domain.enums import MarketRegime, RiskVerdict
from smm.domain.models import Bar, EligibleCandidate, PortfolioSnapshot
from smm.risk.engine import RiskValidationError, evaluate_risk_batch

AS_OF = date(2024, 6, 14)
VERSION = "SMM-V1.0.0"
CONFIG_HASH = "a" * 64


def candidate(
    symbol: str,
    *,
    regime: MarketRegime = MarketRegime.RISK_ON,
    risk_cluster: str | None = "growth",
    momentum_score: float | None = 90.0,
    relative_strength_score: float | None = 80.0,
    **updates: object,
) -> EligibleCandidate:
    values: dict[str, object] = {
        "signal_id": f"signal-{symbol}",
        "symbol": symbol,
        "as_of": AS_OF,
        "strategy_version": VERSION,
        "config_hash": CONFIG_HASH,
        "regime": regime,
        "sector": "information_technology",
        "risk_cluster": risk_cluster,
        "entry_reference": "100",
        "stop_reference": "90",
        "estimated_entry_cost_per_share": "1",
        "estimated_total_cost_per_share": "1",
        "momentum_score": momentum_score,
        "relative_strength_score": relative_strength_score,
    }
    values.update(updates)
    return EligibleCandidate(**values)


def portfolio(**updates: object) -> PortfolioSnapshot:
    values: dict[str, object] = {
        "as_of": AS_OF,
        "account_equity": "100000",
        "available_cash": "100000",
        "gross_exposure_capital": "0",
        "portfolio_initial_risk": "0",
        "sector_initial_risk": {},
        "cluster_initial_risk": {},
        "open_symbols": frozenset(),
        "reserved_signal_ids": frozenset(),
        "strategy_version": VERSION,
        "config_hash": CONFIG_HASH,
    }
    values.update(updates)
    return PortfolioSnapshot(**values)


@pytest.fixture
def risk_config():
    return load_config().config.risk


def test_batch_reserves_cluster_budget_between_candidates(risk_config) -> None:
    snapshot = portfolio(
        portfolio_initial_risk="1505",
        sector_initial_risk={"other": "1505"},
        cluster_initial_risk={"growth": "1505"},
    )

    decisions = evaluate_risk_batch(
        [candidate("BBB"), candidate("AAA")], snapshot, risk_config
    )

    assert [decision.symbol for decision in decisions] == ["AAA", "BBB"]
    assert decisions[0].verdict is RiskVerdict.ACCEPT
    assert decisions[0].quantity == 45
    assert decisions[0].planned_initial_risk == Decimal("495")
    assert decisions[1].verdict is RiskVerdict.REJECT
    assert decisions[1].reason_codes == ("risk_cluster_limit_reached",)


def test_kill_switch_precedes_risk_off_and_open_symbol(risk_config) -> None:
    disabled = risk_config.model_copy(update={"new_entries_enabled": False})
    decision = evaluate_risk_batch(
        [candidate("AAA", regime=MarketRegime.RISK_OFF)],
        portfolio(open_symbols=frozenset({"AAA"})),
        disabled,
    )[0]
    assert decision.reason_codes == ("risk_new_entries_kill_switch",)


def test_risk_off_precedes_open_symbol(risk_config) -> None:
    decision = evaluate_risk_batch(
        [candidate("AAA", regime=MarketRegime.RISK_OFF)],
        portfolio(open_symbols=frozenset({"AAA"})),
        risk_config,
    )[0]
    assert decision.reason_codes == ("risk_off_new_entries_blocked",)


def test_neutral_uses_neutral_per_trade_budget(risk_config) -> None:
    decision = evaluate_risk_batch(
        [candidate("AAA", regime=MarketRegime.NEUTRAL)], portfolio(), risk_config
    )[0]
    assert decision.verdict is RiskVerdict.ACCEPT
    assert decision.quantity == 22
    assert decision.reason_codes == ("risk_sized_by_per_trade",)


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (
            portfolio(
                portfolio_initial_risk="4000",
                sector_initial_risk={"other": "4000"},
                cluster_initial_risk={"other": "4000"},
            ),
            "risk_portfolio_heat_limit_reached",
        ),
        (
            portfolio(
                portfolio_initial_risk="1500",
                sector_initial_risk={"information_technology": "1500"},
                cluster_initial_risk={"other": "1500"},
            ),
            "risk_sector_limit_reached",
        ),
        (
            portfolio(
                portfolio_initial_risk="2000",
                sector_initial_risk={"other": "2000"},
                cluster_initial_risk={"growth": "2000"},
            ),
            "risk_cluster_limit_reached",
        ),
    ],
)
def test_risk_budget_at_limit_rejects_one_share(snapshot, reason, risk_config) -> None:
    decision = evaluate_risk_batch([candidate("AAA")], snapshot, risk_config)[0]
    assert decision.verdict is RiskVerdict.REJECT
    assert reason in decision.reason_codes
    assert decision.quantity == 0


def test_all_zero_capacity_reasons_are_stable_and_ordered(risk_config) -> None:
    snapshot = portfolio(
        available_cash="0",
        gross_exposure_capital="100000",
        portfolio_initial_risk="4000",
        sector_initial_risk={"information_technology": "4000"},
        cluster_initial_risk={"growth": "4000"},
    )
    decision = evaluate_risk_batch([candidate("AAA")], snapshot, risk_config)[0]
    assert decision.reason_codes == (
        "risk_cash_exhausted",
        "risk_exposure_limit_reached",
        "risk_portfolio_heat_limit_reached",
        "risk_sector_limit_reached",
        "risk_cluster_limit_reached",
    )


def test_unclassified_candidates_share_one_budget(risk_config) -> None:
    snapshot = portfolio(
        portfolio_initial_risk="1505",
        sector_initial_risk={"other": "1505"},
        cluster_initial_risk={"unclassified": "1505"},
    )
    decisions = evaluate_risk_batch(
        [candidate("AAA", risk_cluster=None), candidate("BBB", risk_cluster="")],
        snapshot,
        risk_config,
    )
    assert [decision.risk_cluster for decision in decisions] == [
        "unclassified",
        "unclassified",
    ]
    assert [decision.verdict for decision in decisions] == [
        RiskVerdict.ACCEPT,
        RiskVerdict.REJECT,
    ]


def test_reordered_candidates_produce_identical_decision_bytes(risk_config) -> None:
    candidates = [
        candidate("CCC", momentum_score=None, relative_strength_score=None),
        candidate("AAA", momentum_score=90, relative_strength_score=70),
        candidate("BBB", momentum_score=90, relative_strength_score=80),
    ]
    forward = evaluate_risk_batch(candidates, portfolio(), risk_config)
    reverse = evaluate_risk_batch(list(reversed(candidates)), portfolio(), risk_config)
    assert [decision.model_dump_json() for decision in forward] == [
        decision.model_dump_json() for decision in reverse
    ]
    assert [decision.symbol for decision in forward] == ["BBB", "AAA", "CCC"]


@pytest.mark.parametrize(
    "candidates",
    [
        [candidate("AAA"), candidate("BBB", signal_id="signal-AAA")],
        [candidate("AAA"), candidate("AAA", signal_id="other")],
    ],
)
def test_duplicate_batch_identity_fails_closed(candidates, risk_config) -> None:
    with pytest.raises(RiskValidationError, match="duplicate"):
        evaluate_risk_batch(candidates, portfolio(), risk_config)


def test_identity_mismatch_fails_closed(risk_config) -> None:
    with pytest.raises(RiskValidationError, match="identity"):
        evaluate_risk_batch(
            [candidate("AAA", config_hash="b" * 64)], portfolio(), risk_config
        )


def test_candidate_requires_explicit_nonzero_costs() -> None:
    with pytest.raises(ValidationError):
        candidate("AAA", estimated_total_cost_per_share="0")


def test_candidate_cannot_smuggle_provider_bar_into_risk_seam() -> None:
    bar = Bar(
        symbol="AAA",
        date=AS_OF,
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1,
        adj_close=100,
        adj_factor=1,
    )
    with pytest.raises(ValidationError, match="bar"):
        candidate("AAA", bar=bar)
