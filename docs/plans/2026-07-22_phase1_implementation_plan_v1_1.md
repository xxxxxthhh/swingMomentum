# Swing Momentum Scanner — Phase 1 实施计划 v1.1

| 字段 | 值 |
|------|-----|
| 文档类型 | plan |
| 状态 | approved |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0（参数以冻结 YAML 为准） |
| 关联规格 | [../specs/Personal_Trading_Constitution_and_Swing_Momentum_Spec_v2.md](../specs/Personal_Trading_Constitution_and_Swing_Momentum_Spec_v2.md) |
| 关联决策 | [../decisions/2026-07-21_phase1_scope_and_stack.md](../decisions/2026-07-21_phase1_scope_and_stack.md)、[../decisions/2026-07-22_phase1_mvp_slicing_v1_1.md](../decisions/2026-07-22_phase1_mvp_slicing_v1_1.md) |
| 关联评审 | [../reviews/2026-07-22_phase1_plan_review.md](../reviews/2026-07-22_phase1_plan_review.md) |
| 替代 | [2026-07-21_phase1_implementation_plan.md](./2026-07-21_phase1_implementation_plan.md)（v1.0，superseded） |
| 变更摘要 | 两阶段 MVP；Watchlist + Signal State Machine；Setup 极简；Fund 过滤化；Synthetic 前置 |

**决策冻结：** Python + SQLite/Parquet + 可插拔数据源；**MVP-A Signal → MVP-B Risk+Paper**；V1 Setup = 突破+量能；基本面优先作风险过滤。  
**范围：** 美股多头、日线、收盘后 Scanner + Shadow/Paper；**不**自动真实下单、不做空、无杠杆、无期权。

---

## 0. 相对 v1.0 的变更摘要

| 主题 | v1.0 | v1.1 |
|------|------|------|
| MVP 切片 | 一次打通至 Shadow（含 Risk） | **MVP-A** 信号/观察池/状态机 → **MVP-B** 风险/Paper |
| 流水线 | 过滤 → 信号 → 风险 | Universe → Filters → **Watchlist** → Trigger → Risk → Position |
| 信号模型 | 日快照为主 | **State Machine** + logical identity，禁止重复可执行信号 |
| Setup | 整理/紧度/量能收缩等 | **仅** 20 日突破 + rel_vol≥1.3（+ 可选延伸过滤） |
| 基本面 | TotalScore 中可缺席评分 | **过滤/闸门优先**；不作为 V1 主权重 |
| 测试 | 黄金 fixture 在 M3 | **Synthetic 从 M1 起**，MVP-A 门禁 |
| 人工 | 否决原则已有 | **`manual_decisions` 表**（MVP-B） |
| 研究 | notebooks 一句 | **Research Layer 约定**（只读） |

**保留不变：** Risk 独立且不可绕过；fail-closed；无前视（次日开盘成交）；Shadow→Paper→小资金；config/version 审计链。

---

## 1. 目标与成功标准

### 1.1 Phase 1 目标

建立可重复执行的流程：

> 收盘后数据更新 → 质量检查 → 市场状态 → 特征 → 硬过滤 → **观察池** → **触发与状态转移** →（MVP-B）风险检查与仓位 → 快照与日报 → 模拟持仓与出场

系统回答文档使命问题，并满足：可解释、可计算、可复现、可审计。

### 1.2 MVP-A 完成定义（Signal Engine）

| 能力 | 验收标准 |
|------|----------|
| 幂等日任务 | 同一 `as_of_date` + 同一 config/version 重跑，信号实体与状态一致 |
| 无重复刷单 | 同一 logical signal 在非终态下跨日为**续存/转移**，非每日新建可执行信号 |
| Watchlist + Trigger | 硬过滤通过但未突破者进观察池；突破+量能者 `triggered` |
| 状态可追溯 | 每次转移有 reason_code；日报区分新触发 / 续存 / 终态 |
| 硬过滤 | 文档 §16–§17 核心可计算；拒绝原因可追溯 |
| 审计 | 绑定 `strategy_version` + config_hash（+ git commit 若可用）+ 特征快照 |
| 合成门禁 | fixture：突破成功、量能不足拒绝、硬过滤拒绝、同 setup 多日不重复 |
| 日报 | 候选/观察池列表含核心字段；基本面可 N/A 或仅 filter 结果 |

**MVP-A 不要求：** 真实建仓、heat/板块拒仓执行、Paper 成交、完整熔断。

