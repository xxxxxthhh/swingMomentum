# Swing Momentum (SMM)

Personal **Swing Momentum** trading system: constitution-driven rules, daily scanner, shadow/paper execution — not a black-box “pick stocks and hope” script.

**Implementation status:** docs baseline ready. **Next code work is Phase 0 Foundation** (domain, config, fake data, fixtures, CI) — **not** Scanner or yfinance yet. Then Phase 1: MVP-A Signal → MVP-B Risk+Paper. No live auto-trading.

## Docs (source of truth)

| Path | Role |
|------|------|
| [`docs/specs/`](./docs/specs/) | Trading constitution & strategy specification |
| [`docs/system-boundary.md`](./docs/system-boundary.md) | What the system may know / must not know |
| [`docs/plans/`](./docs/plans/) | Phase 0 foundation + Phase 1 implementation |
| [`docs/decisions/`](./docs/decisions/) | ADRs (this folder is the ADR log) |
| [`docs/reviews/`](./docs/reviews/) | Design / stage reviews |
| [`docs/runbooks/`](./docs/runbooks/) | Operational runbooks (as added) |

Start here: **[docs/README.md](./docs/README.md)**

| Plan | When |
|------|------|
| **[Phase 0 Foundation](./docs/plans/2026-07-22_phase0_foundation_implementation_plan.md)** | **Do this first** (domain / config / fixtures / CI) |
| [Phase 1 v1.1](./docs/plans/2026-07-22_phase1_implementation_plan_v1_1.md) | After Phase 0 (MVP-A → MVP-B) |

## Principles (short)

- Risk engine is independent and cannot be bypassed by the scanner
- Fail-closed on bad or missing data
- No look-ahead; paper fills use next-session open
- Shadow → Paper → small capital; **no automatic live orders** in Phase 1
- Every decision should be explainable, computable, reproducible, auditable
- Parameters live in **config**, not hardcoded in business logic
- Core types are **domain objects** (Signal / Order / Position / Trade), not only DataFrames
- Research goes through **experiments/**; notebooks are read-only analytics

## Development (Phase 0)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
smm show-config
smm version
```

- Strategy parameters: [`configs/smm_v1_0_0.yaml`](./configs/smm_v1_0_0.yaml)
- Domain types: `src/smm/domain/`
- Fake market data: `src/smm/data/fake.py` + `tests/fixtures/ohlcv/`
- Research: `experiments/`, `notebooks/` (read-only analytics)

**Config hash:** SHA-256 of canonical JSON (`sort_keys`, compact separators) from the validated pydantic model — see `smm.config.loader`.

## License / disclaimer

This repository is for personal research and engineering practice. Nothing here is investment advice. Past rules or simulated results do not guarantee future performance.
