# ADR：Phase 1 MVP 切片、Watchlist、信号生命周期与 V1 规则边界

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | accepted |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0（执行参数以冻结 config 为准；见 §6） |
| 关联规格 | [../specs/Personal_Trading_Constitution_and_Swing_Momentum_Spec_v2.md](../specs/Personal_Trading_Constitution_and_Swing_Momentum_Spec_v2.md) |
| 关联评审 | [../reviews/2026-07-22_phase1_plan_review.md](../reviews/2026-07-22_phase1_plan_review.md) |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md) |
| 部分替代 | [2026-07-21_phase1_scope_and_stack.md](./2026-07-21_phase1_scope_and_stack.md) 的 **§2 实施优先级** 与 **§3 基本面落地方式** |
| 变更摘要 | 两阶段 MVP；Watchlist + Signal State Machine；Fund 过滤化；Setup 极简 |

---

## 背景

[2026-07-21 ADR](./2026-07-21_phase1_scope_and_stack.md) 冻结了技术栈，并采用「端到端 MVP：数据 → Scanner → 风险 → 日报 → Shadow，再 Paper」。

[Plan 评审](../reviews/2026-07-22_phase1_plan_review.md) 确认架构方向正确，但指出：

1. 端到端切片对**信号是否有形态优势**的验证过晚。  
2. 缺少信号生命周期与观察池，日更易重复刷同一 setup。  
3. Setup / 基本面评分在 V1 过重，易过拟合或与动量本质冲突。

需要在编码前冻结新的实施切片与 V1 规则边界。

---

## 决策

### 1. 技术栈（不变）

沿用 2026-07-21 ADR：

- Python 3.11+  
- Parquet（价格/特征）+ SQLite（信号/订单/持仓/版本/审计）  
- 可插拔 `DataProvider`；Phase 1 可用免费行情  
- YAML + 校验；CLI `run_daily --as-of`  
- **不**自动真实下单  

### 2. 实施优先级（**替代** 2026-07-21 §2）

**采用：两阶段 MVP。**

| 阶段 | 名称 | 回答的问题 | 范围 |
|------|------|------------|------|
| **MVP-A** | Signal Engine | 策略每天产生什么信号/观察池？ | Data → Validate → Features → Regime → Hard filters → Watchlist → Trigger → Signal report + State machine |
| **MVP-B** | Risk + Paper | 如何下注、管仓、出场？ | + Risk Engine → Paper Broker → Portfolio exits → Circuit breakers → Manual decisions |

**顺序原则：**

1. 先证明信号可复现、可解释、不重复刷单（MVP-A）。  
2. 再证明风险闸门、成交模型与出场状态机（MVP-B）。  
3. 完整回测 harness / 敏感性 / 消融 / Walk-Forward 仍在 MVP-B 之后，且规则必须与日更同源。

**不采用：**

- 在 MVP-A 完成并经短窗口信号观察前，完整建设 Risk + Paper。  
- 在出现可复现信号快照前，先完整建设全部研究验证设施。

### 3. 流水线层（新增）

**采用：**

```text
Universe
  → Hard Filters
  → Watchlist          # 强趋势/高 RS，尚未触发
  → Trigger            # 突破 + 量能（V1 极简）
  → Eligible Plan      # 止损距离等可交易草稿
  → Risk Engine        # MVP-B；不得被 Scanner 绕过
  → Shadow 计划 / Paper 下单
```

Watchlist 为**一等数据产物**，不是日报里的附注。

### 4. Signal State Machine（新增）

**采用**显式状态机（逻辑名，实现可用枚举 + 转移表）：

```text
detected → watchlisted → triggered → eligible
  → risk_accepted | risk_rejected
  → entered | cancelled
  → active → exited | stopped | expired
```

**工程约束：**

1. 同一 logical signal identity（建议：`symbol + setup_key + strategy_version`，`setup_key` 由突破锚点/窗口定义）在未达终态前，**不得**每日新建「可执行新信号」。  
2. 日报必须区分：**新触发** / **观察池续存** / **持仓中** / **终态**。  
3. 每次状态转移写入审计（至少：as_of、from、to、reason_code）。  
4. 幂等：同日同 config 重跑不产生重复业务实体。

终态示例：`exited`、`stopped`、`expired`、`cancelled`、`risk_rejected`（是否允许日后同标的新 setup 另开 identity，由 setup_key 定义）。

### 5. Setup 规则（V1 边界）

**V1 仅实现：**

1. 收盘突破：`close > max(high[-20:-1])`（不含当日）  
2. 量能：`volume / avg_volume_20 >= 1.3`（阈值 config 化）  

**可选（config 开关，默认按规格建议开启或关闭须在 YAML 写死）：**

- 延伸过滤：距 20EMA ≤ 2.5 ATR  

