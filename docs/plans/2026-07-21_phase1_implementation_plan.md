# Swing Momentum Scanner — Phase 1 实施计划

| 字段 | 值 |
|------|-----|
| 文档类型 | plan |
| 状态 | superseded |
| 日期 | 2026-07-21 |
| 策略版本 | SMM-V1.0.0 |
| 关联规格 | [../specs/Personal_Trading_Constitution_and_Swing_Momentum_Spec_v2.md](../specs/Personal_Trading_Constitution_and_Swing_Momentum_Spec_v2.md) |
| 关联决策 | [../decisions/2026-07-21_phase1_scope_and_stack.md](../decisions/2026-07-21_phase1_scope_and_stack.md) |
| 替代文档 | [2026-07-22_phase1_implementation_plan_v1_1.md](./2026-07-22_phase1_implementation_plan_v1_1.md) |
| 变更摘要 | Phase 1 端到端 MVP 实施计划首版落盘；**已被 v1.1 替代** |

> **状态说明（2026-07-22）：** 本文档由 [Plan v1.1](./2026-07-22_phase1_implementation_plan_v1_1.md) 替代。  
> 实施请以 v1.1 为准。本文保留供审计；主要差异见 v1.1 §0 与 [评审](../reviews/2026-07-22_phase1_plan_review.md)。

**决策冻结：** Python + SQLite/Parquet + 可插拔数据源；端到端 MVP 优先；基本面 V1 精简（可缺席评分）  
**范围：** 美股多头、日线、收盘后 Scanner + Shadow/Paper 模拟；**不**自动真实下单、不做空、无杠杆、无期权

---

## 1. 目标与成功标准

### 1.1 Phase 1 目标

建立可重复执行的流程：

> 收盘后数据更新 → 质量检查 → 市场状态 → 特征/评分 → 突破信号 → 风险检查 → 入场/止损/仓位计划 → 快照与日报 →（后续）模拟持仓与出场

系统回答文档使命问题，并满足工程原则：可解释、可计算、可复现、可审计；风险引擎独立于 Scanner；数据缺失默认拒绝交易。

### 1.2 MVP 完成定义（可进入 Stage A: Shadow Mode）

| 能力 | 验收标准 |
|------|----------|
| 幂等日任务 | 同一 `as_of_date` + 同一 config/version 重复运行，信号与订单结果一致、无重复订单 |
| 无前视成交 | 信号日收盘确认后，**下一交易日开盘**模拟；不按信号日收盘价成交 |
| 硬过滤 + 评分 | 文档 §16–§23 核心规则可计算；`TotalScore` 排序；拒绝原因可追溯 |
| 风险闸门 | 市场状态、单笔风险、组合 heat、板块/风险簇上限、财报窗口（若有日历）生效 |
| 审计 | 每个信号绑定 `strategy_version` + config hash（+ git commit 若可用）+ 特征快照 |
| 日报 | 候选列表含文档 §57 核心字段（基本面分可标 N/A） |

### 1.3 非目标（明确不做）

- 实盘自动下单、期权、做空、杠杆  
- 盘中/高频、黑箱 AI 直接买卖  
- 完整 Walk-Forward / 消融 / 敏感性（MVP 之后的研究里程碑）  
- 完整 point-in-time 财报修订历史（V1 精简）  
- 历史成分股正式回测结论（工程测试可用当前成分股，但必须标注不可作正式策略结论）

---

## 2. 架构映射（文档 §51 → 工程模块）

```text
config/                 # 冻结 V1 YAML + 策略版本
  market-data           # 行情拉取、日历、复权字段
  fundamental-data      # V1 精简：可选字段 + 财报日（若可得）
  data-validation       # 缺失/异常/重复检查；失败则停机
  feature-engine        # 收益、均线、ATR、相对量、排名
  market-regime         # Risk-On / Neutral / Risk-Off
  momentum-scanner      # 硬过滤 + Setup/突破
  signal-ranking        # 各子分 + TotalScore
  risk-engine           # 独立：仓位、heat、板块/簇、熔断
  paper-broker          # 模拟成交、滑点、跳空取消
  portfolio-engine      # 持仓、出场状态机
  performance-analytics # R 倍数、MFE/MAE（Paper 阶段）
  reporting             # 日报 / 周报
  audit-log             # 不可静默删除的决策与否决记录
```

**关键约束（写入实现与 code review 清单）：**

1. 风险引擎**不得**被 Scanner 绕过。  
2. 研究脚本不得覆盖生产/执行库。  
3. 所有计算以 `as_of_date` 为边界。  
4. 任务幂等；失败默认 **fail-closed**（停止可执行信号）。  
5. 人工只能否决、不能强行加候选；否决必记原因。

---

## 3. 推荐技术底座

