# ADR：M4 信号日报与 MVP-A 日任务

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | proposed |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0（不 bump；M4 不改变信号规则或冻结阈值） |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（原则 1、3、9、11、14；§16–§17、§23） |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)（M4、PR5、MVP-A 完成定义） |
| 关联决策 | [M1](./2026-07-22_m1_data_provider_and_universe.md)、[M2](./2026-07-22_m2_feature_engine_and_regime.md)、[M3](./2026-07-22_m3_watchlist_and_signal_lifecycle.md) |
| 变更摘要 | 冻结 M4 日任务顺序、连续 session 门、日报快照/事件语义、空日与 same-day 重跑、产物提交顺序及 M4/M7 CLI 边界 |

---

## 背景

M1–M3 已交付 provider/cache/validation、features/regime、scanner/watchlist 与只追加 lifecycle 日志。M4 要把这些公共 seam 串成一个可持续日更、可回放、可人工阅读的 MVP-A 交付。

计划只写了：

```text
ingest → validate → features → regime → watchlist/scan → lifecycle → report
日报 Markdown/CSV：新触发 / 观察池 / 状态变化
Shadow 语义（无建仓）
```

这不足以直接编码。以下问题若不先冻结，会让实现得到互不兼容的审计链：

1. 「观察池」是当日事件还是收盘后的当前状态？静默续存的 `WATCHLISTED` 没有新 transition，单看当日事件会把它漏掉。
2. M3 要求空 transition 日也落 batch seal；调用方若把空 tuple 当成「不用 append」，同日修订仍可绕过 R3。
3. feature snapshot 当前允许覆盖；若先覆盖 snapshot、后在 transition batch 上发现冲突，旧报告的复现证据已被新数据破坏。
4. 单日任务若跳过一个交易日，watchlist 可能漏掉该日触发；仅凭最终 `age` 无法补回被跳过的决策。
5. M4 的「无建仓」不等于 M7 的 Risk/Paper shadow；现在暴露 `paper` 或伪造风险计划会让 Scanner 绕过未来 Risk Engine。

---

## 决策

### 1. 一个公共日任务；既有子命令保留为诊断 seam

M4 新增一个顶层公共入口（CLI 名称采用 `smm run-daily --as-of YYYY-MM-DD`），在同一进程内依次调用既有生产 seam：

```text
resolve config/provider/universe/calendar
  → fetch or read bars + validate
  → compute features + regime in memory
  → scan_session
  → append_transitions（即使 transitions == () 也必须调用）
  → write immutable feature/report bundle
  → write completion manifest last
```

`smm ingest` 与 `smm features` 继续存在，供诊断和局部重跑；它们不是 M4 完成一次日任务的证据。M4 测试必须从 `run-daily` 的公共 seam 进入，不得建设一条 test-only pipeline。

`source=synthetic` 与 `source=market` 只决定 provider/universe，不改变后续 scanner/report 代码路径。market 路径仍只能经 `DataProvider`，业务代码不得直接调用 yfinance。

### 2. 连续 session 门：不得静默跳日或回填旧日

transition store 的 batch seal 日期是已处理日历的事实来源，包括 transition 数为 0 的日期。

| store 状态 | 允许的 `as_of` |
|------------|----------------|
| 无任何 seal | 任一有效 provider session；它成为该观察 run 的起点 |
| 已有最新 seal `D` | `D`（精确重跑）或 provider calendar 中紧接 `D` 的下一 session |
| 请求早于 `D` | fail closed；不得在已有未来状态后回填 |
| 请求晚于 `D` 且跳过一个或多个 session | fail closed；先逐日补齐 |

理由：漏跑日可能本应产生 `WATCHLISTED → TRIGGERED` 或硬过滤失效。直接跳到后一天无法从最终状态推回中间决策。

实现需给 store 暴露只读 batch metadata；不得用「当日有无 transition row」推断是否处理过，因为 sealed empty day 是一等状态。

### 3. config identity 在一个 artifact root 内冻结

一旦 root 中存在任何 batch seal 或完成 manifest，后续日任务必须使用相同的 `strategy_version` 与 `config_hash`。不同 identity 必须换新 root，或进入未来显式迁移/人工裁决流程；M4 不提供覆盖开关。

