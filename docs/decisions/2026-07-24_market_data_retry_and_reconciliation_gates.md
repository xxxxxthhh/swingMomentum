# ADR：行情供应商重试、失败粒度与真实资金对账门槛

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | proposed（Issue #73 方向已裁决；等待本 ADR PR 复审） |
| 日期 | 2026-07-24 |
| 策略版本 | SMM-V1.0.0 / SMM-V1.1.0（不 bump；重试政策改变 `config_hash`） |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（原则 9、11、13；§12.4、§37、§48、§53、§59） |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)（M1、M4、Stage A/B/C 路线） |
| 前置决策 | [M1 数据源与股票池](./2026-07-22_m1_data_provider_and_universe.md)、[M4 日任务](./2026-07-22_m4_signal_report_and_daily_task.md)、[M7 审计 bundle](./2026-07-23_m7_daily_orchestration_and_audit_contract.md) |
| 讨论依据 | [Issue #73](https://github.com/xxxxxthhh/swingMomentum/issues/73)；Task Reviewer comment `5066183031` |
| 变更摘要 | 保持整轮 fail-closed；增加短时有限重试与人工延迟重跑分层；禁止默认剔除异常股票；把独立数据源对账冻结为真实资金前置门槛 |

---

## 背景

2026-07-23 的真实 market run 中，Yahoo 对 `ANET` 返回
`low (172.97) > open (172.77)` 的不合法日线。`Bar` 的 OHLC invariant
正确拒绝该数据，整轮没有产生信号、Paper ledger 或完成 manifest。

约一天后重新下载同一 session，Yahoo 返回的 `low` 已修正为与
`open` 相同的 `172.77`。这说明供应商异常至少分为两类：

1. 秒级网络、限流或短暂响应抖动，可能在同一进程内恢复；
2. 供应商较慢的数据修订，短间隔重试无法恢复，只能等待后重新运行。

同时，本策略使用横截面百分位排名。缺少一个普通成分股并不是只少一行：
它会改变其他成员的百分位、动量/相对强度分数和阈值穿越结果。因此不能把
单只股票的坏数据静默降级为局部 exclusion。

PR #72 只改善异常的 symbol/session 可读性，不改变本 ADR 决定的运行语义。

---

## 决策

### 1. 失败粒度保持整轮 fail-closed

任一当日必需的 benchmark、板块 ETF 或普通成分股在供应商读取、标准化或
§12.4 校验后仍不合法，整次 `ingest` / `run-daily` 必须失败：

- 不写入未经校验的行情缓存；
- 不产出看似成功的 signals、Paper ledger 或 completion manifest；
- 不修补、插值、裁剪或猜测 OHLCV；
- 不因标的是普通成分股而自动剔除后继续横截面计算。

benchmark、板块 ETF 与普通成分股使用同一套硬失败语义。这里不采用
“benchmark 硬失败、成员自动排除”的双重标准。

### 2. 两层恢复机制各自处理不同时间尺度

#### 2.1 进程内有限重试：只处理短暂抖动

未来实现新增冻结配置：

```yaml
market_data_retry:
  max_attempts: 3
  backoff_seconds: [2, 8]
```

语义无歧义：

- `max_attempts: 3` 是**总尝试次数**，包含立即执行的第一次；
- 第 2 次尝试前等待 2 秒，第 3 次尝试前等待 8 秒；
- 不使用随机 jitter；同一配置的尝试上限和退避序列固定；
- 每次尝试使用同一个 provider、symbol、请求窗口、`as_of`、股票池快照与
  冻结 config，重新向供应商请求；不得从上一次无效 payload 继续计算；
- 只有最终完整通过标准化与校验的 payload 才允许写入缓存；
- 三次均失败后，聚合有序的 attempt 证据并抛出
  `DataValidationError`，整轮停止。

允许重试的失败仅限于供应商边界：

- transport / HTTP / 限流类供应商错误；
- 空响应、截断响应或供应商 payload 无法标准化；
- 供应商 payload 触发的 OHLC / §12.4 数据校验错误。

以下失败不得重试，因为重复网络请求不会改变其事实：

- config、CLI 参数或策略版本错误；
- 股票池快照缺失/过期；
- optional market dependency 未安装；
- artifact、manifest、transition、ledger 或 config identity 冲突；
- 调用顺序错误导致的未知 benchmark calendar；
- 已验证缓存或本地审计链自身损坏。

实现测试必须注入 clock/sleeper，不允许单测真实等待 10 秒。

#### 2.2 人工延迟重跑：处理供应商的慢修订

短时重试耗尽后，系统不在进程内长时间等待，也不自动调度数小时后的第二套
任务。操作员根据异常上下文等待供应商修订，然后使用**相同**
`as_of`、股票池快照、strategy version 与 config 重新运行。

这次重新运行是新的 operator invocation，不是前一次进程的第 4 次 attempt。
若前一次已失败，它没有 completion manifest，新 invocation 仍必须从完整
fail-closed 校验链开始。

现有行情 runbook 的缓存重置只在以下场景使用：

- 缓存写入发生在旧校验规则生效前；
- 缓存完整性或覆盖证据本身不可证明。

被当前校验拒绝的 payload 在写缓存前已经失败，通常只需等待后重新运行；
不得把递归删除整个缓存变成每次供应商异常的默认动作。实现 PR 同步更新
runbook，把“普通重跑”与“证据不足时缓存重建”分成两个明确步骤。

### 3. 重试政策改变 `config_hash`，不改变策略版本

`market_data_retry` 是数据治理/供应商运维护栏，不改变过滤、评分、信号、风险、
成交或退出规则。因此：

- 它必须进入冻结 YAML 与 Pydantic schema；
- 任一次数或退避变化必须改变 `config_hash`；
- 它不 bump `SMM-Vx.y.z`；
- 不同 retry config 的运行不得写入同一个 artifact root；
- 实现不得提供未进入 config hash 的 CLI 覆盖参数。

这沿用 M1 §2.4 已冻结的规则：数据治理参数改变 config identity，但不改变
strategy identity。

### 4. 实际 attempt 是操作遥测，不进入 completion manifest

重试政策由 `config_hash` 绑定；实际运行用了几次尝试取决于当时的外部供应商
状态。把实际 attempt count 写入 completion manifest，会让相同最终验证数据在
一次成功与二次成功时产生不同 bytes，破坏 M4/M7 的同输入重放契约。

因此本 ADR 决定：

- M4/M7 completion manifest schema 与 artifact key 集合**不变**；
- 每次尝试必须输出一条结构化 operator log，至少包含：
  `provider`、`symbol`、请求 `start/end`、`attempt/max_attempts`、
  `outcome`、稳定的错误分类和 symbol/session 上下文；
- 最终失败的 `DataValidationError` 必须携带有序 attempt 摘要；
- 日任务调度方必须保留标准输出/错误日志；这些日志是操作审计，不是策略输入、
  信号 artifact 或重放 identity；
- 日志不得包含完整原始 dataframe、密钥或本地绝对路径。

后续若要把供应商稳定性做成长期指标，应另立 observability ADR；不得借此扩大
当前 completion manifest。

### 5. 默认 symbol exclusion 暂缓

自动剔除坏数据成员的方案不进入本次实现。未来只有同时满足以下条件才可重开：

- 独立 ADR 明确横截面百分位变化；
- 新语义具备版本化与实验隔离；
- 有真实 replay 量化 expected/included universe 差异对信号的影响；
- exclusion 原因、数量与阈值可审计；
- 不与当前执行中的 Shadow/Paper 统计混合。

在此之前，“少一个成员继续跑”视为改变策略输出，不是无害容错。

### 6. 第二数据源与 reconciliation 是 Stage C 前置门槛

Phase 1 的 Shadow/Paper 观察可继续使用当前单一供应商和本 ADR 的 fail-closed
恢复流程；但进入 Constitution §37 的 Stage C Small-Capital Pilot 前，必须完成：

1. 独立行情/公司行动数据源；
2. provider provenance；
3. 明确的逐字段 reconciliation / tolerance / 冲突规则；
4. provider-native `Bar` 与 true-print `PrintBar` 类型边界验证；
5. 双源冲突的 fail-closed 路径和审计证据；
6. 新 ADR、实现、回归与晋级 review。

该门槛不授权 live broker，也不阻塞当前 Phase 1 纸上验证；它是从 Paper 进入
真实资金前不可跳过的治理 gate。

---

## 实现切片

本 ADR 接受后，单独的实现 PR 只做 A1：

1. schema + 冻结 YAML 的 retry config 与 config-hash 回归；
2. provider 边界的有限重试，sleeper 可注入；
3. 每次 attempt 的结构化 operator log；
4. 最终失败的有序摘要；
5. runbook 的短时重试 / 延迟人工重跑 / 条件式缓存重建分层；
6. 回归证明：第 2 次恢复、三次耗尽、非重试类只调用一次、无效 payload
   不入缓存、失败不产出 completion manifest。

该实现 PR 不加入 symbol exclusion、不接第二供应商、不改变信号、风险、Paper、
manifest shape 或策略版本。

---

## 后果

### 正面

- 秒级供应商抖动不再要求人工立即重跑；
- 较慢修订仍保持显式人工恢复，不用长时间阻塞任务；
- 数据失败不会通过自动剔除改变全市场排名；
- retry policy 可由 config hash 审计；
- completion manifest 继续保持确定性。

### 代价

- 一次全市场 run 可能因单个成员持续异常而停机；
- 3 次短时重试不能解决一天量级的供应商修订；
- 在第二供应商完成前，Stage C 被明确阻塞。

---

## 未采纳方案

| 方案 | 原因 |
|------|------|
| 无限重试或长时间 sleep | 掩盖停机、阻塞调度、没有确定上限 |
| 随机 jitter | 不利于运行政策复现；当前单机任务无并发惊群需求 |
| 普通成员失败后自动 exclusion | 改变其余成员横截面排名与信号语义 |
| 自动修改 OHLC 使其满足 invariant | 伪造市场事实，违反原则 11 |
| 实际 attempt count 写进 manifest | 外部供应商波动会破坏相同最终数据的 byte replay |
| 立即接第二供应商 | 是正确的真实资金前置工程，但不属于 A1 的最小切片 |

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-24 | proposed | Issue #73 Task Reviewer comment `5066183031` 批准 A1 方向、暂缓 B、确认 C 为 Stage C gate；本 ADR 冻结实现前的具体边界，等待 PR 复审 |