| 层 | 选择 | 说明 |
|----|------|------|
| 语言 | Python 3.11+ | 量化生态成熟 |
| 数值 | pandas / numpy | 日线截面与时间序列 |
| 存储 | Parquet（价格/特征）+ SQLite（信号/订单/持仓/版本） | 个人规模足够；表结构对齐文档 §52 |
| 配置 | YAML（冻结 V1 参数）+ pydantic 校验 | 与文档第二十部分一致 |
| 数据源 | 可插拔 `DataProvider` 接口；默认优先 yfinance 或同类免费源，预留 Polygon/Tiingo | 成分股、退市、基本面能力因源而异，文档化限制 |
| 任务 | CLI（`typer`/`click`）+ 可选 cron | `run_daily --as-of YYYY-MM-DD` |
| 测试 | pytest + 固定样本 fixture | 幂等、无前视、仓位公式单测 |
| 版本 | `SMM-V1.0.0` + config_hash + git commit | 策略版本表 |

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
    scanner/
    ranking/
    risk/
    paper/
    portfolio/
    analytics/
    reporting/
    audit/
    cli/
  tests/
  data/                 # gitignore 大文件；schema 与样本可入仓
  reports/
  notebooks/            # 仅研究；禁止写执行库
```

---

## 4. V1 规则落地清单（实现对照）

### 4.1 股票池与流动性

- 池：S&P 500 ∪ Nasdaq-100 去重；仅普通股  
- `price > 10`；`avg_dollar_volume_20d >= 20e6`  
- 数据完整；排除杠杆/反向 ETF、权证等

### 4.2 市场状态（SPY 主，QQQ 可选）

| 状态 | 条件（V1） | 新仓 | 单笔风险 | 最大敞口 |
|------|------------|------|----------|----------|
| Risk-On | SPY > SMA50 & SMA200 且 SMA50 > SMA200 | 允许 | 0.50% | 100% |
| Neutral | 高于 200 但短期条件不全等 | 受限 | 0.25% | 50% |
| Risk-Off | SPY < SMA200 等 | 原则上禁止 | 0% | 0–25% |

### 4.3 硬过滤（全满足才评分）

1. Close > SMA200  
2. Close > SMA50  
3. SMA50 > SMA200  
4. Return_63 > 0  
5. Return_126 > 0  
6. 距 52w 高 ≤ 15%  
7. 流动性门槛  
8. 数据完整  
9. 非禁止事件窗口  
10. 市场允许新增多头  

### 4.4 评分（V1 权重）

```text
MomentumScore     = 0.20·Rank21 + 0.30·Rank63 + 0.50·Rank126
RelativeStrength  = 0.40·Rank(RS_SPY_63) + 0.40·Rank(RS_SPY_126) + 0.20·Rank(RS_Sector_63)
TotalScore        = 0.30·Mom + 0.20·RS + 0.15·TrendQ + 0.15·Fund + 0.20·Setup
```

**V1 精简基本面：** `FundamentalScore` 在数据缺失时标记 `missing`，**不**填 0 或有利默认值；TotalScore 可对缺失做降权或单独展示（实现时选一种并写进 config，禁止静默填优）。

### 4.5 突破与入场

- 收盘突破：`Close > max(High[-20:-1])`（不含当日）  
- `RelativeVolume >= 1.30`  
- 延伸过滤：距 20EMA ≤ 2.5 ATR（文档建议）  
- 次日开盘成交；跳空 > 1 ATR 或止损距离失范 → **取消并记录**  
- 止损：`SetupLow - 0.2·ATR20`，且 `1.0–2.5 ATR` 止损距离  
- 仓位：`floor(equity × risk_per_trade / unit_risk)`，再套单票 15%、heat 4%、板块 1.5%、簇 2%  

### 4.6 出场（Paper 阶段）

- 失效：初始止损 / 跌回突破区 / 异常  
- 时间：10 日后未达 +0.5R 且走弱 → 出  
- 追踪：主规则 **收盘跌破 20EMA → 次日开盘出**（V1 只选一种）  
- 禁止：固定止盈目标、向下移止损、V1 加仓  

### 4.7 熔断（Paper 起强制）

- 单日组合亏 > 4R → 次日暂停新仓  
- 回撤 > 6% → 单笔风险减半  
- 回撤 > 10% → 停新仓 + 审计  
- 数据/仓位不一致 → 立即停止可执行信号  

---

## 5. 数据模型（对齐 §52，MVP 最小集）

**必须先落地：**

- `instruments`（含 `risk_cluster` 人工标签表）  
- `daily_prices`  
- `features`（按 as_of + strategy_version）  
- `signals`（含 reason_codes / rejected_reason）  
- `strategy_versions`  
- `audit_events`  

**Shadow 可空跑，Paper 必须：**

- `paper_orders` / `paper_positions` / `trades`  

**V1 精简：**

- `fundamentals` 可延后；若有 earnings date 可先单表 `earnings_calendar`  

价格：研究用 adjusted 算收益/均线/动量；模拟成交与止损用**可交易未复权价**（文档 §12.1）——数据源若只提供 adj，需在 provider 层明确限制并写入风险说明。

---

## 6. 实施里程碑（端到端 MVP 优先）

### M0 — 项目骨架与配置（0.5–1 天）

- 仓库结构、`pyproject.toml`、lint/test 骨架  
- 冻结 `configs/smm_v1_0_0.yaml`（文档第二十部分）  
- `StrategyVersion` 加载：version + config_hash  
- README：使命、非目标、如何跑一日任务  

**验证：** `pytest` 绿；config 非法值被拒绝。

---

### M1 — 市场数据与校验（2–3 天）

- `DataProvider` 接口：`get_universe` / `get_daily_bars` / `get_calendar`  
- 默认 provider（免费行情）+ 本地 Parquet 缓存  
- 拉取 SPY/QQQ + 股票池；交易日历  
- 校验：缺失日、重复、≤0 价、异常跳变、成交量异常  
- CLI：`smm ingest --as-of ...`；失败 fail-closed  

**验证：** 固定日期重跑缓存一致；故意坏数据触发停止。

**已知限制文档化：** 当前成分股 ≠ 历史成分股；不作正式回测结论。

---

### M2 — 特征引擎 + 市场状态（2–3 天）

- SMA50/200、EMA20、ATR20、Return 21/63/126、52w high、dollar volume  
- 横截面百分位排名（仅当日存活池）  
- RS vs SPY；板块 RS：V1 可用 GICS sector ETF 映射表（缺映射则 RS_Sector 子项标记缺失）  
- Regime 计算与风险参数表  

**验证：** 手算 1–2 只股票特征；regime 边界用例（恰在均线上/下）。

---

### M3 — Scanner + 排名 + 信号快照（3–4 天）— **MVP 核心**

- 硬过滤流水线 + reason codes  
- Setup 结构（整理 5–20 日、Tightness、量能收缩、延伸过滤）— 先实现可测的明确规则，模糊项用 config 阈值  
- 突破信号 §23  
- 子分 + TotalScore；基本面分 V1：`optional` / `missing`  
- 写出 `signals` + 特征快照  
- 幂等：同日重跑更新同一 logical key，不产生重复 signal_id 业务重复  

**验证：** 黄金样本日 fixture（人造 OHLCV）覆盖：通过突破、量能不足拒绝、硬过滤拒绝、延伸拒绝。

---

### M4 — 风险引擎 + 计划仓位（2 天）

- 独立模块：输入「候选信号 + 当前组合状态」→ 接受/拒绝 + 仓位  
- 止损距离检查、unit risk（含费用/滑点假设 config）  
- heat / 单票资本 / 板块 / risk_cluster  
- Kill Switch 配置位  

**验证：** heat 触顶拒新仓；同簇超限拒仓；Risk-Off 拒新仓。

---

### M5 — 日报与 Shadow Mode 日任务（1–2 天）— **MVP 交付点**

- 编排 `run_daily`：ingest → validate → features → regime → positions(noop) → scan → risk → report  
- 日报：Markdown/CSV（§57 核心字段）  
- Shadow：只信号与计划，**不**建仓  
- 日志与 audit  

**验证：** 连续对 N 个历史交易日 batch 回放；结果可复现；人工可读日报。

**→ 进入文档 Stage A：建议 Shadow ≥ 4 周（可用历史回放加速工程验证，真实日历 Shadow 仍建议保留）**

---

### M6 — Paper Broker + 持仓出场（3–4 天）

- 次日开盘成交 + 滑点/价差模型  
- 跳空取消规则  
- 持仓状态机：止损（跳空按可成交价）、时间止损、20EMA 退出  
- `paper_orders` / `positions` / `trades`；R、MFE、MAE  
- 熔断规则  

**验证：** 场景测试——跳空穿止损、跳空取消入场、EMA 退出次日开盘、时间止损、幂等不下双份单。

**→ Stage B：≥ 6 个月或 ≥ 60 笔平仓后再评估（治理门槛）**

---

### M7 — 研究回测骨架（MVP 后，2–4 天起）

- 事件驱动或日终回放引擎（复用同一信号/风险/成交逻辑，禁止两套规则）  
- 成本与滑点压力开关  
- 基准：SPY、等权池、简单 20 日突破、6 月动量（文档 §42）  
- 后续再拆 PR：敏感性、消融、Walk-Forward  

**验证：** 回测与 paper 对同一历史窗口规则一致（允许实现细节文档化差异，但信号定义必须同源）。

---

### M8 — 运维与治理（贯穿 / 收尾）

- 周报复盘清单（§55）字段自动汇总  
- 策略版本变更流程模板（修改八问 §3）  
- 备份：SQLite + Parquet 复制脚本  
- 风险簇种子表（半导体、AI 基建、加密相关等）  

---

## 7. PR / 工作包切分（建议顺序）

| ID | 标题 | 依赖 | 交付 |
|----|------|------|------|
| PR1 | 骨架 + V1 config + CLI 入口 | — | 可安装包、config 校验 |
| PR2 | DataProvider + 缓存 + validation | PR1 | ingest 可用 |
| PR3 | features + regime | PR2 | 特征表/Parquet |
| PR4 | scanner + ranking + signals | PR3 | 信号快照 |
| PR5 | risk-engine + sizing | PR4 | 计划仓位与拒绝原因 |
| PR6 | reporting + run_daily Shadow | PR5 | **MVP** |
| PR7 | paper broker + exits + circuit breakers | PR6 | Paper 模式 |
| PR8 | analytics + weekly report | PR7 | R 统计与周报 |
| PR9 | backtest harness + benchmarks | PR7 | 研究入口 |
| PR10 | 敏感性/消融/Walk-Forward | PR9 | 正式研究门槛 |

并行机会：PR5 可与 PR4 后期接口约定后部分并行；PR8 可与 PR9 并行。

---

## 8. 测试策略（最低集）

1. **单元：** 排名、仓位 floor、止损距离、regime 分支、突破窗口不含当日  
2. **属性/不变式：** 止损永不下移；Risk-Off 无新仓；幂等 run  
3. **黄金 fixture：** 合成 OHLCV 覆盖通过/拒绝路径  
4. **回归：** 固定 `as_of` 集合的 signal 哈希快照（策略版本变更时显式更新）  
5. **禁止：** 测试里用未来 bar 算信号日指标  

---

## 9. 验证阶段与晋级门槛（文档对齐）

```text
M0–M5 完成
    → Stage A Shadow（≥4 周或等价历史回放 + 真实日更）
    → M6 Paper
    → Stage B（≥6 月或 ≥60 笔）
    → 策略/工程/行为门槛（§48–50）
    → Stage C 小资金人工执行（非本 MVP 代码范围）
