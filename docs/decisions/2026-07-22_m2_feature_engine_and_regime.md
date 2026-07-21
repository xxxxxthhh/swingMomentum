# ADR：M2 特征引擎、板块基准、缺失语义与市场状态

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | proposed |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0（不 bump；新增 config 键仅改变 `config_hash`，见 [M1 ADR §2.4](./2026-07-22_m1_data_provider_and_universe.md#24-版本纪律划界经-pr-1-确认)） |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（§14 市场状态、§17 动量、§18 相对强度、§19 趋势质量） |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)（M2 / PR3） |
| 关联决策 | [2026-07-22_m1_data_provider_and_universe.md](./2026-07-22_m1_data_provider_and_universe.md)（M1，accepted rev.4） |
| 关联 issue | [#3](https://github.com/xxxxxthhh/swingMomentum/issues/3) 生产股票池（**本里程碑硬前置**） |
| 变更摘要 | 冻结 M2 四项决策：板块基准用 ETF、缺失整体传递、历史不足排除而非停机、特征计算用标准库 |

---

## 背景

M1 已交付行情接入、§12.4 校验、Parquet 缓存与确定性生成器。按 Plan v1.1 §6，M2 交付**特征引擎 + 市场状态**：SMA50/200、EMA20、ATR20、Return 21/63/126、52 周高点、美元成交额、横截面排名、RS、以及 Risk-On / Neutral / Risk-Off。

M1 ADR 遗留一个 M2 开放项（板块 RS 缺失处理）。核查宪法原文时又发现三处必须在写代码前定死的语义。

---

## 决策

### 1. 板块基准：`sector` 列 + 板块 ETF 收益

> ⚠️ **本节纠正一处提问措辞错误。** 决策提问时曾把「快照加 `sector` 列」描述为「板块 RS 用同板块个股的横截面中位数作基准」。**该基准定义与宪法冲突**，不予采纳。

宪法 §18.2 明确规定：

```text
RS_Sector_63 = Return_63_stock - Return_63_sectorETF
```

板块基准是**板块 ETF 的真实收益**，不是合成的同板块中位数。宪法优先级高于实现便利，故：

| 层 | 来源 |
|----|------|
| 个股→板块归属 | 股票池快照新增 **`sector` 列**，与成分股同源同时间点 |
| 板块收益基准 | **板块 ETF 的真实行情**，经同一 `DataProvider` 与 §12.4 校验 |
| 归属→ETF 映射 | config 新增 `sector_benchmarks` 映射表 |

```yaml
sector_benchmarks:
  technology: XLK
  financials: XLF
  health_care: XLV
  consumer_discretionary: XLY
  consumer_staples: XLP
  energy: XLE
  industrials: XLI
  materials: XLB
  utilities: XLU
  real_estate: XLRE
  communication_services: XLC
```

**理由：** 合成中位数会让基准随股票池组成漂移，且无法与任何可交易标的对账；ETF 收益是真实发生的、可审计的。映射表进 config 而非代码，符合参数纪律。

**代价：** 日更需额外拉取 11 只 ETF。可接受——它们同时是 M2 之后板块暴露与簇约束的天然锚点。

### 2. 板块缺失：整体标记 missing 向下传递，**不重新归一化**

`sector` 缺失、映射不存在、或 ETF 数据未通过校验时：

- `RS_Sector_63` = **missing**（不是 0，不是中位数，不是省略）
- `RelativeStrengthScore` = **missing**，向下传递
- 该股**不进入候选**，记 reason code `rs_sector_missing`

**明确不采纳重新归一化**（把 0.40/0.40 归一为 0.50/0.50 继续打分）。理由：有板块与无板块的股票会得到**不同口径的分数**，却在同一张横截面排名表里比较——这会污染整个排名，而不只是影响那一只股票。它是「不填有利默认」的一种变相形式，违反宪法原则 1 与 §12.4 的缺失处理精神。

**代价：** 板块数据不全时候选会显著减少。这是**期望行为**——宁可少交易，不可用错口径排名。

### 3. 历史不足：排除该股并记录原因，**不停机**

| 情形 | 处置 |
|------|------|
| 数据**错误**（§12.4 任一项） | **fail-closed 停机**（M1 已实现，不变） |
| 数据**正确但不足**（如新上市不满 252 根 bar） | **排除该股**，reason code `insufficient_history`，其余股票正常运行 |

新上市不是坏数据，而是合法地不满足策略前提。把整个日更因为一只新成分股拖停，既不符合宪法意图，也会让 fail-closed 因为噪声过多而被人为绕过——那才是真正的风险。

**最小历史长度**由各特征的窗口决定：

| 特征 | 需要 bar 数 |
|------|------------|
| SMA200 | 200 |
| Return_126 | 127 |
| 52 周高点 | 252 |
| ATR20 / EMA20 / Return_21 / Return_63 | ≤ 64 |

取最大值 → **252**。写入 config：

```yaml
features:
  min_history_bars: 252
```

这是数据治理护栏（§2.4 划界），改变 `config_hash`，不 bump 策略版本。

### 4. 数值实现：标准库，**不引入 pandas/numpy**

> 这是对 [Plan v1.1 §3](../plans/2026-07-22_phase1_implementation_plan_v1_1.md) 技术栈表「数值：pandas / numpy」的**有意偏离**，请评审确认。

**理由：**

1. **数据量微不足道。** 600 标的 × 252 根 bar ≈ 15 万个 bar。纯 Python 的滚动窗口在这个规模上是毫秒级，pandas 带来的性能优势不存在。
2. **无前视更容易审计。** 显式的索引切片（`bars[t - 199 : t + 1]`）在 review 时一眼可验；pandas 的 `rolling` / `shift` 语义正确但需要额外一层推理，而无前视是本项目最不能出错的性质。
3. **缺失语义会打架。** §2 要求缺失作为**一等状态显式传递**。pandas 的 `NaN` 会在 `mean()` / `rank()` 等操作里被默默跳过或传播，行为取决于 `skipna` 默认值——这正是「静默填有利默认」最容易溜进来的地方。用 `float | None` 强制每个消费点显式处理。
4. **核心依赖更轻。** M1 已把 pyarrow 放进核心以保证无网 CI；再加 pandas + numpy 会让默认安装显著变重，收益不明。

**代价：** 横截面排名与滚动统计要手写约 150 行并配单测。可接受，且这些函数正是应当被逐行审的部分。

**若评审不同意**，改用 pandas 的成本集中在特征层内部，不影响本 ADR 其余决策。

---

## 特征清单（宪法 §17 / §18 / §19）

全部基于 **`AdjustedBar` 视图**（M1 §3.3 边界）：

| 组 | 特征 |
|----|------|
| 趋势 | SMA50、SMA200、EMA20、SMA50 斜率、SMA200 斜率 |
| 波动 | ATR20 |
| 动量 | Return_21 / 63 / 126 及其横截面百分位排名 |
| 位置 | 52 周高点、距 52 周高点百分比、63 日最大回撤 |
| 延伸 | 收盘距 EMA20 的 ATR 倍数 |
| 相对强度 | RS_SPY_63、RS_SPY_126、RS_Sector_63 及排名 |
| 流动性 | 20 日平均美元成交额 |
| 市场状态 | SPY regime（§14） |

**成交量口径：** 用 provider 原生 `volume` 序列。经 M1 实测（[M1 ADR §5.2](./2026-07-22_m1_data_provider_and_universe.md)），yfinance 的成交量已拆股调整，口径一致。

---

## 市场状态（宪法 §14）

| 状态 | V1 条件 |
|------|---------|
| Risk-On | SPY > SMA50 且 > SMA200 且 SMA50 > SMA200 |
| Neutral | 高于 SMA200 但短期条件不全 |
| Risk-Off | SPY < SMA200 |

**M2 只计算并展示，不强制拒单**——风险引擎属 MVP-B。SPY 数据缺失或校验失败时 regime = missing 并 fail-closed，**不得**默认为 Risk-On。

---

## 对 M2 实现的约束

1. 特征计算的唯一输入是 `AdjustedBar`；触碰原始 OHLC 即违反 M1 §3.3 边界（已由类型强制）。
2. `as_of` 的特征只允许消费 `date <= as_of` 的 bar。需有测试：截断序列后同一 `as_of` 的特征值不变。
3. 缺失一律为 `None` 并显式传递；**禁止** `or 0`、`fillna`、默认中位数等任何形式的填充。
4. 特征快照绑定 `as_of` + `strategy_version` + `config_hash` 持久化，供审计与复现。
5. 横截面排名以**当日通过硬过滤前的合格池**为基准，排名口径写入特征快照元数据。
6. 所有窗口长度与权重来自 config，不得硬编码。

---

## 后果

### 正面

- 板块 RS 与宪法字面一致，且基准可与真实标的对账。
- 缺失是显式状态，排名不会被混口径污染。
- 日更不会因新上市股票停摆，同时保留真实数据错误的停机。

### 代价

- 需额外维护 `sector` 列与 11 只 ETF 的行情。
- 板块数据不全时候选显著减少（期望行为）。
- 手写数值函数需要配套单测。

### 硬前置

**[#3](https://github.com/xxxxxthhh/swingMomentum/issues/3) 生产股票池是 M2 的 blocker。** 当前 31 标的种子池上的任何横截面排名**不得**被当作信号形态证据。M2 代码可以在种子池上开发与测试；**结论不行**。

---

## 备选（未采纳）

| 方案 | 原因 |
|------|------|
| 同板块个股横截面中位数作板块基准 | **与宪法 §18.2 冲突**；基准随池组成漂移，无法与可交易标的对账 |
| 缺板块时重新归一化 RS 子权重 | 混口径分数进同一张排名表，污染整个横截面而非单只股票 |
| 缺板块时 RS_Sector = 0 | 「不填有利默认」的直接违反 |
| 历史不足即整个 run fail-closed | 池中总有新成分股，会让日更常态性失败，进而诱使人绕过 fail-closed |
| pandas / numpy 做特征计算 | 数据量不需要；`NaN` 的隐式跳过与本 ADR 的显式缺失语义冲突；无前视更难逐行审计 |
| M2 暂不做板块 RS | config 的 0.20 权重会长期悬空，且 RS 分数口径与宪法不符 |

---

## 开放项（不阻塞 M2 编码）

| 项 | 归属 | 说明 |
|----|------|------|
| `sector` 分类体系 | #3 | 建议 GICS 11 板块；需与 `sector_benchmarks` 键一致 |
| 空日历是否 fail-closed | [#5](https://github.com/xxxxxthhh/swingMomentum/issues/5) | M1 遗留；影响 regime 的日历校验强度 |
| 板块 ETF 自身的历史不足 | M2 实现 | 11 只 ETF 均有十年以上历史，实践中不触发；仍按 §3 规则处理 |

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-22 | proposed | M2 实现前提交评审 |
