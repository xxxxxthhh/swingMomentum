# ADR：M7 日任务编排、延后风险评估与审计 bundle 契约

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | accepted（rev.2；[PR #40](https://github.com/xxxxxthhh/swingMomentum/pull/40) Task Reviewer 复审接受） |
| 日期 | 2026-07-23 |
| 策略版本 | 不改变冻结阈值或 config identity；本 ADR 只冻结 M7 编排与审计语义 |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（§12.1、§26、§35、§53） |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)（M7 / PR8） |
| 前置决策 | [M3](./2026-07-22_m3_watchlist_and_signal_lifecycle.md)、[M4](./2026-07-22_m4_signal_report_and_daily_task.md)、[M5](./2026-07-22_m5_risk_engine_and_planned_sizing.md)、[M6](./2026-07-22_m6_paper_broker_contract.md) |
| 讨论依据 | [Issue #39](https://github.com/xxxxxthhh/swingMomentum/issues/39)；Task Reviewer comment `5055842914` |
| 变更摘要 | 冻结 M7 单 CLI 日序、延后消费 open-trigger backlog、CircuitState digest，以及 mode-aware manifest 的不可覆盖重放规则；不实现运行时代码。 |

---

## 背景

M4 已交付一个只产生 `mvp_a_signal` 的、连续 session、单 batch seal 的
`run_daily`。M5/M6 则有已验证的纯 Risk、Circuit 和 Paper seams，但两份 ADR
明确把它们接回日任务、把 RiskDecision 写回 M3 transition store、以及把
Paper ledger 纳入 manifest 留给 M7。

直接把这些模块接起来会遇到四个不能由代码临场决定的问题：

1. M3 对每个 `(signal_id, as_of)` 最多一条 transition；而 M4 已在 trigger
   日持久化 `WATCHLISTED -> TRIGGERED`。
2. M6 定义了「风险计划后的下一 session open」与 open-before-close 的 Paper
   顺序，却没有规定 M7 在何日消费 M4 的 `open_trigger` backlog。
3. `RiskExecutionContext` 已要求 `circuit_state_identity`，但尚未定义该
   identity 的跨平台、可重放编码。
4. richer mode 会增加 risk/circuit/Paper 产物；M4 的 manifest 又禁止同日
   完成产物被隐式覆盖。

本 ADR 只冻结这些编排与审计问题。它不改变入场/止损/成本阈值，不猜风险簇标签，
也不授权 live broker。

---

## 决策

### 1. M7 仍使用一个 `run_daily` CLI；三种 mode 的能力边界固定

M7 将在现有 CLI 上增加显式 `--mode shadow|paper`；省略 `--mode` 的调用保持
M4 的 `mvp_a_signal` 行为、参数、输出与重放语义完全不变。

| mode | 可写事实 | 明确禁止 |
|------|----------|----------|
| `mvp_a_signal`（默认） | M4 feature / report / signal transitions | RiskDecision、order、fill、position、trade |
| `shadow` | M4 事实 + CircuitState + RiskDecision + 当日 risk transition | order、fill、position、trade、live broker |
| `paper` | shadow 事实 + 已验证 M6 Paper ledger | live broker、做空、杠杆、期权 |

mode 是运行时操作选择，和 `--source` 一样不进入 `config_hash`，也不创建第二套
strategy identity。它必须写入 manifest 的 `execution_mode`。`paper_trading`
config 默认值本身仍不构成运行 paper mode 的授权；只有显式 CLI mode 才会选择该路径。

### 2. 选择延后风险评估（Option 1b）并保留 M3/M4 trigger 事实

对于在 session `D` 已被 M4 持久化为 `TRIGGERED` 的 logical signal，M7 不在
同一个 `(signal_id, D)` 再写 `RISK_ACCEPTED` 或 `RISK_REJECTED`。相反：

```text
session D:     WATCHLISTED -> TRIGGERED              # M4 已有事实
session X>D:   TRIGGERED -> RISK_ACCEPTED|RISK_REJECTED
               # X 是实际消费 open_trigger backlog 的 session
session X+1:   M6 的 next-session-open entry / re-risk（若 ACCEPT）
```

通常 `X` 是紧接 `D` 的下一 provider session；若数据完整性缺口或日任务恢复使
消费延后，`X` 就是实际评估日。`open_trigger` 因此继续表示尚未被 Risk 消费的真实
backlog，而不是一个只在异常日才有意义的遗留 bucket。

Circuit 不会把已调度在 `X` 消费的 backlog 变成 skip 或 requeue：它是该次
RiskDecision 的既有输入。`new_entries_blocked=true` 时，M7 必须把候选交给既有
`evaluate_risk_batch`，沿用 `risk_off_new_entries_blocked` 的 `REJECT` 结果，并在
`X` 写终局的 `TRIGGERED -> RISK_REJECTED`；不得把它描述为 circuit 的暂缓，或新增
未定义的再次排队/过期机制。未 block 的 multiplier 降低同样是该次 decision 的既有
输入，不改变消费日。这样 circuit 改变 decision，不改变消费 schedule。

这是对 M5 §8 在 **M7 编排时序** 的显式细化：该节所说的 risk transition 的
`as_of` 是 `EligibleCandidate` 实际有效、Risk Engine 实际运行的 session `X`，
不是原始 trigger session `D`。M3 的一日一跳、M4 的 `new_trigger` / `open_trigger`
报告和已经提交的 `WATCHLISTED -> TRIGGERED` 行均不被重写。

这不重启 M5 §8 所排除的「风险决策推迟到下一收盘」：被排除的是会破坏
`decision session -> next-session open` 耦合的开放式延后；Option 1b 始终保持
`RiskDecision(X) -> earliest X+1 open`，只把原 trigger 日 `D` 与 decision 日 `X`
区分开。

M7 首个实现只可从 run 开始前的 latest-state `TRIGGERED` 集合挑选 backlog；当日
scanner 新产生的 `TRIGGERED` 不得在同一日被风险消费。candidate adapter 只能使用
截至 `X` 已知且满足 M5/M6 PrintBar、成本、identity 与 stop-distance 契约的事实；
不满足时必须按已有的 fail-closed / 已冻结的生命周期语义处理，不能让 Scanner
直接给 quantity 或用 provider-native `Bar` 代替 `PrintBar`。

### 3. 每个 session 的单 CLI 因果顺序

一个 M7 `run_daily --as-of X` 必须在任何持久化前完成输入、identity、calendar、
previous seal 和 mode 的预检；随后按以下顺序在内存中推导：

```text
1. 已有 Paper 事实的 X-open 处理
   scheduled exits -> pending accepted entries -> same-session stops
2. X-close 的 mark、EMA/time-stop 判定与下次 open exit scheduling
3. 用步骤 1/2 的 settled trades、mark 和 integrity facts 得到 CircuitState(X)
4. 以 CircuitState(X) 消费 run 开始前的 TRIGGERED backlog，得到 RiskDecision(X)
5. 计算 M4 的 X-close features / scan / report rows
6. 组合 X 的所有 lifecycle transitions，恰好一次 append + batch seal
7. 提交 mode 所需 artifacts；manifest 最后写入
```

步骤 4 的 circuit state 只影响同样 `as_of=X` 的 risk decision：daily-loss pause
通过 `RiskDecision(X)` 到最早 `X+1` open 的既有 M6 lag 生效，不再另加一天延迟。
步骤 5 的新 trigger 不能反过来进入步骤 4。若步骤 1--5 任一输入不完整、同一业务键
有不同 payload、或任何 transition 不能组成 X 的完整 multiset，整次 run fail closed；
不得 seal 部分新的业务判断。

M4 的「M3 seal 先于报告 bundle、manifest 最后」恢复规则继续适用。M7 可以在
transition seal 前提交已验证、可重放的 M6 ledger append；若随后 bundle/manifest
失败，精确重跑只能把同 payload ledger 与 seal 视为 no-op 并重建缺失 bundle。
不同 payload 一律 conflict，不能覆盖不利事实。

### 4. CircuitState identity 是 canonical payload 的 SHA-256

`circuit_state_identity` 定义为以下 payload 经
`smm.report.format.dump_json_deterministic` 序列化（sorted keys、固定 separators、
ASCII、末尾换行）后的 SHA-256 hex digest：

```text
as_of                         ISO-8601 date string
strategy_version              string
config_hash                   string
realized_loss_r_for_session   fixed six-decimal string
marked_equity                 fixed six-decimal string
high_water_equity             fixed six-decimal string
drawdown                      fixed six-decimal string
new_entries_blocked           JSON boolean
entry_risk_multiplier         fixed six-decimal string
reason_codes                  existing frozen priority order list
```

Decimal text follows the existing `format_float` six-decimal convention, but
is produced by a dedicated Decimal formatter rather than `repr`, implicit
`str`, float conversion, or platform-dependent formatting. The formatter must
be the only CircuitState-to-text path and must reject non-finite values before
serialization. `reason_codes` are emitted exactly in M6's accepted priority
order; they are never re-sorted or converted to a set.

The full payload, digest and `(as_of, config_hash)` business key must be
auditable. On exact rerun, recomputing the payload/digest is a real equivalence
check; same key with a different payload or digest is a fail-closed conflict.
No wall-clock time, random ID, mutable config copy or source-path data may enter
the payload.

### 5. Manifest is mode-aware per session, not mutable per completed day

Artifact roots remain exactly `strategy_version/config_hash`; changing mode does
not change either component. A *session manifest* is nonetheless mode-specific:

- `mvp_a_signal` retains M4's existing three artifact hashes:
  `report_csv`、`report_markdown`、`features_snapshot`.
- `shadow` additionally hashes a canonical CircuitState artifact and the full
  RiskDecision artifact; its manifest also records `circuit_state_identity`.
- `paper` includes all shadow hashes plus each M6 Paper ledger artifact that
  is relevant to that session (orders/fills/positions/trades/equity or a sealed
  empty ledger artifact where applicable).

Thus one root may contain earlier M4-only dates and later richer-mode dates
without a config identity change. This does **not** permit a completed
`as_of=X` bundle to grow in place: a rerun of the same date must use the exact
same mode, manifest shape, hashes and payloads or fail closed. Switching a
completed day from `mvp_a_signal` to shadow/paper, or shadow to paper, is a
conflict; it requires a fresh, valid later session rather than an overwrite.

Every artifact named by a manifest must be finalized and hash-verified before
the manifest is atomically written. Files not named in that session's manifest
are not a successful M7 output, even if a ledger append exists elsewhere.

### 6. Risk-cluster taxonomy remains a separate, fail-closed follow-up

This ADR deliberately does not add a manual cluster map. Existing M5 behavior
remains authoritative: missing or blank cluster labels normalize to the shared
`unclassified` budget, never to an unlimited budget or guessed theme. A future
seed table needs its own dated source, schema, update/review protocol and
candidate-adapter tests before it can label real symbols.

---

## 实现验收契约

后续 M7 runtime PR 必须先写回归，并至少证明：

1. trigger 在 D 的 persisted transition 不会在 D 再写 risk transition；它仅在
   backlog 被消费的 X 写一条 `TRIGGERED -> RISK_*`。
2. 当日新 trigger 不会被同日 risk consumer 读取；`open_trigger` 在 risk 前后均可
   审计地解释。
3. `new_entries_blocked` 不会 defer/requeue X 的 backlog：它经既有 Risk Engine 写
   `risk_off_new_entries_blocked` 的终局 `RISK_REJECTED`；仅数据完整性缺口或恢复会
   使实际消费日变为较晚的 X。
4. X 的 CircuitState 只影响 X 的 risk decision，entry 仍不早于 X+1 true-print
   open，且没有 Scanner 绕过 Risk Engine。
5. 同一 CircuitState payload 在不同进程重放得到相同 digest；任一字段、Decimal
   表示或 reason-code 顺序变化都会被检测。
6. 默认 M4 mode 的现有 byte-for-byte replay 不变；同日 mode switch 或 artifact
   hash 不同 fail closed；后续 session 使用 richer mode 可在同一 root 产生完整、
   mode-aware manifest。
7. shadow 不创建任何 Paper ledger；paper 不连接 live broker；未知 risk cluster
   仍共享 `unclassified` 限额。

发布前仍须运行目标测试、完整 pytest、Ruff 与 `git diff --check`，并在 PR 附上
完整 diff、当前 HEAD、CI 与 reviewer evidence。

---

## 非目标

- 本 PR 的运行时代码、CLI、config、schema、阈值或已有 artifact 重写；
- live broker、做空、杠杆、期权、回测结论；
- `EligibleCandidate` 的价格/stop/cost 公式重定义；
- 风险簇标签的猜测、自动分类或 seed 数据提交；
- 覆盖已完成的 M4/shadow/paper session bundle。

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-23 | proposed | Builder 根据 Issue #39 提出 M7 编排 ADR；Task Reviewer comment `5055842914` 已接受 canonical identity/mode boundary，并建议 Option 1b；等待对本 ADR 的整体复审。 |
| 2026-07-23 | accepted（rev.2） | Task Reviewer comment `5056348471` 对精确 HEAD `7755c7259b7da143ee849af9329eb41ace3189b4` 完成复审并接受；PR #40 已合并。 |
