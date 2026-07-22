# ADR：M5 风险引擎、计划仓位与生命周期边界

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | accepted（rev.2；[PR #21](https://github.com/xxxxxthhh/swingMomentum/pull/21) 首轮评审接受） |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0（参数不变；实施期新增 operational kill-switch 键只移动 `config_hash`） |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（§26–§28、§33–§35、§53–§54） |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)（M5 / PR6） |
| 关联决策 | [M1](./2026-07-22_m1_data_provider_and_universe.md)、[M2](./2026-07-22_m2_feature_engine_and_regime.md)、[M3](./2026-07-22_m3_watchlist_and_signal_lifecycle.md)、[M4](./2026-07-22_m4_signal_report_and_daily_task.md) |
| 变更摘要 | 冻结 M5 纯风险 seam、eligible 输入、整股 sizing、组合预算、批内预留、Risk-Off / kill switch、缺失 cluster 与生命周期单跳语义 |

---

## 背景

M4 已交付 `mvp_a_signal`，但 M5 不能直接从「有一个 TRIGGERED」跳到仓位代码。现有权威层仍留下会让实现分叉的关键问题：

1. Plan 的状态图写作 `TRIGGERED → ELIGIBLE → RISK_ACCEPTED|RISK_REJECTED`；M3 R3 又冻结同一 `(signal_id, as_of)` **至多一条** transition。收盘后一次风险评估若落两跳，会直接违反已接受 ADR。
2. 宪法以 `SetupLow` 定止损，但 V1 极简 trigger 没有编码整理区间，也没有定义 `SetupLow` 的窗口。
3. 宪法的 `UnitRisk` 包含滑点和费用；当前冻结 config 没有成本参数，不能在代码里猜一个数，也不能填 0 后宣称风险完整。
4. 单票、现金、总敞口用资本金额约束；heat、板块、risk cluster 用初始风险金额约束。若不先统一单位，很容易把两者相加或比较。
5. 同一批候选若各自对同一个原始 portfolio snapshot 独立通过，会合计超限；若按调用顺序逐个扣减，又会把未声明的迭代顺序变成策略规则。
6. M5 要求 risk cluster 限制，但种子表在 Plan M7；把缺失 cluster 当作「无限额」会 fail-open，猜标签则不可审计。
7. `risk_off_max_exposure: 0.25` 表示已有持仓可残留，并不授权 Risk-Off 建新仓；不能被误读为「仍可开到 25%」。

因此 M5 先冻结一个**纯计划风险 seam**。它不接管 M4 日任务、不生成订单、不模拟成交；M6 提供 true-print / cost / fill 能力后，M7 才把同一 seam 接回 `run-daily`。

---

## 决策

### 1. M5 是纯风险计划，不是 Paper，也不提前修改 `run-daily`

M5 新增独立公共库 seam（命名可为 `evaluate_risk_batch`）：

```text
tuple[EligibleCandidate] + PortfolioSnapshot + frozen RiskSection
  → tuple[RiskDecision]
```

M5：

- 计算接受 / 拒绝、整股计划数量、计划资本与计划初始风险；
- 对同批接受项在内存中预留 capital / heat / sector / cluster budget；
- 返回确定性、可审计的 reason codes；
- 不写 order / fill / position / trade；
- 不读取 yfinance；M5 代码路径**完全不读取** `Bar`、`PrintBar`、`AdjustedBar` 或 `TradeableBar`，测试直接构造已校验的 `EligibleCandidate`，M7 才由上游 adapter 提供候选；
- 不把 M4 manifest 的 `execution_mode` 从 `mvp_a_signal` 改成 shadow/paper；
- 不在 M5 把风险 decision 接入现有 transition store。生命周期持久化由 §8 冻结语义、M7 集成。

理由：M5 的验收问题是「同一个已验证候选，在一个已知组合上最多能计划多少风险」，不是「下一根真实开盘成交多少」。

### 2. `EligibleCandidate` 是显式输入；Risk Engine 不发明入场或止损

候选至少携带：

```text
signal_id, symbol, as_of,
strategy_version, config_hash,
regime, sector, risk_cluster,
entry_reference, stop_reference,
estimated_entry_cost_per_share,
estimated_total_cost_per_share,
momentum_score, relative_strength_score
```

约束：

- `entry_reference > stop_reference > 0`；
- `estimated_entry_cost_per_share > 0` 且 `estimated_total_cost_per_share >= estimated_entry_cost_per_share`；缺失或总成本为零均 fail closed；
- `unit_risk = entry_reference - stop_reference + estimated_total_cost_per_share`；
- `capital_per_share = entry_reference + estimated_entry_cost_per_share`；调用方必须同时提供两个明确值，Risk Engine 不自行按比例拆分 entry / exit 成本；
- `sector` 必须存在并与 M2 universe / sector key 一致；缺失是数据错误，不是普通业务拒绝；
- `risk_cluster` 缺失时规范化为唯一 sentinel `unclassified`，所有未知标签共享同一个 cluster budget；不得跳过 cluster limit，也不得猜测行业主题。

`EligibleCandidate` 表示以下上游契约已满足：

1. 信号当前为 `TRIGGERED`；
2. 计划 entry/stop 来自合法 true-print 计划 seam，而非 adjusted/provider-native bar；
3. 止损距离位于冻结的 `1.0–2.5 ATR` 范围；
4. 费用和滑点假设已显式版本化并计入 unit risk。

M5 Risk Engine 对这些条件**再次校验形状与正值**，但不重新计算 trigger、ATR、SetupLow 或成本模型。这样 Scanner 不能绕过 Risk Engine，Risk Engine 也不会偷偷长成第二套 Scanner / Broker。

### 3. `SetupLow` 的 V1 计划口径：观察窗口内 true-print low

V1 不编码主观整理形态，因此将 Setup 定义为这次 logical signal 的观察窗口：

```text
SetupLow = min(true_print_low[watchlist_entry : signal_as_of])
InitialStop = SetupLow - stop.atr_buffer × ATR20(signal_as_of)
```

- 两端 session 都包含；
- `watchlist_entry` 与 `signal_as_of` 来自同一 logical signal；
- true-print low 必须来自 `PrintBar → TradeableBar` 边界；不得用 adjusted low 或 yfinance provider-native low 冒充；
- 若历史 true-print 覆盖不足，候选不能成为 `EligibleCandidate`；
- 若由此得到的 stop distance 不在 config 的 `[min_stop_distance_atr, max_stop_distance_atr]`，则上游资格失败，不进入 Risk Engine。

不采用「前 20 日任意 low」：突破窗口定义 trigger，不等于这次 setup 从何时开始被系统观察；使用 `watchlist_entry` 能与 `setup_key`、审计窗口保持同一身份。

### 4. `PortfolioSnapshot` 全部使用金额；资本与初始风险严格分列

快照至少包含：

```text
as_of, account_equity, available_cash,
gross_exposure_capital,
portfolio_initial_risk,
sector_initial_risk: Mapping[str, money],
cluster_initial_risk: Mapping[str, money],
open_symbols, reserved_signal_ids,
strategy_version, config_hash
```

单位规则：

| 概念 | 单位 | 用途 |
|------|------|------|
| equity / cash / gross exposure | 货币金额 | 单票资本、现金、regime 最大敞口 |
| portfolio / sector / cluster initial risk | 货币金额 | heat、板块风险、簇风险 |
| config 上限 | equity 比例 | 比较前乘以 `account_equity` 转为金额 |

以下情况整批 fail closed，不返回看似正常的 reject：equity 非正、金额为负、snapshot/candidate 的 as_of 或 identity 不一致、重复 signal_id、重复 symbol、gross exposure 大于 equity（V1 无杠杆）、已有 reservation 无法对账。

已有同 symbol 持仓时返回业务拒绝 `symbol_already_open`（V1 `pyramiding: false`），而不是数据异常。

### 5. Regime 与 kill switch 先于 sizing

实施 PR 在 `risk` config 增加：

```yaml
risk:
  new_entries_enabled: true
```

- `false`：所有新候选拒绝，reason `risk_new_entries_kill_switch`；
- key 缺失：schema 失败；不得用默认 `true` fail-open；
- 这是 operational safety gate，改变 config hash，不 bump 策略版本；历史 artifact root 不覆盖。

Regime：

| regime | 每笔风险 | 最大敞口 | 新仓 |
|--------|----------|----------|------|
| Risk-On | `risk_on_per_trade` | `risk_on_max_exposure` | 可继续 sizing |
| Neutral | `neutral_per_trade` | `neutral_max_exposure` | 可继续 sizing |
| Risk-Off | `risk_off_per_trade == 0` | `risk_off_max_exposure` 仅描述已有敞口上限 | **一律拒绝新仓**，`risk_off_new_entries_blocked` |

若冻结 config 将 `risk_off_per_trade` 改为非零，schema/风险契约应拒绝该配置；不能通过参数漂移绕过宪法的新仓禁令。

业务拒绝按固定顺序短路，避免同一候选因调用路径不同得到不同 primary reason：

```text
1. kill switch
2. Risk-Off regime
3. symbol_already_open
4. sizing capacities
```

输入/identity/单位错误在上述业务门之前整批 fail closed。命中 kill switch、Risk-Off 或已有 symbol 后不再运行 sizing；进入 sizing 后，所有为 0 的 capacity reasons 仍按 §6 的固定顺序输出。

### 6. 整股 quantity 是所有预算 capacity 的最小值

V1 不做 fractional shares。对每个候选计算：

```text
per_trade_cap = floor(equity × regime_risk_per_trade / unit_risk)
position_cap  = floor(equity × max_position_capital / capital_per_share)
cash_cap      = floor(available_cash / capital_per_share)
exposure_cap  = floor((equity × regime_max_exposure - gross_exposure_capital)
                      / capital_per_share)
heat_cap      = floor((equity × max_portfolio_heat - portfolio_initial_risk)
                      / unit_risk)
sector_cap    = floor((equity × max_sector_risk - sector_initial_risk[sector])
                      / unit_risk)
cluster_cap   = floor((equity × max_risk_cluster_risk
                       - cluster_initial_risk[risk_cluster]) / unit_risk)

quantity = min(per_trade_cap, position_cap, cash_cap, exposure_cap,
               heat_cap, sector_cap, cluster_cap)
```

规则：

- 每个 remaining budget 先以 0 为下限；不得用负数 quantity 表示拒绝；
- `quantity >= 1` → ACCEPT；否则 REJECT；
- ACCEPT 必须带正整数 quantity、planned capital 与 planned initial risk；
- REJECT 不得带正 size；
- 所有达到最小 capacity 的约束都记录 `sized_by_*`；quantity 为 0 时对应 reason 使用 `*_limit_reached`；并列约束全部保留，按固定 reason-code 顺序输出；
- 当前冻结 config 没有 `max_positions`，M5 不从宪法「8–10」建议中擅自选择整数；如需该门，先新增 config/ADR。

这允许约束**缩小**仓位，而不是只做全有或全无；但任何约束连 1 股都容不下时必须拒绝。

### 7. 同批候选按 M4 的确定性排序逐个预留

批处理顺序固定为 M4 已接受的报告顺序：

```text
MomentumScore 降序 → RelativeStrengthScore 降序 → symbol 升序
missing score 排末
```

每个 ACCEPT 后立即在内存 snapshot 中预留：

- `quantity × capital_per_share`：cash 与 gross exposure；
- `quantity × unit_risk`：portfolio heat、sector risk、cluster risk；
- symbol 与 signal_id：防止同批重复。

后续候选基于更新后的 snapshot 计算。相同候选集合无论调用方如何排序，输出都相同；同一输入重复执行逐字节一致。

不采用「每个候选独立看原 snapshot」：它可让多个单独合格的候选合计突破 heat/sector/cluster 上限。也不采用调用方 insertion order：那会把未记录的容器顺序变成资本分配规则。

### 8. `ELIGIBLE` 是 typed seam，不在同日单独持久化一跳

M3 R3 的 `(signal_id, as_of)` 至多一条 transition 保持不变。M5/M7 不引入 `seq`，也不在同一日写：

```text
TRIGGERED → ELIGIBLE → RISK_ACCEPTED
```

改为：

```text
TRIGGERED
  → RISK_ACCEPTED   # 当日 EligibleCandidate 有效且 RiskDecision ACCEPT
  → RISK_REJECTED   # 当日 EligibleCandidate 有效且 RiskDecision REJECT
  → CANCELLED       # 资格构建失败且属于业务取消（M6/M7 冻结具体 reason）
```

`EligibleCandidate` 是代码中的显式、校验后的边界对象，不是必须落盘的中间状态。实施时允许表需增加 `TRIGGERED → RISK_ACCEPTED|RISK_REJECTED`；`ELIGIBLE` enum 暂保留用于历史兼容，但 V1 不发出该 transition。

这是对 Plan v1.1 状态图的有意细化，也是对 M3 R3 的保留。备选的「同日两跳 + seq」会破坏既有唯一键与重放全序；「风险决策推迟到下一收盘」则无法满足次日开盘成交时序。

M5 纯 seam 暂不写 transition；M7 接入时按上述单跳落盘，并继续使用 M3/M4 的 batch seal / conflict 纪律。

### 9. M5 输出契约

`RiskDecision` 至少包含：

```text
signal_id, symbol, as_of,
strategy_version, config_hash,
verdict, reason_codes,
quantity,
entry_reference, stop_reference,
unit_risk, planned_capital, planned_initial_risk,
sector, risk_cluster, regime
```

- 不写 wall-clock `decided_at`；`as_of` 是确定性事实；
- decision 不等于 order，不含 fill price/status；
- M6 在真实下一 session open 到达后必须重新运行 gap/stop/成本检查，并可**减少或取消** M5 quantity；不得因为更有利开盘在未重新通过全组合风险预算时擅自增仓；
- M5 ACCEPT 不是绕过未来 M6/M7 gate 的执行授权。

---

## Reason-code 最小集合

固定前缀 `risk_`，至少覆盖：

```text
risk_new_entries_kill_switch
risk_off_new_entries_blocked
risk_symbol_already_open
risk_per_trade_budget_exhausted
risk_position_cap_reached
risk_cash_exhausted
risk_exposure_limit_reached
risk_portfolio_heat_limit_reached
risk_sector_limit_reached
risk_cluster_limit_reached
risk_sized_by_per_trade
risk_sized_by_position_cap
risk_sized_by_cash
risk_sized_by_exposure
risk_sized_by_portfolio_heat
risk_sized_by_sector
risk_sized_by_cluster
```

数据/identity/单位缺失走 exception + fail-closed run，不伪装成普通风险拒绝 reason。

---

## 实现验收契约

1. 回归先行证明「候选独立看原 snapshot」会合计突破 heat 或 cluster，再实现批内预留。
2. Risk-Off 与 kill switch 对任何正预算候选都拒绝。
3. Neutral 使用 `neutral_per_trade` 与 `neutral_max_exposure`，不回退到 Risk-On。
4. heat、sector、cluster 各自触顶时连 1 股都不接受；边界下仍可缩量到正整数。
5. `unclassified` cluster 的多个候选共享同一预算。
6. 相同候选集合换输入顺序，decision bytes 不变。
7. adjusted/provider-native bar 不能进入计划价格构建 seam；缺 true-print 或显式成本 fail closed。
8. 所有阈值来自 config；新增 kill switch 无 fail-open default。
9. M5 不创建 order/fill/position，不修改 M4 execution mode，不接入 `run-daily`。
10. 目标测试、完整 pytest、ruff、`git diff --check` 全绿；PR 附精确 diff 与证据。

---

## 非目标

- 真实/模拟 fill、spread/slippage 模型的参数来源（M6）
- 次日 gap 取消与实际 open 后二次 sizing（M6）
- position / exit / circuit breaker drawdown 状态机（M6）
- manual decision log（M6）
- `run-daily --mode shadow|paper` 与持久化 risk decision（M7）
- 最大持仓数量（冻结 config 尚无阈值）
- 自动真实下单、做空、杠杆、期权

---

## 备选（未采纳）

| 方案 | 原因 |
|------|------|
| M5 直接从 adjusted signal close 生成 order | 违反 true-print / PrintBar 边界，并绕过 M6 |
| 成本缺失时按 0 继续 | 低估 unit risk，违反宪法真实成本原则 |
| 每个候选独立使用原 portfolio snapshot | 批量合计可超 heat / sector / cluster |
| 按调用方顺序预留 | 未审计的容器顺序成为资本分配规则 |
| 缺 risk cluster 时跳过该门 | fail-open |
| 猜测 risk cluster | 不可复现、不可审计 |
| Risk-Off 仍开到 `risk_off_max_exposure` | 混淆已有敞口上限与新仓许可 |
| 同日持久化 `TRIGGERED → ELIGIBLE → RISK_*` | 违反 M3 R3 的 `(signal_id, as_of)` 单行唯一键 |
| 风险决策推迟一个收盘日 | 无法满足信号后下一 session 开盘时序 |
| 从宪法建议中硬编码 max positions = 8 或 10 | 冻结 config 未选择该阈值 |

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-22 | proposed | M5 实现前提交评审；冻结风险输入、预算单位、批内预留、生命周期单跳与 M5/M6/M7 边界 |
| 2026-07-22 | accepted (rev.2) | Task Reviewer comment `5047596385` 接受契约；按 non-blocking residual 补充业务拒绝短路顺序与 M5 不读取任何 bar 类型的显式边界 |
