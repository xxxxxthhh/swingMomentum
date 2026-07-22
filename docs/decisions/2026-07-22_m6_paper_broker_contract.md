# ADR：M6 Paper Broker、真实成交价、退出与熔断契约

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | proposed |
| 日期 | 2026-07-22 |
| 策略版本 | 不在本 ADR 中变更；M6 实现前必须以新的冻结 config identity 固化尚缺的成本与熔断参数 |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（§12.1、§24、§26、§29、§31、§35、§36、§53） |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)（M6 / PR7） |
| 前置决策 | [M1](./2026-07-22_m1_data_provider_and_universe.md)、[M3](./2026-07-22_m3_watchlist_and_signal_lifecycle.md)、[M4](./2026-07-22_m4_signal_report_and_daily_task.md)、[M5](./2026-07-22_m5_risk_engine_and_planned_sizing.md) |
| 变更摘要 | 冻结 M6 的 true-print、实际开盘二次风险、日线成交顺序、止损/退出、paper ledger、人工 SKIP、熔断与 M7 编排边界；不实现自动真实下单。 |

---

## 背景

M5 已交付纯 `RiskDecision` seam，但它刻意不创建 order、fill、position 或 trade。M6 是 MVP-B 第一个能够把计划仓位转为模拟结果的边界，因此不能把以下未冻结问题留给实现者临场决定：

1. Yahoo provider-native OHLC 已拆股调整，不能作为填单、止损或跳空价格；现有 `DataProvider` 也尚未暴露公司行动历史。
2. M5 输入要求明确、非零成本，冻结 config 却只有 `next_day_open` 与 `max_open_gap_atr`，没有滑点、价差、费用或熔断参数。
3. 同一根日线既有 open 又有 high/low/close；若不定义日内事件顺序，就会在「开盘成交后当日触发止损」与「收盘 EMA 信号」上产生前视或重复成交。
4. M3 对每个 `(signal_id, as_of)` 最多允许一条 lifecycle transition。entry 当天又止损时，不能用两条 signal transition 伪造一个日内序列。
5. 宪法写了日损失、回撤、数据不一致熔断，但没有定义其净值口径、优先级或怎样把“单笔风险减半”传给不应被绕过的 M5 风险引擎。

本 ADR 的保守选择是先把这些 seam 写成可测试契约；在 Task Reviewer 接受、并把缺失执行参数写进新的冻结 config identity 前，不实施 Paper Broker。这样不会以“paper”为名把 provider-native 价格或猜测性成本带入结果。

---

## 决策

### 1. M6 是可重放的 Paper domain seam；`run_daily --mode paper` 仍归 M7

M6 提供纯、显式输入的 domain/service seam，职责如下：

```text
true-print bars + accepted M5 decisions + prior paper ledger
  + portfolio snapshot + circuit state + manual decisions
  -> deterministic paper order/position/trade/circuit events
```

M6 可以定义和测试 ledger repository 的原子、幂等 append/read 行为，但不接入当前 M4 `run_daily`、不改变其 `mvp_a_signal` manifest，也不在本里程碑引入 `shadow|paper` CLI 分支。这些编排与把 M5 decision 持久化到 M3 transition store 的工作保持为 M7。

M6 的输入、输出与持久化记录都必须带 `as_of`、`strategy_version`、`config_hash`；同一确定性输入重跑不得创建重复订单、重复 fill、重复仓位、重复 trade 或冲突的日终净值记录。缺失、身份不一致或同一业务键载荷不同一律 `DataValidationError` / fail closed，而不是选择一个较新值覆盖历史。

**非目标：** 自动真实下单、做空、杠杆、期权、部分成交、盘口/分钟级 path、M7 任务编排、收益回测和任意人工强制开仓。

### 2. `PrintBar` 是所有成交和止损价格的唯一入口；公司行动 adapter 缺失即不能 Paper

延续 M1 的类型边界：

- `Bar` / `AdjustedBar` 只可用于特征、EMA、ATR 与收盘条件；不得进入 fill、stop、gap 或 cash 计算。
- `PrintBar -> TradeableBar` 是 entry、exit、stop、MFE/MAE、gap 的唯一价格输入。
- M6 新增的 corporate-action adapter 必须显式输出 `Sequence[PrintBar]`；它不是把 `Bar` 强制转换为同字段对象。

对采用的 Yahoo 路径，adapter 至少接收 provider primary OHLCV、`actions=True` 的 `Stock Splits` 历史、请求区间和 observation cutoff，并遵守：

```text
split_factor(d) = product(split_ratio for action_date > d and action_date <= cutoff)
PrintBar[d].OHLC = provider_primary[d].OHLC * split_factor(d)
PrintBar[d].volume = provider_primary[d].volume / split_factor(d)
```

