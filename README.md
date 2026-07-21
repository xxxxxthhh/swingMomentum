# Swing Momentum (SMM)

Personal **Swing Momentum** trading system: constitution-driven rules, daily scanner, shadow/paper execution — not a black-box “pick stocks and hope” script.

**Phase 1 status:** documentation baseline ready; implementation follows the approved plan (MVP-A Signal → MVP-B Risk+Paper). No live auto-trading.

## Docs (source of truth)

| Path | Role |
|------|------|
| [`docs/specs/`](./docs/specs/) | Trading constitution & strategy specification |
| [`docs/plans/`](./docs/plans/) | Phase implementation plans |
| [`docs/decisions/`](./docs/decisions/) | ADRs (stack, MVP slicing, rule boundaries) |
| [`docs/reviews/`](./docs/reviews/) | Design / stage reviews |
| [`docs/runbooks/`](./docs/runbooks/) | Operational runbooks (as added) |

Start here: **[docs/README.md](./docs/README.md)**

Current Phase 1 plan: **[docs/plans/2026-07-22_phase1_implementation_plan_v1_1.md](./docs/plans/2026-07-22_phase1_implementation_plan_v1_1.md)**

## Principles (short)

- Risk engine is independent and cannot be bypassed by the scanner
- Fail-closed on bad or missing data
- No look-ahead; paper fills use next-session open
- Shadow → Paper → small capital; **no automatic live orders** in Phase 1
- Every decision should be explainable, computable, reproducible, auditable

## License / disclaimer

This repository is for personal research and engineering practice. Nothing here is investment advice. Past rules or simulated results do not guarantee future performance.