**MVP-A 观察窗口：** 建议 ≥20–30 个交易日信号回放（合成全覆盖 + 真实日 batch）；**不**将收益当作晋级证据。

### 1.3 MVP-B 完成定义（Risk + Paper → 可进 Stage A/B 治理）

| 能力 | 验收标准 |
|------|----------|
| 无前视成交 | 信号确认后 **下一交易日开盘** 模拟；不按信号日收盘价成交 |
| 风险闸门 | 市场状态、单笔风险、heat、板块/簇上限；Risk 不得被 Scanner 绕过 |
| Paper | 跳空取消、滑点模型、持仓出场状态机、R/MFE/MAE |
| 熔断 | 文档核心熔断规则可配置并触发停新仓 |
| Manual log | 人工 SKIP 可审计；不可人工加票 |
| 编排 | `run_daily` 支持 shadow（只计划）与 paper（建仓）模式 |

### 1.4 非目标（明确不做）

- 实盘自动下单、期权、做空、杠杆  
- 盘中/高频、黑箱 AI 直接买卖  
- 完整 Walk-Forward / 消融 / 敏感性（研究里程碑）  
- 完整 point-in-time 财报修订历史  
- 复杂 SetupScore（漂亮整理等）  
- 历史成分股正式回测结论（工程可用当前成分股，必须标注非正式）  

---

## 2. 架构映射

```text
config/                 # 冻结 V1 YAML + 策略版本
  market-data           # 行情拉取、日历、复权字段
  fundamental-data      # V1 精简：可选；优先事件/红旗过滤
  data-validation       # 缺失/异常；失败则停机
  feature-engine        # 收益、均线、ATR、相对量、排名
  market-regime         # Risk-On / Neutral / Risk-Off
  watchlist             # 硬过滤通过、未触发之观察池
  momentum-scanner      # 硬过滤 + V1 极简 Trigger
  signal-lifecycle      # State Machine + logical identity
  signal-ranking        # Mom / RS / Trend 等（Fund 不进主权重）
  risk-engine           # 独立：仓位、heat、板块/簇、熔断（MVP-B）
  paper-broker          # 模拟成交（MVP-B）
  portfolio-engine      # 持仓、出场状态机（MVP-B）
  performance-analytics # R、MFE/MAE（MVP-B）
  reporting             # 日报 / 周报
  audit-log             # 决策、否决、状态转移
  manual-decisions      # 人工 SKIP 日志（MVP-B）
```

**关键约束（code review 清单）：**

1. 风险引擎**不得**被 Scanner 绕过（MVP-B 起强制；架构上模块独立从第一天遵守）。  
2. 研究脚本 / notebook **不得**覆盖生产/执行库。  
3. 所有计算以 `as_of_date` 为边界。  
4. 任务幂等；失败默认 **fail-closed**。  
5. 人工只能否决、不能强行加候选；否决必记原因。  
6. 非终态 logical signal **不得**每日复制为新的可执行信号。  

### 2.1 Signal State Machine

```text
detected
  → watchlisted      # 趋势/RS 合格，未触发突破
  → triggered        # 收盘满足突破+量能
  → eligible         # 可交易检查通过（止损距离等）
  → risk_accepted | risk_rejected   # MVP-B
  → entered | cancelled             # MVP-B 次日开盘
  → active
  → exited | stopped | expired
```

实现要求：

- 持久化 `signal_id`（稳定）+ `state` + `setup_key`  
- `signal_transitions` 或等价审计事件  
- Watchlist 超时未触发 → `expired`（阈值 config，如 N 个交易日）  

### 2.2 Watchlist Layer

| 进入 Watchlist | 离开 / 转移 |
|----------------|-------------|
| 硬过滤全通过 | 触发突破 → `triggered` |
| 未满足 Trigger | 硬过滤失效 → `expired` 或移出并记原因 |
| | 超时 → `expired` |

---

## 3. 推荐技术底座

| 层 | 选择 | 说明 |
|----|------|------|
| 语言 | Python 3.11+ | 量化生态成熟 |
| 数值 | pandas / numpy | 日线截面与时间序列 |
| 存储 | Parquet + SQLite | 对齐规格 §52；含 signals / transitions / watchlist 视图或表 |
| 配置 | YAML + pydantic | 与规格第二十部分对齐，**以本 plan ADR 修订项为准** |
| 数据源 | `DataProvider`；默认免费源 | 预留 Polygon/Tiingo/Alpaca/IBKR |
| 任务 | CLI + 可选 cron | `run_daily --as-of YYYY-MM-DD [--mode shadow\|paper]` |
| 测试 | pytest + **synthetic fixtures 优先** | 再补真实日回归快照 |
| 版本 | SMM-V1.0.0 + config_hash + git commit | strategy_versions 表 |