- action 生效日当天属于拆分后的 share unit，故仅乘 `action_date > d` 的拆分；不得用包含当日的边界猜测。
- 每个 split ratio 必须为有限正数，且 action 日期、symbol、session 都可验证；任何重复、未知、非正或覆盖不完整的 action history 均使整个请求失败。
- adapter 必须用至少一个已知拆分 fixture 断言 split 前/后的 `PrintBar` 真实 OHLCV 以及 action-date 边界；没有 fixture 证据不接受网络样例代替回归。
- retrieval 时点晚于历史 `as_of` 的 Yahoo 资料不是 point-in-time 回测证据。M6 可服务当前 Paper 日任务的审计重放；研究/历史回测仍受 M1 §1.1 的非 PIT 限制，不能借“已重建 PrintBar”取消该限制。
- `PrintBar`、actions、或二者与 provider session 不一致时，停止该 symbol 的 Paper 动作并生成可审计的 data-integrity halt；绝不退回 split-adjusted primary OHLC。

### 3. 成本模型必须先在新的冻结 config identity 中给出；本 ADR 不猜数值

宪法要求真实成本进入模拟，M5 也拒绝零成本候选。现有 `smm_v1_0_0.yaml` 没有成本和熔断数值，因此 M6 实现前必须新增一个**新的、不可覆盖历史的** config identity（推荐策略版本也随执行/风险语义评审决定）并由 schema 要求至少：

```yaml
execution:
  entry_slippage_bps: <positive Decimal>
  exit_slippage_bps: <positive Decimal>
  half_spread_bps: <positive Decimal>
  commission_per_share: <non-negative Decimal>
  max_open_gap_atr: 1.0              # 现有键，保留
risk:
  daily_loss_pause_r: <positive Decimal>
  drawdown_reduce_at: <0..1 Decimal>
  drawdown_stop_at: <0..1 Decimal>
```

数值必须来自这份新 config，不能在 Broker 代码、测试 helper 或环境变量中隐含默认。`entry_slippage_bps`、`exit_slippage_bps`、`half_spread_bps` 的精确值在本 ADR 中故意不伪造为“市场事实”；在 PR 评审中先确认保守值及版本纪律。`drawdown_reduce_at < drawdown_stop_at`、所有 bps 非负、以及所有 required keys 缺失即 schema fail closed。

在数值冻结后，日线模型固定为：

```text
buy_fill  = base_price * (1 + (half_spread_bps + entry_slippage_bps) / 10_000)
sell_fill = base_price * (1 - (half_spread_bps + exit_slippage_bps) / 10_000)
per-share commission is added to cash cost on both sides
```

所有金额使用 `Decimal`；禁止 float 四舍五入悄悄改变 size。M5 在 signal 日收到的 estimated entry/round-trip cost 与 M6 在实际 open 重算的模型必须来自同一 config identity；M6 实际成交仍以真实 open 重算，不能把 M5 估计当 fill。

### 4. 入场：先完成既有退出，再用实际 true-print open 重新检查，不得增仓

对 `risk_accepted` 的计划入场，signal `as_of=D` 只允许在下一 provider session `D+1` 的 `TradeableBar.open` 尝试。D+1 的开盘处理顺序固定为：

1. 读取并验证 D+1 的 required `PrintBar`、corporate action、已持仓 ledger 与 circuit state；任一不一致先进入 data-integrity halt，不产生新 fill。
2. 先执行 D 收盘已安排的卖出订单，再处理新入场；卖出释放的现金和新 entry 的风险预算必须使用同一 D+1 snapshot。
3. 对每个计划 entry，以 signal 日 true-print close reference 计算 `abs(actual_open - entry_reference) / ATR20(D)`。超过 `execution.max_open_gap_atr` 则取消，reason `paper_entry_gap_exceeds_limit`。
4. 以实际 open 与成本模型重算实际 entry、initial stop distance 和 estimated round-trip unit risk。若 actual open 不高于 initial stop，或 distance 不在 frozen `[min_stop_distance_atr, max_stop_distance_atr]`，取消，reason 分别为 `paper_entry_open_at_or_below_stop`、`paper_entry_stop_distance_out_of_bounds`。
5. 以同一批、M5 固定排序和 D+1 portfolio snapshot 重跑风险容量。实际成本、cash、heat、sector 和 cluster 仍受 M5 gate；gap 后可能缩量或取消。
6. 最终 `quantity <= M5 RiskDecision.quantity`。较有利的 open 也不能使仓位超过 M5 计划，除非未来另有明确 ADR 同时允许全组合风险复核后的上调。

取消和 manual skip 都是终态的 paper order record，并在 M7 以一条 `RISK_ACCEPTED -> CANCELLED` transition 记录。它们不消费现金，也不得在重跑时重复写入。

