# ADR：M3 Watchlist、信号身份与生命周期持久化

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | proposed |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0（不 bump；新增 config 键仅改 `config_hash`，见 [M1 ADR §2.4](./2026-07-22_m1_data_provider_and_universe.md)） |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（§16 硬过滤、§17 动量、§23 突破信号） |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)（M3/M4、§2.1 状态机、§2.2 Watchlist） |
| 关联决策 | [M1](./2026-07-22_m1_data_provider_and_universe.md)、[M2](./2026-07-22_m2_feature_engine_and_regime.md) |
| 变更摘要 | 修正 setup_key 身份缺陷；冻结 relative_volume 口径、watchlist 过期语义、状态持久化模型 |

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
| 到期 | 满 `watchlist_expire_bars` 仍未触发 → `expired`（终态） |
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

**幂等要求：** 同一 `as_of` + 同一 config 重跑，**不得**追加重复转移。以 `(signal_id, as_of, to_state)` 去重。

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
2. 非终态 logical signal 跨日为**续存**，不得每日新建。测试须覆盖「同一 setup 连续 N 日只有一个 logical signal」。
3. 每次转移必须带 reason code；无理由的状态变更视为缺陷。
4. 触发判定只允许消费 `date <= as_of` 的 bar（沿用 M2 的入口截断）。
5. 突破位的窗口**不含当日**（宪法 §23）；相对量的均量窗口同样不含当日（§2）。
6. 所有阈值来自 config。
7. `EXPIRED` / `CANCELLED` 等终态无出边（Phase 0 已定），实现不得绕过 `assert_signal_transition`。

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
