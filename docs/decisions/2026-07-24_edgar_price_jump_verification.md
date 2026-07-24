# ADR：EDGAR 重大协议对极端价格跳变的确定性验证

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | proposed |
| 日期 | 2026-07-24 |
| 策略版本 | SMM-V1.0.0 / SMM-V1.1.0（不 bump；验证政策改变 `config_hash`） |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（原则 9、11、13；§12.3、§12.4、§37） |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)（M1、M4、MVP-A 真实日 batch、Stage A/B） |
| 前置决策 | [M1 数据源与股票池](./2026-07-22_m1_data_provider_and_universe.md)、[行情重试与对账门槛](./2026-07-24_market_data_retry_and_reconciliation_gates.md)、[M4 日任务](./2026-07-22_m4_signal_report_and_daily_task.md)、[M7 审计 bundle](./2026-07-23_m7_daily_orchestration_and_audit_contract.md)、[指数事件成交量验证](./2026-07-24_index_change_volume_spike_verification.md) |
| 讨论依据 | [Issue #82](https://github.com/xxxxxthhh/swingMomentum/issues/82)；Task Reviewer comment `5068470938` |
| 变更摘要 | 50% 门槛继续作为异常检测器；首个规则只允许 SEC EDGAR Form 8-K Item 1.01、精确时序及独立证券身份映射共同验证真实价格跳变；任何缺失、晚到或冲突仍整轮 fail-closed |

---

## 背景

PR #81 合并后，Builder 在 merge commit
`7d9c270f434cd125063980bedbe3aa94656a6949` 上重新执行
`as_of=2026-07-23` 的真实 market daily task。CASY 与 CRH 的极端成交量均已通过
accepted 的 S&P 500 事件验证，任务随后在 `ECHO` 停止：

| 日期 | Open | High | Low | Close | Volume |
|------|-----:|-----:|----:|------:|-------:|
| 2025-08-25 | 29.950001 | 30.070000 | 29.340000 | **29.879999** | 2,493,700 |
| **2025-08-26** | **54.110001** | **55.189999** | **50.619999** | **50.869999** | **46,579,100** |

close-to-close move 为约 `70.2477%`，超过冻结的
`validation.max_abs_daily_return: 0.50`。OHLC 内部自洽，三次供应商尝试返回同一
结果；这不是单一字段错乱，也没有 split signature。任务按既有规则正确地
fail-closed，未写 completion manifest 或成功 bundle。

SEC EDGAR accession
[`0001415404-25-000035`](https://www.sec.gov/Archives/edgar/data/1415404/000141540425000035/tmb-20250825x8k.htm)
记录：

- registrant CIK：`0001415404`；
- 当时交易 symbol：`SATS`；
- Form：`8-K`；
- Item：`1.01 — Entry into a Material Definitive Agreement`；
- acceptance datetime：`2025-08-26 06:31:18 ET`，早于当日 regular session；
- 事项：以约 `$22.65B` 向 AT&T 出售 3.45 GHz 与 600 MHz 频谱牌照。

EchoStar 后来宣布同一公司股票自 `2026-06-24` 起由 `SATS` 改为 `ECHO`，CUSIP
不变；该事实同时见
[Nasdaq 发布页](https://www.nasdaq.com/press-release/echostar-changing-stocker-ticker-sats-echo-marking-companys-next-era-earth-and-space)
与
[issuer ticker-change notice](https://ir.echostar.com/news-releases/news-release-details/echostar-changing-stocker-ticker-sats-echo-marking-companys-next)。
Yahoo 当前已把历史价格追溯到 `ECHO`；因此 provider bar 使用当前 symbol，但事件
发生时的 EDGAR cover page 使用历史 symbol。这个差异不能靠字符串猜测，必须由
独立的证券身份映射证明。

当前 validator 的注释已经说明：真实的 60% gap 会发生，但必须先检查，不能静默
进入打分。问题不是是否保留异常门槛，而是怎样在不把“新闻叙事”变成通用后门的
前提下，让一个已由官方 filing、严格时序与一致行情共同证明的真实市场 print
继续通过。

---

## 决策

### 1. 50% 阈值保留，不按 ECHO 样本提高

`max_abs_daily_return: 0.50` 继续作为异常检测器：

```text
abs(close / previous_close - 1) <= threshold
    -> 通过既有 price validation

abs(close / previous_close - 1) > threshold
    -> 进入本 ADR 的确定性验证
    -> 任一证据缺失、晚到、冲突或不匹配时整轮 fail-closed
```

本 ADR 不把阈值提高到 `0.75`，不自动跳过 symbol，不裁剪或替换 OHLC，也不允许
人工布尔 override。验证成功只说明原始 bar 是一个可审计的真实市场事件，不给
momentum score、trigger、Risk 或 Paper 任何额外加分。

### 2. 首个规则的唯一主来源是 SEC EDGAR，且只允许 Form 8-K Item 1.01

首个 policy 的允许目录冻结为：

| 维度 | 冻结范围 |
|------|----------|
| 官方系统 | `www.sec.gov` / `sec.gov` 的 EDGAR archive |
| Form | `8-K` |
| Item | `1.01 — Entry into a Material Definitive Agreement` |
| registrant identity | 精确 CIK |
| 事件时序 | EDGAR `ACCEPTANCE-DATETIME` |
| 验证 session | filing 在 regular close 前 accepted 时为当日；regular close 后 accepted 时只能为下一 session |

Item `2.01`、`7.01`、`8.01`，10-Q/10-K、S-4、issuer IR、新闻稿、财经媒体、
搜索摘要、社交媒体以及“价格看起来像有消息”都不在首个允许目录中。即使同一
8-K 同时包含其它 Item，放行记录也必须精确绑定其中的 Item `1.01`。

issuer press release、交易对手公告与媒体报道可以作为人工 review 的交叉引用，
但不能作为 runtime 验证主来源，也不能在缺少 EDGAR Item `1.01` 时让 bar 通过。
未来若要支持其它可枚举 Item 或事件类别，必须新增 accepted ADR 或明确修订本
ADR，不能用自由文本“重大事件”扩展。

### 3. EDGAR acceptance timestamp 决定最早可验证 session，禁止前视

事件记录必须保存 EDGAR 原始 `ACCEPTANCE-DATETIME`，并转换到上市地时区后匹配
provider calendar：

```text
accepted before regular open on session S  -> earliest_session = S
accepted during regular session S          -> earliest_session = S
accepted after regular close on session S  -> earliest_session = next_provider_session(S)
accepted on non-session day                -> earliest_session = next_provider_session(date)
```

首个规则只允许 price jump session 精确等于 `earliest_session`。不允许用 filing
之后第二、第三天仍在上涨的叙事回填，也不允许使用 `as_of` 之后才加入的事件快照。

对 ECHO：

```text
EDGAR acceptance = 2025-08-26 06:31:18 ET
regular session   = 2025-08-26
earliest_session  = 2025-08-26
price jump        = 2025-08-26
```

### 4. 历史 ticker 到当前 ticker 必须走独立证券身份契约

price event 以 registrant CIK 和 filing 当时的 historical symbol 为身份；provider
bar 则可能使用股票池当前 symbol。两者不同时，必须存在独立、版本化的
`SecurityIdentityMapping`，至少包含：

```text
mapping_id
registrant_cik
security_class
old_symbol
new_symbol
effective_date
source_published_at
source_url
source_title
cusip_continuity
```

映射只在以下条件全部满足时有效：

1. old/new 两端绑定同一 registrant CIK 与同一证券类别；
2. 官方 ticker-change 记录明确给出 old symbol、new symbol 与 effective date；
3. 主来源是上市交易所官方 notice，或 issuer 官方 ticker-change notice 加同一 CIK
   的 EDGAR before/after cover-page 交叉证明；
4. 若来源声明 CUSIP 不变，映射必须记录并验证该 continuity；若 CUSIP 变化，则
   首个 policy 不支持并 fail-closed；
5. event session 必须早于 mapping effective date，provider current symbol 必须
   在 effective date 当日或之后有效；
6. 同一 CIK/security/effective date 的映射必须唯一；链式改名必须逐段连续，不能
   跳过中间 symbol；
7. 缺记录、错误 historical symbol、错误 current symbol、CIK 不同、证券类别
   不同、日期倒置、duplicate 或 conflicting mapping 均 fail-closed。

ECHO/SATS 的身份依据与价格事件依据是两组独立事实：2025-08-26 的 EDGAR filing
证明重大协议，2026-06-24 的 ticker-change 记录与同一 CIK/CUSIP continuity
证明当前 `ECHO` 与历史 `SATS` 是同一证券。任何一组都不能替代另一组。

### 5. 原始行情还必须通过一致性与重复抓取门槛

事件证据不能修复坏 bar。price jump 只有在以下条件全部满足时才可验证：

1. previous/current bar 已通过有限、正数、OHLC invariant、session、排序、重复
   记录、adj factor 与 split artefact 等既有检查；
2. 三次确定性 provider 尝试返回同一 affected-session OHLCV payload，或命中
   由同一校验契约验证过的 immutable cache；
3. current open/high/low/close 均位于同一数量级，且
   `low <= open/close <= high`；
4. 没有同日 split action；存在 split 或 corporate-action 冲突时仍 fail-closed；
5. EDGAR event、identity mapping 与 bar/session 唯一精确匹配；
6. 原始 OHLCV 原样保留，不缩放、不 winsorize、不替换邻日或第二供应商值。

本规则不是“EDGAR 里有一条 Item 1.01 就接受该公司任意历史价格跳变”。session、
CIK、historical symbol、current symbol、identity mapping 与 acceptance timestamp
必须共同唯一匹配。

### 6. 所有事件与映射都是版本化 point-in-time 输入

执行路径不得运行时抓 SEC、issuer 或交易所网页。后续实现使用签入仓库的累计
快照，例如：

```text
configs/price_events/YYYY-MM-DD_edgar_item_1_01.csv
configs/security_identities/YYYY-MM-DD_symbol_mappings.csv
```

要求：

- 真相源是规范化快照 + git commit；URL 只指向允许的一手来源；
- 快照选择只使用 `snapshot_date <= as_of` 中最新的一份；
- 每份较新快照必须累计保留仍可能落入 lookback 的既有记录；
- 历史快照不可覆盖或删除，修订只能追加新快照；
- event business key 至少为
  `(registrant_cik, accession_number, item_number, acceptance_datetime)`；
- mapping business key 至少为
  `(registrant_cik, security_class, old_symbol, new_symbol, effective_date)`；
- event、mapping、source URL 或 digest 重复/冲突时整轮 fail-closed；
- runtime 缺事实时停止并由人工提交新快照走 review，不做网络补全。

### 7. 成功运行必须把放行原因写入审计 bundle

后续实现必须在冻结 config 中新增显式 policy，并让全部 policy catalog 进入
`config_hash`。不得提供不入 hash 的 CLI override。成功 bundle 的
`market_data_verifications.json` 对每次 price-jump verification 至少写出：

```text
verification_kind
current_symbol
historical_symbol
registrant_cik
session
previous_close
raw_ohlcv
move
threshold
accession_number
form
item_number
acceptance_datetime
event_snapshot_id
event_snapshot_sha256
identity_mapping_id
identity_snapshot_id
identity_snapshot_sha256
```

completion manifest 必须绑定 verification artifact、event snapshot 与 identity
snapshot 的 SHA-256。exact rerun 必须 byte-identical；失败尝试只写 operator
log，不得写伪完成 verification artifact 或 manifest。

### 8. 版本与边界

本 ADR 改变数据治理与可用性边界，不改变选股、突破、评分、风险、仓位、成交或
退出定义：

- 不 bump `SMM-Vx.y.z`；
- policy/config/schema 改变 `config_hash`；
- 不同 config hash 的 Shadow/Paper 结果不得混合统计；
- 已完成旧 bundle 保持不可变；
- 当前 `as_of=2026-07-23` canonical run 在 ADR 与后续实现合并前继续
  fail-closed；
- 本 ADR 不授权 live broker、自动真实下单或盘中监控。

---

## 后续实现切片

本 ADR accepted 后，另开一个独立实现 PR，最小范围为：

1. EDGAR Item 1.01 cumulative snapshot schema、loader、PIT selector 与 digest；
2. security identity cumulative snapshot、独立 loader 与唯一性/连续性检查；
3. frozen config / Pydantic policy catalog 与 config-hash 回归；
4. price-jump checker 的显式 `detect -> verify or fail` 边界；
5. verification artifact、manifest digest 与 active runbook；
6. 使用 ECHO/SATS 的 hermetic fixture，不在 CI 访问网络；
7. canonical N-day replay 与 exact-rerun 证明。

至少需要下列回归：

| 场景 | 结果 |
|------|------|
| ECHO 2025-08-26 + 精确 Item 1.01 + SATS→ECHO 映射 | 原始 bar 不变地通过并写 verification |
| 同样 bar、无 EDGAR event | fail-closed |
| Form 不是 8-K 或 Item 不是 1.01 | fail-closed |
| acceptance 在 regular close 后但 jump 是同日 | fail-closed（禁止前视） |
| jump 不在 `earliest_session` | fail-closed |
| EDGAR CIK 或 historical symbol 不匹配 | fail-closed |
| 缺少 SATS→ECHO mapping | fail-closed |
| mapping CIK/security/CUSIP continuity 错误 | fail-closed |
| duplicate/conflicting event 或 mapping | fail-closed |
| 同日存在 split 或 OHLC invariant 失败 | fail-closed |
| provider 三次 payload 不一致 | fail-closed |
| 50% 以下普通价格变化 | 走原路径，无 verification row |
| exact rerun | artifact 与 manifest byte-identical / no-op |

实现 PR 不修改 `max_abs_daily_return`，不支持其它 8-K Item，不抓运行时网页，不
改变 signal/risk/Paper 规则，也不混入其它真实运行 blocker。

---

## 后果

### 正面

- 保留 50% 异常门槛，又不把已由 EDGAR、精确时序和一致行情证明的真实 print
  当作坏数据永久阻断；
- 首个目录只有一个 SEC Form/Item，避免“任意公司新闻都能解释价格”的开放后门；
- CIK 与独立 ticker mapping 把公司事件真实性和证券身份连续性拆开验证；
- 原始行情不修补，运行原因由 config hash、两个快照 digest、verification artifact
  与 manifest 完整绑定；
- ECHO/SATS 提供真实、可复现且不依赖 CI 网络的回归样本。

### 代价

- 新重大事件与 ticker change 需要人工维护累计快照并走 review；
- 只支持 Item 1.01，会保守地拒绝其它真实但未纳入目录的极端事件；
- EDGAR acceptance 时区、session cutoff 与身份链增加 schema 和测试复杂度；
- 在实现合并前，真实 daily run 仍会按设计停在 ECHO。

### 风险控制

- 不因单个样本提高全局阈值；
- 不让 issuer 新闻稿或媒体叙事单独放行；
- 不把同名/改名猜测当作证券身份；
- 不允许未来 filing、未来快照或 after-close filing 回填同日；
- 不在失败路径产出伪完成结果。

---

## Alternatives Rejected

| 方案 | 拒绝原因 |
|------|----------|
| 把阈值提高到 75% | 按样本调门槛，会弱化全市场异常检测且不能证明数据真实性 |
| ECHO 永久 allow-list | 不可泛化、不可审计，等同 symbol 级人工 override |
| 任意 issuer press release 即放行 | 来源与事件类型开放，新闻叙事会成为通用后门 |
| 任意 8-K Item 即放行 | Item 集合过宽，无法形成可枚举政策目录 |
| 只按公司名称或当前 ticker 匹配 | ticker 可复用/变更，可能把 A 公司历史事件错误绑定给 B 公司 |
| runtime 搜 SEC/新闻 | 非确定、不可重放、可能前视，且网络失败会改变策略输出 |
| 直接删除 ECHO | 改变股票池与横截面结果，掩盖而非解决数据治理问题 |
