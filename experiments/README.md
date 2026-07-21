# experiments/ — structured research

Use this directory for **versioned research experiments**, not ad-hoc notebook-only parameter tweaks.

## When to create an experiment

- Comparing parameter sets (e.g. momentum weights, exit rules)
- Ablations (with/without fundamental filter)
- Anything that might later change **execution** config

## Layout

```text
experiments/
  exp001_short_name/
    hypothesis.md   # what you believe and why
    config.yaml     # full config or delta from baseline (document which)
    result.md       # outcomes, limitations, go/no-go
  _template/        # copy this
```

## Rules

1. **Do not** promote unfinished experiment parameters into `configs/smm_v1_0_0.yaml` without a decision + version discipline.
2. Execution Shadow/Paper runs must **not** mix stats with experimental configs.
3. Promotion path: `result.md` → `docs/reviews/` → `docs/decisions/` → new config version / strategy bump.
4. Notebooks may explore; **conclusions that change the system** land here or in docs.

## Naming

`expNNN_snake_topic` with zero-padded NNN.