### 5. 日线事件顺序、止损与退出条件

每日对已有 position 固定使用下列顺序，防止同一根日线给出互相矛盾的结果：

```text
previous-close scheduled exits at today's open
  -> new entry fills at today's open
  -> same-day stop evaluation using today's true-print low
  -> today's close feature/EMA/time-stop evaluation
  -> schedule next-session-open exits
```

#### 5.1 Stop

stop 永不向不利方向移动。对当日仍 open 的 long position：

- `open <= stop`：这是 gap-through-stop，`base_exit = open`，再应用 sell cost；reason `paper_stop_gap_open`。不得假设成交在 stop。
- `open > stop and low <= stop`：`base_exit = stop`，再应用 sell cost；reason `paper_stop_triggered`。
- 否则继续持有。

若新 entry 在 D+1 open 后同日命中 stop，paper ledger 记录 entry 与 stop 两个事实，但 M7 signal log 只写一条 `RISK_ACCEPTED -> STOPPED` transition，附 `paper_entry_and_stop_same_session`。这保留 M3 的一日一跳约束，不用虚构中间持久化状态。

#### 5.2 收盘条件与下一开盘成交

EMA 条件是 feature 条件，不是成交价格：它只能比较同日 `AdjustedBar.adj_close` 与由截至同日资料计算的 `EMA20`。一旦 `adj_close < EMA20`，在当日 close 后安排下一 session open 的卖出，reason `paper_exit_ema20_close_below`；真正 fill 仍使用下一 session 的 `TradeableBar.open` 与 sell cost。

时间止损在 entry session 算第一个已完成持有 session。满 `exit.time_stop_days` 个 session 后，仅当：

```text
MFE_R < exit.time_stop_min_mfe_r
and adjusted close < actual entry fill
```

才安排下一开盘 exit，reason `paper_exit_time_stop`。`MFE_R` 使用从 entry session 至当前 session 的 `PrintBar.high` 和初始、不可上调的 per-share R（包含同一版本的预计 round-trip cost）计算。宪法中的“relative strength 明显恶化”目前没有冻结 metric，V1 不把它悄悄实现为另一个 exit；未来需单独 ADR/config。

同一日 stop 与 close condition 同时成立时，stop 先终结 position，不能再排 EMA/time exit。已有前一日安排的开盘 exit 又遇见低开时，先执行该 exit open；日线数据不足以假设一个更优的盘中止损顺序。

M7 lifecycle 允许实现下列事实映射，仍每 `(signal_id, as_of)` 最多一条：

```text
RISK_ACCEPTED -> ENTERED                 # 开盘填单且当日未终结
RISK_ACCEPTED -> STOPPED                 # entry 后同 session stop
ENTERED       -> ACTIVE | EXITED | STOPPED
ACTIVE        -> EXITED | STOPPED
```

`ENTERED -> ACTIVE` 仅表示仓位经过一个完成 session 后仍存续；terminal transition 必须由 paper trade/order 记录的日期与 reason 支撑。

### 6. Paper ledger 与 idempotency 业务键

M6 定义 append-only、可重放的 records；具体 SQLite/Parquet storage 选择可沿现有状态边界实现，但不允许 UUID 作为唯一去重机制。至少需要：

| 记录 | 必要事实 | 业务唯一键 |
|------|----------|------------|
| `paper_orders` | signal、purpose、scheduled session、planned/actual qty、status、reason codes、identity | `(signal_id, purpose, scheduled_session, config_hash)` |
| `paper_fills` | order、actual session、true-print base price、cost components、fill price、qty | `(paper_order_id, fill_session)` |
| `paper_positions` | entry fill、initial stop、qty、open/closed status、identity | `position_id` derived from entry `paper_order_id` |
| `paper_trades` | entry/exit fills、realized P&L/R、MFE/MAE、exit reason | `position_id` |
| `paper_equity_snapshots` | cash、marked equity、high-water mark、drawdown、circuit outcome | `(as_of, config_hash)` |
| `manual_decisions` | target signal/order、SKIP、reason、note、actor, as_of、identity | `(target_id, decision, as_of, config_hash)` |

同一业务键的完全相同 payload 是 no-op；不同 payload 是冲突而非 update。新写入必须先验证 referential identity、`as_of` session 和 config/strategy identity 一致。`paper_positions` 不可用新的开仓覆盖旧仓，`paper_trades` 不可覆盖不利结果。

人工操作在 V1 仅允许 `SKIP` 一个尚未 fill 的 `risk_accepted` entry。它必须有非空 reason code，允许文本 note 但不把 note 当规则；不能人工创建候选、改变 size、移动 stop、填补缺数据或替代风险拒绝。SKIP 通过同一 idempotency 规则记录并使该订单取消。