这条在「当前没有 active signal」时仍适用。不能因为没有可重放实体，就把两个 config 的日期悄悄拼成同一观察窗口。

provider source、universe snapshot identity/hash 与 git commit（可获得时）写入 manifest 供审计，但不进入 `signal_id`。

### 4. 日报同时表达当前状态与当日事件，但 CSV 每个 signal 只出现一次

Markdown 固定三节，CSV 用互斥 `bucket` 表达相同内容：

| bucket / Markdown 节 | 定义 |
|----------------------|------|
| `new_trigger` / 新触发 | 当日 transition 的 `to_state == TRIGGERED`，含 `DETECTED → TRIGGERED` 与 `WATCHLISTED → TRIGGERED` |
| `watchlist` / 观察池 | 当日全部转移重放后的当前状态为 `WATCHLISTED`；必须包含没有新 transition 的静默续存 |
| `terminal_change` / 终态变化 | 当日进入 `EXPIRED` / `CANCELLED` / `EXITED` / `STOPPED` 等终态；M4 实际主要为 `EXPIRED` |

同一 signal 在一份 CSV 中只进一个 bucket。`new_trigger` 优先于通用「状态变化」，避免同一 transition 重复两行。历史终态不在后续每日重复展示。

零信号不是失败：只要上游数据、regime、calendar 与 batch seal 完整，仍必须写出带表头的空 CSV、Markdown 中明确的 `0` 计数、以及完成 manifest。现金/无机会是宪法允许的有效结果。

### 5. 静默 watchlist 需要当日 observation；不得拿旧 transition 属性冒充

M3 的 silent stay 没有 transition row，因此「重放 latest transition 再展示」只能拿到进入 watchlist 当天的 breakout/relvol，不能代表今天。

M4 实现应让 scanner 的当日结果同时返回非持久化的 observation（或等价的单一公共计算结果），供报告消费。不得在 reporting 模块复制一套触发公式。

每个日报行至少包含：

```text
as_of, bucket, symbol, signal_id, state, watchlist_entry,
from_state, to_state, reason_codes,
close, breakout_level, relative_volume, extension_atr,
momentum_score, relative_strength_score, regime,
strategy_version, config_hash
```

- silent `watchlist` 的 `from_state` / `to_state` 为空，但当日 trigger diagnostics 与失败条件必须来自当日 observation。
- 基本面尚无可靠源时写明确的 `not_evaluated`/N/A，不得填有利默认。
- `MomentumScore` 与 `RelativeStrengthScore` 直接展示 M2 结果；M4 不计算 `TotalScore`，也不为 `trend_trigger_weight` 发明分子。
- 展示排序仅为：bucket 固定顺序；bucket 内 `MomentumScore` 降序、`RelativeStrengthScore` 降序、symbol 升序，missing 排末。排序不改变 signal 状态或资格。

### 6. transition seal 先于报告 bundle；完成 manifest 最后写

M4 不假装跨 Parquet 文件拥有数据库事务。采用可恢复的提交顺序：

1. 所有输入先在内存中计算并通过验证；此时不得覆盖已完成产物。
2. 调用 `append_transitions(..., as_of=, strategy_version=, config_hash=)`，包括空 batch。
3. seal 成功后，把 feature snapshot、CSV、Markdown 写入同目录临时 bundle；校验后原子 rename 到日期目录。
4. `manifest.json` 最后原子写入，含各产物 SHA-256、transition batch digest/count 与审计 identity。只有 manifest 存在且 hashes 匹配，日任务才算成功。

若步骤 2 成功而 3/4 失败，下一次用相同输入重跑：transition append 为 no-op，bundle 可重新生成。若输入已变化，M3 batch conflict 先 fail closed，旧 snapshot/report 不得被覆盖。

已存在 complete manifest 时：

- 逐字节一致的重跑为 no-op；
- 任一产物、hash、identity 或当日计算结果不一致都 fail closed；
- M4 不提供 `--force`、原地重写或删除历史的通道。

### 7. M4 是 `mvp_a_signal`，不是没有 Risk 的伪 shadow/paper