```

**不设单一收益率门槛**；要求：计入成本后期望方向合理、非极少数交易贡献全部收益、参数邻域不崩、回撤可承受、模拟与回测方向一致、无严重违规人工操作。

---

## 10. 风险与开放项

| 风险 | 缓解 |
|------|------|
| 免费数据质量/复权/停牌不全 | validation fail-closed；文档化 provider 限制；预留付费 provider |
| 无历史成分股 → 幸存者偏差 | 正式结论前不宣称；回测报告强制 disclaimer |
| Setup「整理形态」规则模糊 | 先实现可编码代理指标 + config；避免主观图形 |
| 板块 ETF / risk_cluster 不全 | 种子表 + 未知簇单独限额或拒绝 |
| 基本面缺失 | 标记 missing，不填有利默认；TotalScore 规则写死在 config |
| 两套逻辑（回测 vs 日更） | 强制共用 scanner/risk/paper 核心库 |

**暂不阻塞实现的决策（有默认即可）：**

- 默认免费 provider 具体包名（实现 PR2 时选定并写适配器）  
- 日报格式 Markdown vs HTML（先 Markdown/CSV）  
- TrendQualityScore / SetupScore 内部细项权重（实现时落 YAML，版本化）

---

## 11. 建议执行顺序（你确认计划后）

1. **立即：** PR1 → PR2 → PR3 → PR4 → PR5 → PR6（打通 Shadow MVP）  
2. **稳定后：** 用历史交易日 batch 回放 1–2 年做工程与信号稳定性检查（非正式策略结论）  
3. **再：** PR7 Paper → 进入治理意义上的模拟周期  
4. **研究轨并行：** PR9 起回测与基准，**不得**热改执行中 config  

---

## 12. 与交易宪法的一致性检查（实现中每 PR）

- [ ] 证据优先；无新闻情绪输入  
- [ ] 先风险后仓位  
- [ ] 无未来函数；次日成交  
- [ ] 成本/滑点/跳空进入模拟  
- [ ] 研究与执行配置分离  
- [ ] 版本号变更流程，不覆盖历史结果  
- [ ] 人工否决可审计；不可人工加票  

---

## 13. 总结

本计划把规格文档落成 **可运行的个人量化工程**：先用 Python + 本地存储 + 可插拔行情打通 **日更 Scanner + 风险计划 + 审计日报（Shadow MVP）**，再叠加 **Paper 成交与出场**，最后才是完整研究回测与晋级门槛。骨架（宪法与风险纪律）固化在 config 与独立 risk 模块中；血肉（阈值与权重）一律版本化，禁止中途混统计。
