# decisions/ — 决策记录

采用简短 ADR（Architecture / Strategy Decision Record）风格，记录**不可从代码直接读出的选择与理由**。

模板字段见 [docs/README.md](../README.md)。

本目录即 **ADR（Architecture Decision Record）** 存放处；不另建 `docs/adr/`。

当前：

- [2026-07-21_phase1_scope_and_stack.md](./2026-07-21_phase1_scope_and_stack.md)（accepted；§1 技术栈有效，§2/§3 部分被后续 ADR 修订）
- [2026-07-22_phase1_mvp_slicing_v1_1.md](./2026-07-22_phase1_mvp_slicing_v1_1.md)（accepted）— 两阶段 MVP 与 V1 规则边界
- [2026-07-22_phase0_repo_foundation.md](./2026-07-22_phase0_repo_foundation.md)（accepted）— Phase 0 domain/config/fixtures/CI
- [2026-07-22_m1_data_provider_and_universe.md](./2026-07-22_m1_data_provider_and_universe.md)（accepted）— M1 数据源、股票池快照、Bar 双价格表示、fixture 生成器
- [2026-07-22_m2_feature_engine_and_regime.md](./2026-07-22_m2_feature_engine_and_regime.md)（accepted）— M2 特征引擎、板块 ETF 基准、缺失语义、市场状态
- [2026-07-22_m3_watchlist_and_signal_lifecycle.md](./2026-07-22_m3_watchlist_and_signal_lifecycle.md)（proposed）— M3 Watchlist、信号身份与生命周期持久化

被替代的决策不要删除，标记 `superseded` 并链接新文档；部分条款修订可在旧文标注并指向新 ADR。
