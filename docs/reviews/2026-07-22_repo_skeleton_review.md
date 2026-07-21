# Repo 骨架评审

| 字段 | 值 |
|------|-----|
| 文档类型 | review |
| 状态 | accepted |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0 |
| 评审对象 | 仓库骨架（docs + README，尚无业务实现） |
| 关联计划 | [../plans/2026-07-22_phase0_foundation_implementation_plan.md](../plans/2026-07-22_phase0_foundation_implementation_plan.md)、[../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md) |
| 关联决策 | [../decisions/2026-07-22_phase0_repo_foundation.md](../decisions/2026-07-22_phase0_repo_foundation.md) |
| 变更摘要 | 肯定 docs 为 source of truth 与 MVP-A/B；要求 Phase 0 补 domain/config/fixtures/experiments/CI，禁止过早写 Scanner |

---

## 1. 结论

| 项 | 判断 |
|----|------|
| 架构思想 | 9/10 |
| 交易系统理解 | 9/10 |
| 工程规范 | 8/10 |
| 量化研究规范 | 7/10（缺 experiments / 实验治理） |
| 实现成熟度 | 1/10（尚无代码，预期中） |
| 是否可直接 Implement Phase 1 业务 | **否** |
| 下一步 | **Phase 0 Foundation**（domain + config + tests/CI + 边界文档） |

**总评：** 方向正确，且高于普通个人量化项目起点。当前最大价值是把「交易系统工程化」固化在 repo 结构中，而非代码量。不要急着写业务代码；先把仓库变成可长期演化的交易操作系统骨架。

---

## 2. 做对了什么（保留）

### 2.1 docs 作为 source of truth

分层：

```text
Decision → Specification → Implementation → Review
```

而不是代码写完再补 README。必须保留。

### 2.2 MVP slicing 已对齐

README / Plan v1.1 已采用：

```text
MVP-A Signal → MVP-B Risk+Paper
```

先证明信号系统是否有形态价值，再证明风险与执行能否把信号变成可交易结果。顺序正确。

### 2.3 定位与命名

`swingMomentum` 定位为 Swing Momentum 研究/交易系统，而非 AI Stock Predictor 或 Trading Bot。保持。

---

## 3. 缺失的骨架节点

当前像：

```text
交易宪法 → ？ → 代码
```

中间需补齐，避免日后变成 `data/ strategy/ utils/` 一团。

| # | 建议 | 说明 |
|---|------|------|
| 1 | 强化 ADR 纪律 | 已有 `docs/decisions/`；保持 ADR 格式与可检索命名，避免半年后重辩栈选型 |
| 2 | `src/smm/domain/` | 核心对象：Signal / Order / Position / Trade / RiskDecision；系统本质不是 DataFrame |
| 3 | `experiments/` | 假设 → config → result；禁止只在 notebook 里乱改参 |
| 4 | `configs/` | 参数属策略；禁止业务代码硬编码阈值 |
| 5 | `tests/fixtures/` | 突破成功/假突破/Risk-Off/跳空等；避免「正常数据跑通即完成」 |

另需明确 **System Boundary**（系统知道什么 / 不知道什么），防止滑向新闻情绪与「AI 预测机」。

---

## 4. 工程原则：不要过早写 Scanner

**错误顺序：** `find_stocks()` → 调指标 → 接 yfinance。

**正确顺序：**

1. Domain model  
2. Config loader  
3. Fake / Mock DataProvider + synthetic fixtures  
4. 再真实数据与 Scanner  

---

## 5. 对 Agent 的明确指令（本评审）

**不要：** 直接 “Implement Phase 1” 全量业务。

**要：** 先完成 **Phase 0 Foundation**，交付：

```text
Repository foundation
+ Domain model
+ Config system
+ Testing framework
+ CI
```

**Phase 0 明确不碰：** yfinance、scanner 业务规则、指标引擎实现。

Phase 0 完成后 repo 应类似：

```text
swingMomentum/
  src/smm/{domain,config,core,...}
  tests/fixtures/
  configs/
  experiments/   # 约定与占位即可
  docs/
```

然后再进入 Phase 1 的 M1+（数据与 Scanner）。

---

## 6. 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-22 | accepted | 骨架评审落盘；驱动 Phase 0 计划 |