**目录建议：**

```text
swingMomentum/
  README.md
  pyproject.toml
  configs/smm_v1_0_0.yaml
  src/smm/
    config/
    data/
    validation/
    features/
    regime/
    watchlist/
    scanner/
    signals/          # lifecycle + ranking
    risk/
    paper/
    portfolio/
    analytics/
    reporting/
    audit/
    cli/
  tests/
    fixtures/ohlcv/   # synthetic cases
  data/               # gitignore 大文件
  reports/
  notebooks/          # 仅研究；禁止写执行库
    README.md
    01_factor_analysis.ipynb      # 占位约定即可
    02_signal_review.ipynb
    03_strategy_comparison.ipynb
    04_trade_postmortem.ipynb
```

---

## 4. V1 规则落地清单

### 4.1 股票池与流动性

- 池：S&P 500 ∪ Nasdaq-100 去重；仅普通股  
- `price > 10`；`avg_dollar_volume_20d >= 20e6`  
- 数据完整；排除杠杆/反向 ETF、权证等  

### 4.2 市场状态（SPY 主，QQQ 可选）

| 状态 | 条件（V1） | 新仓（MVP-B） | 单笔风险 | 最大敞口 |
|------|------------|---------------|----------|----------|
| Risk-On | SPY > SMA50 & SMA200 且 SMA50 > SMA200 | 允许 | 0.50% | 100% |
| Neutral | 高于 200 但短期条件不全等 | 受限 | 0.25% | 50% |
| Risk-Off | SPY < SMA200 等 | 原则上禁止 | 0% | 0–25% |

MVP-A：必须计算并展示 regime；不强制拒单执行（无 Risk 模块时）。

### 4.3 硬过滤（全满足才进 Watchlist / 评分）

1. Close > SMA200  
2. Close > SMA50  
3. SMA50 > SMA200  
4. Return_63 > 0  
5. Return_126 > 0  
6. 距 52w 高 ≤ 15%  
7. 流动性门槛  
8. 数据完整  
9. 非禁止事件窗口（若有日历）  
10. 市场允许新增多头（MVP-B 强制执行；MVP-A 标注）  

### 4.4 评分（V1）

**方向（写入 YAML，禁止代码魔数）：**

```text
MomentumScore     = 0.20·Rank21 + 0.30·Rank63 + 0.50·Rank126
RelativeStrength  = 0.40·Rank(RS_SPY_63) + 0.40·Rank(RS_SPY_126) + 0.20·Rank(RS_Sector_63)
# V1 TotalScore：不以 Fundamental 为主驱动
TotalScore        = 权重化(Mom, RS, TrendQuality, Setup/TriggerQuality)
# Fundamental：filter pass/fail 或 red_flag；缺失 = missing，不填有利默认
```

建议起点（可在首次冻结 config 时微调，但冻结后变更需版本纪律）：

```text
Mom 50% / RS 30% / Trend+Trigger 20%
```

或保留多因子结构但 **Fund 权重 = 0** 且 filter 独立。

### 4.5 突破与入场（V1 极简 Trigger）

- `Close > max(High[-20:-1])`  
- `RelativeVolume >= 1.30`  
- 可选：距 20EMA ≤ 2.5 ATR  
- **不做**复杂整理形态编码  

**入场（MVP-B）：**

- 次日开盘成交；跳空 > 1 ATR 或止损距离失范 → **取消并记录**  
- 止损：`SetupLow - 0.2·ATR20`，且 `1.0–2.5 ATR`  
- 仓位：`floor(equity × risk_per_trade / unit_risk)`，再套单票 15%、heat 4%、板块 1.5%、簇 2%  

### 4.6 出场（MVP-B / Paper）

- 失效：初始止损 / 跌回突破区 / 异常  
- 时间：10 日后未达 +0.5R 且走弱 → 出  
- 追踪：V1 **收盘跌破 20EMA → 次日开盘出**  
- 禁止：固定止盈、向下移止损、V1 加仓  

### 4.7 熔断（MVP-B）

- 单日组合亏 > 4R → 次日暂停新仓  
- 回撤 > 6% → 单笔风险减半  
- 回撤 > 10% → 停新仓 + 审计  
- 数据/仓位不一致 → 立即停止可执行信号  