**V1 明确不做（research backlog）：**

- 「漂亮整理」主观形态  
- 整理 5–20 日 / Tightness / 量能收缩等复杂 SetupScore 细项  

### 6. 基本面（**修订** 2026-07-21 §3 落地方式）

**采用：V1 基本面作风险过滤，不作为 TotalScore 主驱动力。**

- 默认路径：明显财务红旗 / 策略要求的数据不可用 → **拒绝或降级**，原因码可审计。  
- **禁止**缺失静默填 0 或有利默认值。  
- TotalScore V1 以动量、相对强度、趋势/触发质量为主；具体权重写入冻结 YAML。  
- 完整 point-in-time 财报与评分体系仍不作为 Phase 1 阻塞项。  
- 若后续恢复 Fundamental 为评分项，须新 decision + 评估策略版本 bump。

**与规格关系：** 规格正文中的 `0.15 × FundamentalScore` 等权重，在 V1 实现期以 **冻结 config + 本 ADR** 为执行边界；规格大修订可另开版本，避免中途混统计。

### 7. Manual Decision Log（新增，MVP-B）

**采用**表/事件流 `manual_decisions`：

- 记录：signal_id（或 logical id）、as_of、decision（如 SKIP）、reason、operator  
- **人工只能否决，不能强行加候选**（与宪法一致）  
- 用于区分系统错误 vs 人工干预  

### 8. Notebook Research Layer（新增约定）

**采用：**

- `notebooks/` 仅分析、可视化、实验  
- **禁止** notebook 写入执行库（SQLite 信号/订单/持仓）或覆盖生产 Parquet 契约路径  
- 研究改参结果不得与执行中 Shadow/Paper 混统计  

### 9. Synthetic Fixtures（新增，MVP-A 门禁）

**采用：** 在接入大规模真实行情验收前，必须有合成 OHLCV fixture，至少覆盖：

| 案例 | 期望 |
|------|------|
| 突破成功路径 | → `triggered` / `eligible` |
| 假突破 | 不进入可执行，或进入后按规则 `stopped`（MVP-B） |
| Risk-Off | 可有观察/触发快照，但无新仓 / 无 `risk_accepted`（MVP-B 强制；MVP-A 至少标记 regime） |
| 同 setup 连续多日 | 状态续存，不重复新建可执行信号 |

### 10. 数据源（澄清，不变）

- Phase 1 价格可用 yfinance 或同类免费源。  
- 接口保持 `DataProvider`，预留 Polygon / Tiingo / Alpaca / IBKR。  
- 复权、退市、成分股历史等限制必须文档化；正式策略结论前强制 disclaimer。

---

## 理由

1. **先 Alpha 形态，后下注工程** — 若信号本身无稳定、可解释输出，Risk/Paper 工程无法回答「系统有没有边」。  
2. **状态机 + Watchlist** — 真实系统不是每日全量重扫描当新单；避免重复推荐与审计混乱。  
3. **Setup / Fund 瘦身** — 降低主观与数据滞后风险，保持 Momentum 身份。  
4. **合成数据前置** — 逻辑正确性不依赖外部 API 稳定性。  
5. **栈与 fail-closed 不变** — 仅改切片与 V1 规则边界，不推翻交易系统设计。

---

## 后果

### 正面

- 更快看到「每天到底产出什么」  
- 信号身份与生命周期可测、可审计  
- V1 规则更少、更可辩护  

### 代价 / 风险

- Shadow 完整「计划仓位」体验略晚于原端到端 MVP（可接受：先信号后风险）  
- 与规格文档中完整 SetupScore / FundamentalScore 表述需靠 config + ADR 对齐，避免读者以为未实现项是 bug  
- 策略版本：仅工程切片不变版本号；**若 YAML 中 TotalScore 权重相对规格冻结块永久偏离**，应在首次冻结 config 时记录 hash，并在晋级评审中引用本 ADR  

### 后续必须遵守

- 恢复复杂 Setup 或 Fund 评分 → 新 decision + 评估 bump 版本  
- 改变 MVP-A/B 边界 → 更新 plan 并评估是否 supersede 本 ADR  
- 执行中模拟不得与研究改参混统计  

---

## 备选方案（未采纳）

| 方案 | 未采纳原因 |
|------|------------|
| 维持端到端 MVP 一次打到 Shadow 含 Risk | 信号未验证前风险工程投入偏早 |
| V1 即完整 SetupScore + Fund 评分 | 过拟合与数据质量风险高 |
| 无状态机、每日全新 signal 行 | 重复推荐与持仓语义混乱 |
| 无 Watchlist、过滤后直接 Trigger 列表 | 丢失「强但未破」的可运营层 |

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-22 | accepted | 评审确认后落盘；指导 Plan v1.1 |
