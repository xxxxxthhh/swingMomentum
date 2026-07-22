"""Cross-sectional ranking and relative strength (ADR R1, §1–§2)."""

from __future__ import annotations

import pytest

from smm.config.loader import load_config
from smm.data.generator import synthetic_universe, universe_rows
from smm.features.cross_section import (
    REASON_NO_SECTOR,
    REASON_RS_SECTOR_MISSING,
    REASON_SECTOR_BENCHMARK_MISSING,
    build_cross_section,
)
from smm.features.engine import SymbolFeatures, compute_features

CONFIG = load_config(None).config
UNIVERSE = synthetic_universe()
AS_OF = UNIVERSE["SPY"].bars[-1].date
SECTORS = {row["symbol"]: row["sector"] for row in universe_rows(AS_OF)}


def all_features() -> dict[str, SymbolFeatures]:
    out: dict[str, SymbolFeatures] = {}
    for symbol, path in UNIVERSE.items():
        result = compute_features(list(path.bars), as_of=AS_OF, cfg=CONFIG.features)
        if isinstance(result, SymbolFeatures):
            out[symbol] = result
    return out


def build(features=None, sectors=None):
    return build_cross_section(
        features if features is not None else all_features(),
        as_of=AS_OF,
        sectors=SECTORS if sectors is None else sectors,
        config=CONFIG,
    )


# --- ranking universe (R1) -------------------------------------------------


def test_benchmarks_are_computed_but_never_ranked() -> None:
    """Their returns are the RS reference; ranking an ETF against its own
    members would compare different kinds of thing."""
    cs = build()
    assert "SPY" not in cs.ranking_universe
    for etf in CONFIG.sector_benchmarks.values():
        assert etf not in cs.ranking_universe
    assert cs.excluded_from_ranking["SPY"] == "benchmark"


def test_ranking_universe_is_the_member_set() -> None:
    cs = build()
    assert set(cs.ranking_universe) == set(SECTORS)


def test_offline_cross_section_is_not_empty() -> None:
    """The whole point of the synthetic universe: a real, scored result."""
    cs = build()
    assert len(cs.candidates) == len(SECTORS)


# --- scores discriminate ---------------------------------------------------


def test_scores_spread_across_the_universe() -> None:
    scores = sorted(s.momentum_score for s in build().candidates)
    assert scores[0] < scores[-1]


def test_a_leader_outscores_a_laggard() -> None:
    scored = build().scored
    assert scored["SYNT1"].momentum_score > scored["SYNT4"].momentum_score
    assert scored["SYNT1"].relative_strength_score > scored["SYNT4"].relative_strength_score


def test_sector_relative_strength_has_both_signs() -> None:
    """A sector RS that is positive for everyone is not measuring anything."""
    values = [s.rs_sector for s in build().scored.values()]
    assert any(v > 0 for v in values)
    assert any(v < 0 for v in values)


def test_relative_strength_is_measured_against_the_sector_etf() -> None:
    """Constitution §18.2 — not against a peer median."""
    features = all_features()
    cs = build(features)
    subject, etf = features["SYNT1"], features["XLK"]
    window = CONFIG.features.return_windows[1]
    expected = subject.returns[window] - etf.returns[window]
    assert cs.scored["SYNT1"].rs_sector == pytest.approx(expected)


# --- missing propagates, never renormalises (§2) ---------------------------


def test_missing_sector_drops_the_symbol_with_a_reason() -> None:
    sectors = dict(SECTORS)
    del sectors["SYNT1"]
    scored = build(sectors=sectors).scored["SYNT1"]

    assert scored.rs_sector is None
    assert scored.relative_strength_score is None
    assert REASON_NO_SECTOR in scored.reason_codes
    assert REASON_RS_SECTOR_MISSING in scored.reason_codes
    assert not scored.is_scored


def test_missing_sector_does_not_renormalise_the_other_weights() -> None:
    """Rescaling 0.40/0.40 to 0.50/0.50 would put a differently-scoped score in
    the same table, corrupting the ranking rather than one row."""
    sectors = dict(SECTORS)
    del sectors["SYNT1"]
    scored = build(sectors=sectors).scored["SYNT1"]
    # Both SPY legs are present, so a renormalising implementation would have
    # produced a number here.
    assert scored.rs_spy_short is not None
    assert scored.rs_spy_long is not None
    assert scored.relative_strength_score is None


def test_unmapped_sector_key_is_reported_distinctly() -> None:
    """A sector with no ETF in config is a different fault from no sector."""
    sectors = dict(SECTORS)
    sectors["SYNT1"] = "utilities"  # mapped to XLU, which is not in the fixture
    scored = build(sectors=sectors).scored["SYNT1"]
    assert REASON_SECTOR_BENCHMARK_MISSING in scored.reason_codes
    assert scored.relative_strength_score is None


def test_momentum_survives_a_missing_sector() -> None:
    """Sector data feeds relative strength only; momentum is price-only."""
    sectors = dict(SECTORS)
    del sectors["SYNT1"]
    assert build(sectors=sectors).scored["SYNT1"].momentum_score is not None


def test_dropping_a_symbol_from_a_component_does_not_shift_the_rest() -> None:
    """Only present values are ranked, so a missing one must not take a slot."""
    baseline = build().scored
    sectors = dict(SECTORS)
    del sectors["SYNT1"]
    without = build(sectors=sectors).scored
    # SYNT1 leaves the rs_sector ranking entirely; the others keep their
    # momentum scores, which never depended on sector data.
    for symbol in ("SYNH1", "SYNH2", "SYNT2"):
        assert without[symbol].momentum_score == pytest.approx(
            baseline[symbol].momentum_score
        )


def test_missing_market_benchmark_leaves_no_candidates() -> None:
    """ADR R2's premise: without SPY every symbol's RS is missing."""
    features = {s: f for s, f in all_features().items() if s != "SPY"}
    cs = build(features)
    assert cs.candidates == []
    assert all(s.relative_strength_score is None for s in cs.scored.values())