---

## 5. 数据模型（MVP 最小集）

**MVP-A 必须：**

- `instruments`（含 `risk_cluster` 可先占位）  
- `daily_prices`  
- `features`（as_of + strategy_version）  
- `signals`（logical id、state、setup_key、reason_codes）  
- `signal_transitions`（或并入 audit_events）  
- `strategy_versions`  
- `audit_events`  

**MVP-B 必须：**

- `paper_orders` / `paper_positions` / `trades`  
- `manual_decisions`  

**可延后：**

- 完整 `fundamentals`；`earnings_calendar` 有则优先服务事件过滤  

价格：研究用 adjusted 算收益/均线/动量；模拟成交与止损用**可交易未复权价**（规格 §12.1）；provider 限制写入风险说明。

---

## 6. 实施里程碑

### M0 — 项目骨架与配置（0.5–1 天）

- 仓库结构、`pyproject.toml`、lint/test 骨架  
- 冻结 `configs/smm_v1_0_0.yaml`（含 state 超时、Trigger、评分权重、fund_as_filter）  
- `StrategyVersion`：version + config_hash  
- `notebooks/README.md` 研究层约定  
- 根 README：使命、非目标、如何跑一日任务  

**验证：** pytest 绿；非法 config 被拒绝。

---

### M1 — 市场数据、校验与 Synthetic 基建（2–3 天）

- `DataProvider`：`get_universe` / `get_daily_bars` / `get_calendar`  
- 默认免费 provider + Parquet 缓存  
- 校验 fail-closed  
- **Synthetic OHLCV fixtures** 与加载工具  
- CLI：`smm ingest --as-of ...`  

**验证：** 缓存幂等；坏数据停机；合成数据可被 feature/scanner 测试加载。

---

### M2 — 特征引擎 + 市场状态（2–3 天）

- SMA50/200、EMA20、ATR20、Return 21/63/126、52w high、dollar volume  
- 横截面排名；RS vs SPY；板块 RS 可缺失标记  
- Regime 与参数表  

**验证：** 手算样本；regime 边界用例。

---

### M3 — Watchlist + 极简 Scanner + State Machine（3–4 天）— **MVP-A 核心**

- 硬过滤 + reason codes  
- Watchlist 维护  
- Trigger：20 日突破 + rel_vol  
- State machine 转移与幂等 logical identity  
- 子分 + TotalScore（V1 权重）；Fund = filter  
- 写出 signals + transitions + 特征快照  

**验证：** 合成案例全绿；同 setup 连续日不重复可执行信号；量能不足/硬过滤拒绝。

---

### M4 — 信号日报与 MVP-A 日任务（1–2 天）— **MVP-A 交付**

- 编排：ingest → validate → features → regime → watchlist/scan → lifecycle → report  
- 日报 Markdown/CSV：新触发 / 观察池 / 状态变化  
- Shadow 语义（无建仓）  

**验证：** N 日 batch 回放可复现；人工可读。

**→ MVP-A 观察窗口：≥20–30 交易日信号形态检查（非正式 alpha 结论）**

---

### M5 — 风险引擎 + 计划仓位（2 天）— **MVP-B 起**

- 输入：候选 `eligible` + 组合状态 → 接受/拒绝 + 仓位  
- heat / 单票 / 板块 / risk_cluster；Kill Switch 配置位  

**验证：** heat 触顶拒仓；同簇超限；Risk-Off 拒新仓。

---

### M6 — Paper Broker + 持仓出场 + Manual Log（3–4 天）

- 次日开盘 + 滑点；跳空取消  
- 持仓状态机与 `active`→终态  
- `manual_decisions`  
- 熔断  

**验证：** 跳空穿止损、取消入场、EMA 退出、时间止损、幂等下单、人工 SKIP。

---

### M7 — 完整 Shadow/Paper 编排与周报骨架（1–2 天）— **MVP-B 交付**

- `run_daily --mode shadow|paper`  
- 周报复盘字段汇总（规格 §55 子集）  
- 风险簇种子表  

**→ Stage A Shadow 治理窗口（规格）；Paper 后 Stage B 门槛仍适用**

---

### M8 — 研究回测骨架（MVP-B 后）

- 同源信号/风险/成交逻辑  
- 基准：SPY、等权池、简单 20 日突破、6 月动量  
- 敏感性/消融/WF 另 PR  

---

## 7. PR / 工作包切分

