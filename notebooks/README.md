# notebooks/ — research only

## Allowed

- Exploratory analysis and visualization
- Factor charts, signal reviews, post-mortems
- Reading local Parquet / report exports

## Forbidden

- Writing the **execution** SQLite database (signals, orders, positions)
- Overwriting production contract paths under governed `data/` layouts
- Silently changing frozen execution config used by Shadow/Paper
- Treating notebook cells as the source of strategy truth

## Promotion

Insights that change rules or parameters must go through:

```text
notebook exploration
  → experiments/ (hypothesis + result)
  → docs/reviews/ + docs/decisions/
  → configs/ version bump
```

Suggested names (create when needed):

- `01_factor_analysis.ipynb`
- `02_signal_review.ipynb`
- `03_strategy_comparison.ipynb`
- `04_trade_postmortem.ipynb`
