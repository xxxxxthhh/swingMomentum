# ADR：指数成分变更对极端成交量的确定性验证

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | accepted |
| 日期 | 2026-07-24 |
| 策略版本 | SMM-V1.0.0 / SMM-V1.1.0（不 bump；验证政策改变 `config_hash`） |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（原则 9、11、13；§12.3、§12.4、§37） |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)（M1、M4、MVP-A 真实日 batch、Stage A/B） |
| 前置决策 | [M1 数据源与股票池](./2026-07-22_m1_data_provider_and_universe.md)、[行情重试与对账门槛](./2026-07-24_market_data_retry_and_reconciliation_gates.md)、[M4 日任务](./2026-07-22_m4_signal_report_and_daily_task.md)、[M7 审计 bundle](./2026-07-23_m7_daily_orchestration_and_audit_contract.md) |
| 讨论依据 | [Issue #77](https://github.com/xxxxxthhh/swingMomentum/issues/77)；Task Reviewer comment `5067778024` |
| 变更摘要 | 25 倍门槛继续作为异常检测器；仅当版本化的 S&P 官方成分变更事实精确解释生效前一交易日或生效日放量时，原始成交量可不经修改地通过；缺失或冲突仍整轮 fail-closed |

---

## 背景

PR #72 合并后，Builder 在 merge commit
`d0426f96db4b9dfcc8f38c248ea20f90c84dc062` 上执行了
`as_of=2026-07-23` 的真实 market daily task。运行使用冻结的
`SMM-V1.0.0` 配置与 config hash
`4d9e51a0fb38a1747e4a251c48097b9431b9a0f9fe2af4c835518f8cbead982a`。

SPY 日历前置摄取成功后，任务在 `CASY` 停止：

| 日期 | Volume |
|------|-------:|
| 2026-04-06 | 312,400 |
| 2026-04-07 | 710,700 |
| **2026-04-08** | **8,688,600** |
| 2026-04-09 | 707,700 |
| 2026-04-10 | 574,100 |

请求窗口的中位成交量为 `338,700`；2026-04-08 的比值为
`25.6527900797x`，超过冻结的
`validation.max_volume_spike_ratio: 25.0`。三次供应商尝试返回相同值，
OHLC 均满足 invariant，因此这不是 #73 的慢修订 OHLC 错误。

Task Reviewer 独立核实了行情，并找到 S&P Global 的官方公告：

- 公告日期：2026-04-06；
- CASY 将加入 S&P 500；
- 生效时间：2026-04-09 开盘前；
- 生效前最后一个 provider session 正是 2026-04-08。

一手来源：
[S&P Global — Casey's General Stores Set to Join S&P 500, 2026-04-06](https://press.spglobal.com/2026-04-06-Caseys-General-Stores-Set-to-Join-S-P-500-DigitalOcean-Holdings-to-Join-S-P-MidCap-400-Broadstone-Net-Lease-to-Join-S-P-SmallCap-600)。
公告明确列出 S&P 500、Addition、CASY 与 2026-04-09 effective date。

被动指数资金在生效前一交易日集中调仓，为这次极端放量提供了公开、确定且
可审计的原因。它不证明所有大成交量都正确，但证明“超过静态倍数”不能独自
区分坏数据与真实市场事件。

继续等待也不是可行的恢复方案。当前 daily lookback 约 433 个自然日，
CASY 2026-04-08 预计到 2027-06 中旬才滑出窗口，足以阻断 Constitution §37
建议的 Stage A/B 验证周期。

---

## 决策

### 1. 25 倍阈值保留为异常检测器，不再单独充当最终真伪裁决

`max_volume_spike_ratio` 继续检测需要额外证据的异常。默认路径仍与当前一致：

```text
volume / series_median <= threshold
    -> 通过既有 volume validation

volume / series_median > threshold
    -> 必须进入本 ADR 的确定性验证
    -> 验证不完整、冲突或不匹配时整轮 fail-closed
```

本 ADR 不提高 `25.0`，不按样本选取 `30.0` 或其它“刚好通过”的数字。指数加入、
剔除与季度调仓会重复发生；仅提高静态倍数会把问题推迟到下一只股票，并可能
最终使门槛失去意义。

门槛触发也不是“自动豁免”。它把输入从普通验证路径转入一个更严格、需要额外
point-in-time 事实的验证路径。

### 2. 首个允许的确定性规则仅覆盖 S&P 500 官方成分变更

首个实现只接受以下事件来源与事件类型：

| 维度 | 冻结范围 |
|------|----------|
| 权威来源 | S&P Global / S&P Dow Jones Indices 官方公告 |
| 指数 | S&P 500 |
| action | `addition` 或 `deletion` |
| 标的 | 官方记录中的精确 ticker |
| 验证 session | effective date 前最后一个 provider session 或 effective date 当天 |
| 公告时点 | 官方公告日期必须 `<=` 被验证 session |

第三方新闻、PR Newswire 转发、搜索摘要、社交媒体、成交量本身或“看起来像调仓”
均不构成执行依据。它们可以辅助人工研究，不能让 bar 通过验证。

Nasdaq-100、S&P 400/600、其它指数、ETF rebalance、财报、并购、增发或其它新闻
事件不在首个规则内。未来扩展必须新增 accepted ADR 或明确修订本 ADR 的 source
catalog、时序和测试，不得把“官方指数事件”泛化为任意叙事解释。

### 3. 匹配算法必须精确且无前视

对于 volume spike session `S`，只有全部满足下列条件，才可接受原始 bar：

1. `Bar` 已通过 OHLC、价格、session、复权因子、排序、重复记录与 split artefact
   等所有其它既有检查；
2. `volume` 为有限正数；零、负数或非有限值不能进入事件验证；
3. 存在唯一、已版本化的官方事件记录，ticker 与 bar symbol 完全一致；
4. 事件 index 为 `S&P 500`，action 为 `addition` 或 `deletion`；
5. 事件 official announcement date `<= S`，禁止用 `S` 之后才公开的解释回填历史；
6. 对事件 effective date `E`，`S` 必须是 provider calendar 中 `E` 的前一
   session 或 `E` 本身，即
   `S ∈ {previous_provider_session(E), E}`；
7. 事件记录、provider calendar 或同一业务键不得重复或互相冲突；
8. 原始成交量必须原样保留；不得裁剪到阈值、替换为邻日、中位数或其它供应商值。

CASY 的匹配为：

```text
announcement = 2026-04-06
spike session S = 2026-04-08
effective session E = 2026-04-09
next_provider_session(S) == E
index/action/symbol = S&P 500 / addition / CASY
```

CASY 使用的是窗口中的 `T-1` 分支；同一条规则也允许 `S == E` 的生效日分支。
两个分支都必须满足唯一事件、公告日期 `<= S`、ticker/index/action 精确匹配及
所有其它 fail-closed 约束，不因多一个候选 session 放宽真实性门槛。

该规则只说明极端成交量有一个当时已公开的指数调仓原因，因此可以把原始成交量
当作市场事实继续验证与计算。它不产生额外 momentum 分数、信号加分或人工加票。

### 4. 事件日历必须是版本化的 point-in-time 输入，不允许运行时抓网页

执行路径不得在 daily run 中临时搜索网页或抓取新闻。首个实现使用签入仓库的
带日期事件快照，例如：

```text
configs/market_events/YYYY-MM-DD_sp500_constituent_changes.csv
```

规范化记录至少包含：

```text
event_id
source_published_date
effective_date
index_name
action
symbol
source_url
source_title
```

要求：

- 真相源是规范化快照 + git commit；`source_url` 指向官方一手公告；
- 同一 event business key
  `(index_name, action, symbol, effective_date)` 必须唯一；
- 快照选择只能使用 `snapshot_date <= as_of` 中最新的一份；
- 不得使用未来快照、运行时网页内容或本地未版本化补丁；
- 官方事实缺失时维持 fail-closed，由人工提交新快照并走 review；
- 历史快照不可覆盖或删除；修订以新快照表达并保留来源。

这是数据验证输入，不是 alpha feature。事件事实不能直接进入排序、trigger、
Risk 或 Paper。

### 5. 每次成功运行必须绑定政策与实际验证事实

仅把规则写进代码不足以审计一次运行为何放行 25 倍以上的 volume。
后续实现必须：

1. 在冻结 config 中新增显式 policy，例如：

   ```yaml
   validation:
     max_volume_spike_ratio: 25.0
     volume_spike_verification:
       policy: official_sp500_constituent_change_v1
       session_offsets: [-1, 0]
   ```

2. policy、允许的 index/action/source catalog 与 session offsets 进入
   Pydantic config 并改变 `config_hash`；不得提供不入 hash 的 CLI override；
3. daily bundle 写出确定性的 `market_data_verifications.json`，即使为空也存在，
   每条至少包含：
   `symbol`、`session`、原始 volume、median、ratio、threshold、`event_id`、
   action、effective date、event snapshot id 与 event snapshot SHA-256；
4. completion manifest 绑定该 artifact 的 SHA-256，并绑定事件快照 id/digest；
5. 同一 `(as_of, strategy_version, config_hash)` 重跑必须得到 byte-identical
   verification artifact；不同 payload 不得覆盖已完成 bundle；
6. 失败尝试仍只写 operator log，不写伪完成 verification artifact 或 manifest。

`market_data_verifications.json` 记录“输入为何被验证为可用”，不表示人工 override，
也不修改原始 Parquet bar。

### 6. 版本与评估隔离

本规则改变数据治理与可用性边界，不改变突破、相对量、评分、风险、仓位、成交或
退出定义。因此沿用 M1 §2.4：

- 不 bump `SMM-Vx.y.z`；
- 新 policy/config 字段必须改变 `config_hash`；
- 新旧 config hash 的 Shadow/Paper 结果不得混合统计；
- 已完成的旧 artifact bundle 保持不可变；
- manifest/artifact shape 变化只适用于新 config identity；
- 当前 `as_of=2026-07-23` canonical run 在 ADR 与实现合并前继续 fail-closed。

若后续研究发现“指数事件本身应改变信号或评分”，那是策略逻辑变化，必须另立
研究与版本决策，不能从本数据验证规则顺带进入。

---

## 实现切片

本 ADR 接受后，后续实现 PR 最小范围为：

1. event snapshot schema、loader、point-in-time selector、唯一性与 digest；
2. frozen config / Pydantic policy 与 config-hash 回归；
3. volume anomaly checker 的显式“detect → verify or fail”边界；
4. `market_data_verifications.json` 与 manifest digest；
5. active runbook 的事件快照刷新与失败处置；
6. 使用 CASY 2026-04-08 的确定性 fixture，不在 CI 访问网络；
7. 完整 N-day replay / exact-rerun 证明。

至少需要下列回归：

| 场景 | 结果 |
|------|------|
| CASY 2026-04-08 + 精确官方 addition 记录 | 原始 `8,688,600` 不变地通过，并写 verification |
| 生效日当天的 spike + 精确官方记录 | 原始 volume 不变地通过，并写 verification |
| 同样 bar、无事件记录 | 整轮 fail-closed |
| 记录公告日晚于 spike session | fail-closed（禁止前视） |
| spike session 不在 effective date 的 `{T-1, T}` 窗口 | fail-closed |
| ticker/index/action 不匹配 | fail-closed |
| duplicate/conflicting event record | fail-closed |
| 零、负数或非有限 volume | fail-closed，事件记录不得放行 |
| 普通 25 倍以下成交量 | 走原路径，无 verification row |
| 25 倍以上 Nasdaq-100-only 事件 | 首个 slice fail-closed |
| exact rerun | artifact 与 manifest byte-identical / no-op |

实现 PR 不提高阈值、不自动剔除 symbol、不抓取运行时网页、不接第二行情供应商、
不改变信号/风险/Paper 规则，也不授权 live broker。

---

## 后果

### 正面

- 合法的指数调仓放量不会长期阻断 Stage A/B；
- 大成交量仍必须有 point-in-time、版本化、一手事实，而不是凭叙事放行；
- 原始 market bar 不修补、不裁剪，横截面股票池不缩水；
- config hash、事件快照 digest、verification artifact 与 manifest 形成完整审计链；
- 首个规则范围窄，可用 CASY 真实样本做 hermetic 回归。

### 代价

- 需要人工维护官方事件快照；漏更会有意地停机；
- manifest 与 artifact bundle 增加稳定字段/文件；
- 首个 slice 只解决 S&P 500 成分变更，未来其它指数/事件仍可能停机；
- 单供应商行情限制仍存在；Stage C 双源 reconciliation gate 不变。

---

## 未采纳方案

| 方案 | 原因 |
|------|------|
| 把阈值从 25 调到 30/50 | 对单一样本过拟合；周期性调仓仍会撞到新阈值 |
| 直接删除 `max_volume_spike_ratio` | 会让真实数据损坏失去入口检查 |
| CASY ticker 白名单 | 不可复用、不可解释未来事件，等同手工加票 |
| 超阈值只告警继续 | 不能区分坏数据与真实事件，违反原则 11 |
| 自动剔除异常 symbol | 改变全横截面排名；新纳入时反向移除成分股尤其错误 |
| 运行时网页搜索/新闻 NLP | 不可稳定重放，越过 system boundary |
| 第三方新闻即可放行 | 不是本 slice 的权威事实源 |
| 立即接第二行情供应商 | Stage C 的正确方向，但不是本次最小验证规则 |

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-24 | proposed | Issue #77 Task Reviewer comment `5067778024` 批准 C 方向；冻结 S&P 官方成分变更日历的首个确定性验证规则，等待 PR 精确 HEAD 复审 |
