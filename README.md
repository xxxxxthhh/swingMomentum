# Swing Momentum (SMM)

Personal **Swing Momentum** trading system: constitution-driven rules, daily scanner, shadow/paper execution — not a black-box “pick stocks and hope” script.

---

## Supreme source of truth

| Priority | Document | Role |
|----------|----------|------|
| **1 (highest)** | **[`CONSTITUTION.md`](./CONSTITUTION.md)** | Trading constitution + strategy specification (v2.0). **Code and other docs lose if they conflict.** |
| 2 | [`configs/smm_v1_0_0.yaml`](./configs/smm_v1_0_0.yaml) | Frozen executable parameters (must not contradict the constitution) |
| 3 | [`docs/system-boundary.md`](./docs/system-boundary.md) | What the system may / may not know |
| 4 | [`docs/plans/`](./docs/plans/), [`docs/decisions/`](./docs/decisions/) | How we build; why we chose X |
| 5 | Implementation (`src/`) | Must implement the above; never redefine strategy in code alone |

> If you only open one file in this repository, open **[`CONSTITUTION.md`](./CONSTITUTION.md)**.

---

**Implementation status:** Phase 0 and M1–M4 (MVP-A) are complete. The M5 risk-engine and planned-sizing contract is accepted; implementation is next. **No live auto-trading.**

## Docs map

| Path | Role |
|------|------|
| **[`CONSTITUTION.md`](./CONSTITUTION.md)** | **最高权威：交易宪法与策略规格** |
| [`docs/README.md`](./docs/README.md) | Documentation library index & audit rules |
| [`docs/system-boundary.md`](./docs/system-boundary.md) | System boundary |
| [`docs/plans/`](./docs/plans/) | Phase 0 / Phase 1 implementation plans |
| [`docs/decisions/`](./docs/decisions/) | ADRs |
| [`docs/reviews/`](./docs/reviews/) | Design / stage reviews |
| [`docs/runbooks/`](./docs/runbooks/) | Operational runbooks (as added) |
| [`docs/specs/`](./docs/specs/) | Secondary specs only (constitution lives at repo root) |

| Plan | When |
|------|------|
| [Phase 0 Foundation](./docs/plans/2026-07-22_phase0_foundation_implementation_plan.md) | Done (foundation code) |
| [Phase 1 v1.1](./docs/plans/2026-07-22_phase1_implementation_plan_v1_1.md) | Next: real data → MVP-A → MVP-B |

## Principles (short)

Derived from the constitution — full text in [`CONSTITUTION.md`](./CONSTITUTION.md):

- Risk engine is independent and cannot be bypassed by the scanner
- Fail-closed on bad or missing data
- No look-ahead; paper fills use next-session open
- Shadow → Paper → small capital; **no automatic live orders** in Phase 1
- Every decision should be explainable, computable, reproducible, auditable
- Parameters live in **config**, not hardcoded in business logic
- Core types are **domain objects** (Signal / Order / Position / Trade), not only DataFrames
- Research goes through **experiments/**; notebooks are read-only analytics

## Development

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q                   # network tests are deselected by default
smm show-config
smm ingest --as-of 2024-01-26          # offline: deterministic synthetic paths
```

Real market data is an **optional extra**, so default installs and CI stay
network-free:

```bash
pip install -e ".[dev,market]"
smm ingest --as-of 2024-06-14 --source market -s NVDA
pytest -m network -o addopts=""        # opt-in, hits Yahoo
```

- Strategy parameters: [`configs/smm_v1_0_0.yaml`](./configs/smm_v1_0_0.yaml)
- Universe snapshots: [`configs/universe/`](./configs/universe/) (dated, with survivorship disclaimer)
- Domain types: `src/smm/domain/` — provider-native `Bar`, feature-only `AdjustedBar`, and fill-only `PrintBar` / `TradeableBar`
- Synthetic market data: `src/smm/data/generator.py` (the generator is the truth source, not CSVs)
- Research: `experiments/`, `notebooks/` (read-only analytics)

**Price series:** constitution §12.1 requires two. Features read an adjusted
view derived from provider-native bars. Fills and stops accept only a separate
true-print `PrintBar`; Yahoo's split-adjusted OHLC cannot cross that boundary.
The attribute surfaces remain non-overlapping so mixing them fails in code.

**Config hash:** SHA-256 of canonical JSON (`sort_keys`, compact separators) from the validated pydantic model — see `smm.config.loader`.

## License / disclaimer

This repository is for personal research and engineering practice. Nothing here is investment advice. Past rules or simulated results do not guarantee future performance.