### 7. 熔断是可审计的 operational state，不篡改冻结 config

M6 计算一个显式 `CircuitState`，而不是悄悄改变 `risk.new_entries_enabled` 或编辑 config：

```text
as_of, strategy_version, config_hash,
realized_loss_r_for_session,
marked_equity, high_water_equity, drawdown,
new_entries_blocked, entry_risk_multiplier, reason_codes
```

- session realized loss 是该 session 内已平仓 paper trade 的 realized R 之和；小于 `-daily_loss_pause_r` 时，**下一** session 阻止新 entry，reason `circuit_daily_loss_pause`。
- marked equity = settled cash + 所有 open position 的 `qty * current PrintBar.close` 减去按同一 config 立即卖出的成本；high-water 只可上升。drawdown = `(high_water - marked_equity) / high_water`。
- drawdown 到达 `drawdown_reduce_at` 而未到 stop threshold 时，输出 `entry_risk_multiplier = 0.5` 与 `circuit_drawdown_reduce_risk`。M7 必须把该显式 operational multiplier 作为 risk input 传给 M5 的 sizing seam 并记录它；不得复制/修改 config hash，也不得让 Scanner 直接决定 quantity。
- drawdown 到达 `drawdown_stop_at` 时，输出 `new_entries_blocked = true`、`entry_risk_multiplier = 0`、`circuit_drawdown_stop_new_entries`，并要求 audit record；不自动清算既有仓位。
- data、actions、ledger 或 position reconciliation 不一致时，输出最高优先级 `circuit_data_or_position_integrity_halt`，立即阻止新 entry；不虚构 exit price。

优先级固定为 integrity halt > drawdown stop > daily-loss pause > drawdown risk reduction > normal。多个适用原因都要保留且排序稳定。熔断只影响新增 entry；既有仓位仍按 §5 的 stop/exit 条件处理，除非数据完整性使其无法安全计算，此时记录 halt 并升级人工处理，而不是假装已成交。

### 8. 实现验收与 M7 边界

M6 代码前先提交针对下列契约的 red tests，再实现最小模块：

1. known-split `actions` 重建为 `PrintBar`；缺 action、action-date 边界错误或 provider `Bar` 进入 fill seam 均 fail closed。
2. 计划 entry 用次日 true-print open；过大 gap、open at/below stop、stop-distance 越界均取消且不占 cash。
3. actual-open re-risk 只能缩量/取消，不能超过 M5 planned quantity；同批次仍不超 heat/sector/cluster。
4. entry 后同日 low 穿 stop 与 gap-through-stop 分别按指定 base price；止损不下移。
5. EMA 使用 adjusted close/EMA、fill 使用 next-session PrintBar open；time stop 的 10-session/MFE 条件与 stop 优先级正确，未定义 RS 条件不触发 exit。
6. 订单、fill、仓位、trade、manual SKIP 与 equity snapshot 完全重跑 no-op；同 key 不同 payload fail closed。
7. 4R 日损失、6% risk reduction、10% stop、data/position mismatch 的 circuit state 与 stable reason codes 正确；CircuitState 不能被 Scanner 绕过。

发布前仍运行目标测试、完整 pytest、ruff 与 `git diff --check`。M6 不更新 M4 run-daily manifest；M7 才负责以这个已验证 seam 连接 M5 decision、M3 transition store 和 `run_daily --mode shadow|paper`。

---

## 备选（未采纳）

| 方案 | 原因 |
|------|------|
| 直接把 provider-native Yahoo OHLC 用作 paper fill | 违反 M1 已接受的 `PrintBar` 类型边界，拆股前历史价格错误。 |
| 缺成本时按 0 或任意 bps 继续 | 违反宪法 §10 与 M5 成本契约，且把未审计假设变成风险结论。 |
| entry 用 signal close 或 future high/low 作为 fill | 违反无前视和次日开盘成交规则。 |
| 同日 entry/stop 写两条 signal transition | 违反 M3 `(signal_id, as_of)` 一日一跳不变量。 |
| 用调整后价格直接与 true-print stop 比较 | 混淆 feature 与 fill 价格语义，拆股后不可审计。 |
| 把 RS 恶化临时写成任意指标 | 宪法未冻结 metric；应先 ADR/config，而不是 Broker 中猜测。 |
| 熔断通过篡改 `risk.new_entries_enabled` 或 config hash 实现 | 把运行时组合事实伪装成策略配置，重放和审计会失真。 |
| manual decision 可以强制入场或改止损 | 违反“人工只能否决、不能加票”及止损纪律。 |

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-22 | proposed | M5 实现合并后提出 M6 真实成交、成本、退出、ledger、manual 与熔断契约；等待 Task Reviewer 裁定成本/熔断 config identity 与语义。 |