manifest 明确记录 `execution_mode: mvp_a_signal`。M4：

- 不生成 order / fill / position；
- 不做仓位、heat、板块/簇上限或 market-regime 拒仓；regime 只展示；
- 不接受 `paper` 选项，也不构造可绕过未来 Risk Engine 的「临时风险计划」。

M7 在 Risk/Paper seam 存在后，再把同一 `run-daily` CLI 扩展为 `--mode shadow|paper`。这是一项向后兼容扩展；M4 不提前暴露一个语义虚假的 mode。

### 8. N 日回放必须证明内容与字节可复现

M4 门禁至少覆盖一个连续的合成 session 序列，并从同一公共日任务 seam 逐日执行：

1. 出生为 watchlist；
2. 至少一个 silent continuation；
3. 后续触发或过期；
4. 至少一个 sealed empty day；
5. 第二个全新 root 重放同一序列，CSV/Markdown/manifest（除明确排除的运行时字段外）逐字节一致；
6. 同 root 精确重跑不改 bytes；跳日、旧日回填、同日修订、config 漂移全部 fail closed。

manifest 不记录 wall-clock、随机 run id 或绝对临时路径。否则「逐字节可复现」会被无业务意义字段破坏。

---

## 非目标

- Risk Engine、sizing、heat、板块/风险簇拒绝（M5）
- Paper 次日开盘、滑点、跳空取消、持仓/出场（M6）
- `manual_decisions`（MVP-B）
- 自动真实下单、做空、杠杆、期权
- HTML dashboard、周报、绩效/alpha 结论
- 在 M4 把 regime 变成信号拒绝条件
- 通过覆盖旧日报来「修正历史」

---

## 备选（未采纳）

| 方案 | 原因 |
|------|------|
| 日报只列当日 transitions | 漏掉 silent `WATCHLISTED`，无法回答当前观察池 |
| 日报只列 current state | 看不到当日新触发与终态变化 |
| CSV 分成互相重叠的三份 | 同一 signal 重复，机器对账需再发明优先级 |
| reporting 重新计算 trigger | 形成 Scanner 与日报两套业务逻辑 |
| 空 transition 日不 append | 重开 M3 R3 的 silent-day 幂等漏洞 |
| 先写/覆盖 feature snapshot，再 append transitions | 后续冲突会留下新 snapshot + 旧审计链的混合历史 |
| 允许跳 session | 丢失中间日可能发生的触发/失效且不可恢复 |
| M4 提前支持 `paper` 或 position plan | Risk Engine 尚不存在，构成绕过未来风险门的路径 |
| manifest 带当前时间/随机 id | 破坏相同输入的逐字节复现 |

---

## 实现验收契约

1. 回归先行：先证明 silent watchlist 会被 event-only 报告漏掉，再扩 scanner/report seam。
2. `append_transitions` 在每个成功扫描日都被调用，空 tuple 也不例外。
3. 同一 root 的 session 连续性与 config identity 在写任何新产物前校验。
4. 报告不复制 trigger/hard-filter/score 公式。
5. 所有阈值来自冻结 config；报告代码不得改变资格或排名基准。
6. 全部失败路径非零退出且不产出看似正常的 complete manifest。
7. 目标测试、完整 pytest、ruff、`git diff --check` 全绿；PR 附 N 日回放产物摘要与 hashes。

---

## 开放项（请 Task Reviewer 裁决）

1. `run-daily` 的 market 路径是否在 M4 内主动补 fetch，还是严格 cache-only、由 `ingest` 另行调度。本文推荐：仍经同一 `DataProvider` 获取/缓存，以满足 Plan 的单入口编排；绝不直接依赖 yfinance。
2. artifact root 的默认目录是否统一为 `data/runs/<strategy_version>/<config_hash>/`。本文推荐按 identity 分 root，避免调用方误把 config 漂移拼接进同一观察窗口。
3. 完成 manifest 是否应纳入 git commit 为 nullable 字段。本文推荐记录可获得的 SHA；脱离 git 安装时明确为 `unknown`，但不因此拒绝运行。

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-22 | proposed | M4 实现前提交评审；冻结日任务、日报、连续 session 与 artifact commit 语义 |
