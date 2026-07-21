# Phase 1 Implementation Plan 评审

| 字段 | 值 |
|------|-----|
| 文档类型 | review |
| 状态 | accepted |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0 |
| 评审对象 | [../plans/2026-07-21_phase1_implementation_plan.md](../plans/2026-07-21_phase1_implementation_plan.md) |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md) |
| 后续动作 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)、[../decisions/2026-07-22_phase1_mvp_slicing_v1_1.md](../decisions/2026-07-22_phase1_mvp_slicing_v1_1.md) |
| 变更摘要 | 批准 v1.0 计划方向；要求修订 Signal 生命周期、Watchlist、两阶段 MVP 后再编码 |

---

## 1. 结论

| 项 | 判断 |
|----|------|
| 总体评分 | 8.5/10（个人量化项目语境） |
| 架构方向 | **通过** — 交易系统而非「选股脚本」 |
| 是否推翻重写 | **否** |
| 是否可立即编码 | **否** — 先完成 Plan v1.1 与 ADR 修订 |
| 晋级条件 | v1.1 计划 `approved` 后进入实现 |

---

## 2. 值得肯定的设计

1. **Risk Engine 独立** — 风险引擎不得被 Scanner 绕过；交易系统核心是控制暴露，而非仅发现机会。
2. **Fail-closed** — 数据缺失默认拒绝可交易动作，而非静默填 0 继续交易。
3. **Shadow → Paper → Small Capital** — 第一阶段验证数据、信号幂等、仓位与出场逻辑，而非追求立刻赚钱；且不自动真实下单。
4. **分层流水线** — 宪法 → 规格 → 数据治理 → Feature → Signal → Risk → Paper → Evaluation，与交易宪法一致。

---

## 3. 必须修改 / 补充（按优先级）

| 优先级 | 修改 | 理由 |
|--------|------|------|
| **P0** | 增加 **Signal State Machine** | 缺少信号从检测到终态的生命周期；否则易每日重复推荐同一标的 |
| **P0** | 增加 **Watchlist Layer** | 专业流程为 Universe → Watchlist → Setup → Trigger → Position；不能过滤后直接买 |
| **P1** | MVP 拆成 **Signal MVP** 与 **Risk+Paper MVP** | 先证明信号形态与稳定性，再投入下注工程 |
| **P1** | 基本面降低权重，优先作 **风险过滤** | 动量策略以价格信息为主；财报滞后可能错过第一段行情 |
| **P1** | **Setup 规则简化** | V1 只做 20 日突破 + 量能阈值；不编码「漂亮整理」以免过拟合 |
| **P2** | 增加 **Manual Decision Log** | 区分系统错误与人工干预 |
| **P2** | 明确 **Notebook Research Layer** | 只分析/可视化；禁止改生产/执行数据 |
| **P2** | 增加 **Synthetic Test Fixtures** | 先人造案例验逻辑，再接真实行情 |

---

## 4. 架构正确性摘要

v1.0 计划已正确避免常见个人量化路径（下载数据 → 指标 → 回测赚钱 → 上线），而是按**长期运行的决策系统**设计。  
与「交易宪法」一致的部分应保留；缺陷集中在**信号产品化不足**与 **MVP 切片偏重**，而非方向错误。

---

## 5. 编码前建议顺序（评审时提出）

1. 冻结 **SMM-V1 策略 Config（YAML）** — 全部参数版本化。  
2. 建立 **Synthetic Test Data** — 突破成功 / 假突破 / Risk-Off 等案例。  
3. 再接真实数据与日更流水线。

（详细里程碑与验收以 Plan v1.1 为准。）

---

## 6. 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-22 | accepted | 评审结论落盘；要求产出 Plan v1.1 后再实现 |