| ID | 标题 | 依赖 | 交付 |
|----|------|------|------|
| PR1 | 骨架 + V1 config + CLI | — | 可安装包、config 校验 |
| PR2 | DataProvider + validation + **synthetic fixtures** | PR1 | ingest + 测试数据 |
| PR3 | features + regime | PR2 | 特征/Parquet |
| PR4 | watchlist + scanner + **state machine** + ranking | PR3 | 信号生命周期 |
| PR5 | signal reporting + run_daily（无 Risk） | PR4 | **MVP-A** |
| PR6 | risk-engine + sizing | PR5 | 计划仓位 |
| PR7 | paper + exits + circuit + manual_decisions | PR6 | Paper |
| PR8 | full orchestration + weekly report | PR7 | **MVP-B** |
| PR9 | analytics + backtest harness | PR8 | 研究入口 |
| PR10 | 敏感性/消融/Walk-Forward | PR9 | 正式研究门槛 |

---

## 8. 测试策略（最低集）

1. **单元：** 排名、突破窗口不含当日、状态转移合法性、regime 分支  
2. **属性/不变式：** 非终态不重复可执行信号；止损永不下移（MVP-B）；Risk-Off 无新仓（MVP-B）；幂等 run  
3. **黄金 synthetic：** 突破成功 / 假突破 / Risk-Off / 多日同 setup  
4. **回归：** 固定 as_of 集合的 signal 状态哈希（版本变更时显式更新）  
5. **禁止：** 用未来 bar 算信号日指标  

---

## 9. 验证阶段与晋级门槛

```text
M0–M4 完成（MVP-A）
    → 信号观察窗口（20–30 交易日回放 + 可持续日更）
    → M5–M7（MVP-B）
    → Stage A Shadow（规格 ≥4 周等）
    → Paper 深度验证
    → Stage B（≥6 月或 ≥60 笔等治理门槛）
    → Stage C 小资金人工执行（非本 Phase 自动下单范围）
```

**不设单一收益率门槛**；要求可解释、可复现、成本后方向合理、非极少数交易贡献全部、参数邻域不崩、无严重违规人工操作。

---

## 10. 风险与开放项

| 风险 | 缓解 |
|------|------|
| 免费数据质量 | validation fail-closed；provider 限制文档化 |
| 幸存者偏差 | 正式结论前 disclaimer |
| setup_key 定义不当导致错误合并/拆分信号 | 单测固定锚点规则；版本化 |
| 规格全文 vs V1 瘦身误解 | ADR + config 为执行边界；README 指向 v1.1 |
| 两套逻辑（回测 vs 日更） | 共用 scanner/lifecycle/risk/paper 库 |

**暂不阻塞：**

- 默认免费 provider 具体包名（PR2 选定）  
- 日报 Markdown vs HTML（先 Markdown/CSV）  
- Watchlist 超时默认天数（写入 YAML 首次冻结）  

---

## 11. 建议执行顺序

1. **文档已就绪（本 v1.1 + ADR + 评审）**  
2. **PR1 → PR2 → PR3 → PR4 → PR5（MVP-A）**  
3. 信号观察窗口（不改执行 config 混统计）  
4. **PR6 → PR7 → PR8（MVP-B）**  
5. 研究轨 PR9+ 并行但隔离配置  

**编码启动顺序强调：**

1. 先 YAML 冻结  
2. 先 Synthetic  
3. 再真实数据  

---

## 12. 与交易宪法一致性检查（每 PR）

- [ ] 证据优先；无新闻情绪输入  
- [ ] 先风险后仓位（MVP-B；架构预留）  
- [ ] 无未来函数；次日成交（MVP-B）  
- [ ] 成本/滑点/跳空进入模拟（MVP-B）  
- [ ] 研究与执行配置分离  
- [ ] 版本号变更流程，不覆盖历史结果  
- [ ] 人工否决可审计；不可人工加票  
- [ ] 信号生命周期与 Watchlist 行为符合本 plan  

---

## 13. 总结

v1.1 在**不推翻交易系统架构**的前提下，把 Phase 1 改成可验证的两段交付：**先看清信号与观察池（MVP-A），再兑现风险与 Paper（MVP-B）**；用 **State Machine + Watchlist** 避免日更重复推荐；用 **极简 Trigger 与 Fund 过滤化** 保持 Momentum 身份与可测性。  
骨架（宪法、fail-closed、Risk 独立）不变；血肉（阈值与权重）一律 YAML 版本化。
