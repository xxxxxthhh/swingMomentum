# ADR：M3 Watchlist、信号身份与生命周期持久化

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | accepted（rev.3；[PR #13](https://github.com/xxxxxthhh/swingMomentum/pull/13) 二次评审接受） |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0（不 bump；新增 config 键仅改 `config_hash`，见 [M1 ADR §2.4](./2026-07-22_m1_data_provider_and_universe.md)） |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（§16 硬过滤、§17 动量、§23 突破信号） |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)（M3/M4、§2.1 状态机、§2.2 Watchlist） |
| 关联决策 | [M1](./2026-07-22_m1_data_provider_and_universe.md)、[M2](./2026-07-22_m2_feature_engine_and_regime.md) |
| 变更摘要 | 修正 setup_key 身份缺陷；冻结 relative_volume 口径、watchlist 过期语义、状态持久化模型；rev.2 钉死过期边界、`DETECTED` 路径、幂等语义、触发公式与 `TotalScore` 表态 |

---

## 背景

M1 交付行情与校验，M2 交付特征、排名与市场状态。M3 是 **MVP-A 的核心**：硬过滤 → Watchlist → 触发 → 信号状态机。

核查既有代码时发现 **Phase 0 的 `make_setup_key` 有一处会直接击穿状态机目的的缺陷**（§1）。另有三处语义在编码前必须定死。

---

## 决策

### 1. ⚠️ `setup_key` 不得包含滚动突破位（修正既有实现）

现状（[`src/smm/domain/identity.py`](../../src/smm/domain/identity.py)）：

```python
make_setup_key(symbol, breakout_window=..., breakout_level=..., anchor_date=...)
# → "NVDA|bw20|lvl109.3400|a2024-01-01"
```

`breakout_level` 是**前 20 日最高价**，窗口每日滚动，因此该值几乎每天都变：

```text
D1: lvl=109.34 → setup_key A → signal_id A
D2: lvl=110.02 → setup_key B → signal_id B    ← 同一个 setup，新身份
D3: lvl=110.51 → setup_key C → signal_id C
```

于是**同一个观察中的 setup 每天都会产生一个新的 logical signal**——正是 Plan v1.1 §2 约束 6 明令禁止的「非终态 logical signal 不得每日复制为新的可执行信号」，也正是状态机存在的唯一理由。

该 helper 写于 Phase 0，当时尚无 watchlist，从未被真正行使过，所以缺陷一直未暴露。

**决策：身份锚定在「这个 setup 何时开始被观察」，而非它今天的触发价。**

```python
make_setup_key(symbol, breakout_window=..., watchlist_entry=<date>)
# → "NVDA|bw20|w2023-12-04"
```

| 属性 | 归类 | 理由 |
|------|------|------|
| symbol、breakout_window、**watchlist 进入日** | **身份** | 定义「哪一次观察」 |
| 突破位、相对量、评分 | **每日属性** | 逐日重算，不进身份 |

**后果：** 连续在观察池中的标的始终是同一个 logical signal；过期后重新合格则是**新的**一个（新的观察窗口 = 新的 setup）。这正是想要的语义。

### 2. `relative_volume` 的均量不含当日

宪法 §23 写 `RelativeVolume = TodayVolume / AverageVolume_20`，未言明分母是否含当日。

**决策：不含。** 与同节「计算前 20 日最高价时**不得包含当前交易日**」对称——突破位不能包含当日高点，量能基准同样不该包含当日量。

否则放量日会**稀释自己的基准**：正常量为 1、当日 20 倍时，含当日的均量为 `(19 + 20)/20 = 1.95`，算出 relvol = 10.2 而非 20。阈值 1.30 因此会随放量幅度而漂移，越极端的放量被压缩得越厉害。

```text
RelativeVolume = volume[t] / mean(volume[t-20 : t])   # 右开，不含 t
```

### 3. Watchlist 过期以交易日计，过期后重新合格是新信号

config 已有 `signal.watchlist_expire_bars: 20`。

| 规则 | 决策 |
|------|------|
| 计数单位 | **交易日（session）**，非日历日。休市不消耗观察窗口 |
| 起点 | 进入 watchlist 当日（该日计为第 0 根） |
| 到期 | 满 `watchlist_expire_bars` 仍未触发 → `expired`（终态）。精确边界与评估顺序见 [R1](#r1-过期边界与同一-as_of-的评估顺序) |
| 硬过滤中途失效 | 立即 `expired`，记 reason code；不等待到期 |
| 过期后重新合格 | **新的 logical signal**（新 setup_key，见 §1） |

**不采纳**「过期后复用原 signal_id」：终态不可复活（Phase 0 的 `ALLOWED_SIGNAL_TRANSITIONS` 已把 `EXPIRED` 定为无出边），且两次观察是两次独立的机会，混为一个会让「同一 setup 观察了多久」这一审计问题无法回答。

### 4. 状态持久化：只追加的转移日志，当前状态由重放导出

**决策：** `signal_transitions` 为**只追加**的 Parquet 日志，每行一次转移（`signal_id`、`from_state`、`to_state`、`as_of`、`reason_codes`、`strategy_version`、`config_hash`）。当前状态由重放导出，不单独维护可变表。

**理由：**

1. Plan v1.1 §2.1 本来就要求 `signal_transitions` 审计事件——把它当作**真相源**而非派生物，可变状态与日志不一致的可能性就不存在。
2. 与 repo 的「不覆盖历史」纪律一致（docs/README.md §4）。
3. 体量微不足道：数百标的、每日至多数条转移，重放是毫秒级。
4. 不引入第二套存储引擎。

**这是对 Plan v1.1 §3「存储 Parquet + SQLite」的有意偏离**，请评审确认。若坚持 SQLite，可在同一接口后替换，不影响本 ADR 其余决策。

**幂等要求：** 同一 `as_of` + 同一 config 重跑，**不得**追加重复转移。去重键与冲突处理见 [R3](#r3-幂等语义每个-signal_id-as_of-至多一条转移)。

### 5. 硬过滤在 M3 应用，评分沿用 M2

M2 已产出特征与横截面分数，但**未应用宪法 §16 硬过滤**。M3 补上，逐条记 reason code：

```text
close_above_sma_50 / close_above_sma_200 / sma_50_above_sma_200
return_63_positive / return_126_positive
within_15_percent_of_52w_high
min_price / min_avg_dollar_volume_20d
```

全部通过 → 进 Watchlist。任一失败 → 记录失败项，不进。

**排名顺序不变**：M2 ADR R1 已定「排名以硬过滤**前**的合格池为基准」，M3 不得改为对幸存者重排——否则分数会随过滤强度漂移。

---

## 边界冻结（rev.2，回应 PR #13 评审）

以下五项在评审中被指出「不写死则实现会分叉」。逐项钉死。

### R1. 过期边界与同一 `as_of` 的评估顺序

```text
age(as_of) = 从 watchlist 进入日（含）到 as_of（含）之间的 session 数 - 1
             进入日 age = 0
观察窗 = age ∈ [0, N-1]，N = signal.watchlist_expire_bars
```

**观察窗恰好 N 个 session，不是 N+1。** 评审建议的「先判触发、再 `age >= N` → expired」若字面实现，会在 `age = N` 那天多给一次触发机会，`N=20` 时实际可观察 21 个 session，与 `watchlist_expire_bars: 20` 的字面读法不符。此处取**恰好 N**：`age = N` 的那天已在窗外，不再评估触发。

评审「不得本可触发却先被过期」的意图仍然满足——**窗内每一个 session（含 age=0 与 age=N-1）都完整评估触发**，过期只在窗口耗尽后发生。

工作示例（`N=20`，假设该区间无休市）：

| as_of | age | 动作 |
|-------|-----|------|
| 2026-03-02（进入日） | 0 | 评估触发；未触发则留在 `WATCHLISTED` |
| 2026-03-27 | 19 | **窗内最后一次**触发评估 |
| 2026-03-30 | 20 | 不再评估触发 → `EXPIRED`（`watchlist_expired`） |

同一 `as_of` 上的评估顺序：

```text
1. 硬过滤是否仍全部通过？       否 → EXPIRED（reason: hard_filter_lost）
2. age < N 且触发条件满足？      是 → TRIGGERED（reason: breakout_confirmed）
3. age >= N？                   是 → EXPIRED（reason: watchlist_expired）
4. 否则                            保持 WATCHLISTED
```

**硬过滤先于触发，触发受 `age < N` 门控**：宪法 §16 是可执行的前置条件，已失效的标的即使当日突破也不得成为可执行信号；`age = N` 已在观察窗外，不得先无条件计算触发再判断过期。

### R2. `DETECTED` 语义与同日触发路径

`DETECTED` 是**出生态**，不单独落一行日志；新信号的第一行转移即以 `DETECTED` 为 `from_state`。

| 情景 | 日志中的转移 |
|------|-------------|
| 硬过滤通过、当日未突破 | `DETECTED → WATCHLISTED` |
| 硬过滤通过、**当日已突破 + 量能达标** | `DETECTED → TRIGGERED`（同日，`watchlist_entry` = 该 `as_of`）；不得因「没在池里待过」拒绝 |
| 硬过滤失败 | 不建 signal，不落日志 |
| 已在池中、硬过滤中途失效 | `WATCHLISTED → EXPIRED` + reason code |

每种情景恰好一行，无中间跳。Phase 0 的 `WATCHLISTED → DETECTED` 反向边保留在表中，但 **V1 永不发出**。

### R3. 幂等语义：每个 `(signal_id, as_of)` 至多一条转移

采用评审的方案 **R**。`(signal_id, as_of, to_state)` 作为去重键过弱——同日重跑若两次算出不同 `to_state`，两行会并存，重放结果不确定。

| 规则 | 决策 |
|------|------|
| 唯一键 | `(signal_id, as_of)`，**至多一条**转移 |
| 重放定序 | 按 `as_of` 排序即可，无需 `seq` 列（唯一键已保证全序） |
| 同 config 重跑，结果一致 | **no-op**，不写入 |
| 同 config 重跑，结果**不一致** | **fail loud**：中止并报告差异，不静默覆盖也不并存 |

R2 的路径设计保证每天至多一次转移，与该唯一键相容（同日触发是单行 `DETECTED → TRIGGERED`）。

**代价（有意接受）：** 上游 bar 修正后重跑会合法地产生差异，从而停在人工裁决上。这与 [PR #8](https://github.com/xxxxxthhh/swingMomentum/pull/8) 空日历、[PR #12](https://github.com/xxxxxthhh/swingMomentum/pull/12) 缺 session 的 fail-closed 纪律一致：宁可停，不可静默改写审计链。覆写通道的具体形态属实现 PR。

### R4. 触发公式：用 `high`，不用 `close`

Plan v1.1 §4.5 已定，此处引用并钉死索引口径——实现若写成 `max(close[...])` 会**静默**改变触发集合且不报错：

```text
close[t] > max(high[t-20 : t])                        # 右开，用 high；不含当日
RelativeVolume >= signal.relative_volume_min          # 见 §2，分母同样不含当日
可选：(close[t] - EMA20[t]) / ATR20[t] <= signal.max_extension_atr
      仅当 signal.extension_filter_enabled 为真
```

Plan 原文写作 `Close > max(High[-20:-1])`，该切片实为 19 个元素；**以上 `t` 下标形式为准**（宪法 §23「计算前 20 日最高价时不得包含当前交易日」）。

### R5. `TotalScore` 与 `trend_trigger_weight: 0.20`

**M3 不计算 `TotalScore`。** 展示 M2 已有的 `MomentumScore` / `RelativeStrengthScore` 加上触发布尔与延伸值；排名口径仍为 M2 R1，不变。

`scoring.trend_trigger_weight: 0.20` 保留在 config 中（schema 已校验三权重和为 1），**实现不得为它临时发明分子**。Trend/Trigger 质量分的定义延后；届时冻结公式会改变分数，按 M1 ADR §2.4 走 `config_hash` 纪律。

---

## 范围（V1 明确不做）

| 项 | 状态 |
|----|------|
| 宪法 §23「收盘位置靠近当日高位」 | **不做**。Plan v1.1 §4.5 已把 V1 触发器瘦身为「20 日突破 + 量能 + 可选延伸过滤」，并明言「不做复杂整理形态编码」 |
| §21 Setup 结构评分（整理区间、紧凑度、量缩） | 不做，同上 |
| 财报窗口过滤 | 无可靠财报日历前不做（M1 ADR 开放项） |
| 风险引擎、仓位、Paper 成交 | **MVP-B**（M5–M7） |
| 市场状态拒单 | MVP-B。M3 仅展示 regime，不据此拒绝进入 watchlist |

---

## 对 M3 实现的约束

1. 同一 `as_of` + 同一 config 重跑 → 信号实体与状态**逐字节一致**；转移日志不得追加重复行。
2. 非终态 logical signal 跨日为**续存**，不得每日新建。测试须覆盖「同一 setup 连续 ≥3 个 session 只有一个 `signal_id`」，且**滚动的 `breakout_level` 日变不得产生新 id**。
3. 每次转移必须带 reason code；无理由的状态变更视为缺陷。
4. 触发判定只允许消费 `date <= as_of` 的 bar（沿用 M2 的入口截断）。
5. 突破位的窗口**不含当日**（宪法 §23）；相对量的均量窗口同样不含当日（§2）。
6. 所有阈值来自 config。
7. `EXPIRED` / `CANCELLED` 等终态无出边（Phase 0 已定），实现不得绕过 `assert_signal_transition`。
8. 缺基准日历或缺 bar 时**不得静默产出触发**——沿用 [#12](https://github.com/xxxxxthhh/swingMomentum/pull/12) 的 session 完整性与 fail-closed 语义。
9. 合成门禁须覆盖 MVP 切片 §9 的四类用例：突破成功 / 量能不足 / 硬过滤拒绝 / 同一 setup 跨多日。
10. 修改 `make_setup_key` 时同步废止 `breakout_level` 参数及 `test_setup_key_level_rounding` 一类用例（属 M3 实现 PR，不在本 ADR 分支）。

---

## 后果

### 正面

- 观察中的 setup 有稳定身份，日报能区分「新触发 / 续存 / 终态」。
- 转移日志即审计链，无需额外对账。
- 放量阈值不再随放量幅度漂移。

### 代价

- `make_setup_key` 签名变更，Phase 0 的既有测试需同步更新。
- 当前状态需重放导出，日报要多一步（体量下可忽略）。

### 必须遵守

- 终态不可复活；过期重新合格是新信号。
- 排名基准仍是硬过滤**前**的池子。

---

## 备选（未采纳）

| 方案 | 原因 |
|------|------|
| `setup_key` 保留 `breakout_level` | 每日新身份，直接击穿状态机目的 |
| 以「整理区间起点」为锚 | V1 不编码整理形态（Plan §4.5），无从计算 |
| 相对量分母含当日 | 放量日稀释自己的基准，阈值随幅度漂移 |
| 过期后复用原 signal_id | 终态复活；且无法回答「这次观察了多久」 |
| watchlist 过期按日历日 | 长假会凭空消耗观察窗口 |
| 观察窗 `age ∈ [0, N]`（`age = N` 当天仍评估触发） | 实际可观察 N+1 个 session，与 `watchlist_expire_bars` 的字面读法不符（见 [R1](#r1-过期边界与同一-as_of-的评估顺序)） |
| 去重键含 `to_state` 或引入 `seq` 列 | 同日多跳会让重放终态取决于定序细节；每日至多一次转移已足够（[R3](#r3-幂等语义每个-signal_id-as_of-至多一条转移)） |
| 可变状态表 + 日志双写 | 二者不一致是必然会发生的缺陷 |
| SQLite 存信号状态 | 引入第二套存储引擎；只追加日志已满足需求（**偏离 Plan §3，请裁决**） |

---

## 开放项（不阻塞 M3）

| 项 | 归属 |
|----|------|
| 日报格式（Markdown / CSV） | M4 |
| `manual_decisions` 人工否决日志 | MVP-B（Plan §5） |
| 财报日历数据源 | 有可靠源后 |

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-22 | proposed | M3 实现前提交评审 |
| 2026-07-22 | proposed (rev.2) | 回应 PR #13 评审：新增 R1 过期边界与评估顺序、R2 `DETECTED` 与同日触发、R3 幂等语义、R4 触发用 `high`、R5 `TotalScore` 表态；补充实现约束 8–10 |
| 2026-07-22 | accepted (rev.3) | 二次评审接受；将 R1 写成可直接实现的 `age < N` 门控顺序，并修正 PR #8 链接 |
