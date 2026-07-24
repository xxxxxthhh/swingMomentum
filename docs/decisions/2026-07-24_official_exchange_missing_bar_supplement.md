# ADR：官方交易所单日行情补充

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | accepted（用户批准；Issue #85 Builder receipt `5069177583`；最终 feature PR 复审待完成） |
| 日期 | 2026-07-24 |
| 策略版本 | SMM-V1.0.0 / SMM-V1.1.0（不 bump；政策改变 `config_hash`） |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（原则 9、11、13；§12.4、§37） |
| 前置决策 | [M1 数据源与股票池](./2026-07-22_m1_data_provider_and_universe.md)、[行情重试与对账门槛](./2026-07-24_market_data_retry_and_reconciliation_gates.md)、[M4 日任务](./2026-07-22_m4_signal_report_and_daily_task.md)、[M7 审计 bundle](./2026-07-23_m7_daily_orchestration_and_audit_contract.md) |
| 讨论依据 | [Issue #85](https://github.com/xxxxxthhh/swingMomentum/issues/85) FISV 真实运行阻塞；Builder comments `5069139028`、`5069177583` |
| 变更摘要 | 只允许签入仓库的官方交易所快照补充单一、精确的 provider session 空洞；必须绑定完整 OHLCV、SEC 身份连续性、provider 复权证据、config hash 与 completion manifest；任何缺失或冲突继续 fail-closed |

---

## 背景

2026-07-23 canonical real-market run 对 `FISV` 连续三次得到相同结果：

- 请求窗口：`2025-05-16..2026-07-23`；
- 唯一缺失 session：`2025-11-12`；
- provider：Yahoo / yfinance；
- 结果：完整性校验拒绝，未跳过 symbol，未生成 completion bundle。

这不是休市日。Fiserv 官方公告与 SEC Form 8-K Item 3.01 均确认：

- NYSE `FI` 在 2025-11-10 收盘后结束交易；
- 同一 Class A common stock 在 2025-11-11 起转到 Nasdaq；
- 新 ticker 为 `FISV`。

Nasdaq 官方 chart API 对 2025-11-12 返回：

| 字段 | 值 |
|------|---:|
| Open | 64.20 |
| High | 64.87 |
| Low | 63.11 |
| Close | 64.38 |
| Volume | 6,244,651 |

Yahoo 在相邻 2025-11-11 与 2025-11-13 session 均返回
`Adj Close == Close`，因此缺口 session 的 provider-compatible
`adj_factor` 为 `1.0`。等待后再次执行完整三次 provider retry，缺口仍精确复现。

直接跳过 FISV 会改变全股票池横截面百分位；前向填充、插值或把缺口当休市会
伪造市场事实。另一方面，让一个已被官方交易所完整公开的孤立历史空洞永久阻塞
Phase 1 Shadow/Paper 验证，也不增加安全性。

---

## 决策

### 1. 只补“单一、精确、已审阅”的 provider 空洞

冻结政策 `official_exchange_isolated_missing_bar_v1` 只在以下条件全部满足时生效：

1. benchmark calendar 证明该日期是 session；
2. 缺口位于标的首根与末根 provider bar 之间；
3. 同一 symbol/window 最多缺失一个 session；
4. point-in-time 快照存在唯一 `(symbol, session)` 记录；
5. 记录包含完整正值 OHLCV，并满足既有 `Bar` invariant；
6. bar 来源为冻结的官方交易所 host；
7. SEC 一手文件证明同一 registrant、security class 与 ticker 在该 session 已生效；
8. 相邻 provider session 明确证明 `Close == Adj Close`，补充记录本身也固定
   `adj_close == close` 与 `adj_factor == 1.0`；
9. 快照日期 `<= as_of`；
10. provider/cache 已存在同 session bar 时，必须与快照逐字段完全一致，否则
    fail-closed。

当前精确 source catalog：

```yaml
official_bar_supplement:
  policy: official_exchange_isolated_missing_bar_v1
  max_missing_sessions_per_symbol: 1
  allowed_bar_source_hosts: [api.nasdaq.com]
  allowed_identity_source_hosts: [www.sec.gov]
  adjustment_method: adjacent_provider_equal_close_v1
```

catalog 是精确值，不是可任意追加的 allowlist。新增交易所、身份来源或 adjustment
方法必须重新 review，并改变 config hash。

### 2. 补充数据是离线、不可变的审计输入

runtime 不访问 Nasdaq、SEC 或其它网页。规范化事实签入：

```text
configs/official_bar_supplements/YYYY-MM-DD_official_bar_supplements.csv
```

快照至少固定：

- supplement id、symbol、session；
- 原始 OHLCV、`adj_close`、`adj_factor`；
- 官方 bar URL/title；
- registrant CIK、identity effective date、SEC URL/title；
- adjustment method；
- 前后相邻 provider session 的 Close / Adj Close 证据。

历史快照不可覆盖；修订必须新增日期更晚的快照。重复 supplement id、重复业务键、
未来 session、错误 host、无效 CIK、未生效身份、未夹住缺口的相邻 session，或
不能证明 factor 1 的记录均整份拒绝。

### 3. provider 与快照必须 reconciliation，不是无条件 override

同一逻辑用于首次抓取和 cache replay：

```text
provider session 缺失 + 唯一精确 supplement
    -> 插入原始官方 bar
    -> 运行全部既有 §12.4 校验
    -> 只有全链通过后才允许写缓存

provider/cache 已有该 session
    -> 与 supplement 完整 Bar 比较
    -> 完全一致：继续并重放 verification
    -> 任一字段冲突：整轮 fail-closed
```

因此本政策不会让缓存洗掉来源：后续 cache hit 仍生成同一
`official_bar_supplement` verification 与 snapshot digest。

### 4. completion bundle 必须绑定补充事实

成功运行的 `market_data_verifications.json` 对每根补充 bar 记录：

- symbol/session 与原始 OHLCV；
- `adj_close` / `adj_factor`；
- supplement/event id；
- 官方 bar URL；
- registrant CIK、identity effective date、SEC URL；
- adjustment method 与相邻 provider 证据；
- snapshot id 与 SHA-256。

manifest 的 `market_data_snapshots.official_bar_supplement` 必须绑定同一 snapshot
id/digest，verification artifact 本身继续由 artifact SHA-256 绑定。

### 5. 不改变交易策略或真实资金门槛

本 ADR：

- 不改变 50% price guard、25x volume guard 或任何信号阈值；
- 不剔除 symbol，不缩小股票池，不改变横截面排名；
- 不改变 Risk、Paper、true-print、订单、止损或退出语义；
- 不授权 live broker；
- 不算完成 Stage C 的双源 reconciliation 门槛。

Stage C 仍需要独立、系统性的第二供应商、公司行动、逐字段 tolerance 与全股票池
冲突处理。本 ADR 只处理 Phase 1 中经逐条人工审阅的孤立历史空洞。

---

## 回归与验收

至少覆盖：

| 场景 | 结果 |
|------|------|
| FISV 2025-11-12 唯一缺口 + 精确官方快照 | 补齐原始 OHLCV 并生成 verification |
| cache replay | 重放相同 verification/snapshot digest |
| provider 已有相同 bar | 继续，证据不丢失 |
| provider 任一字段与快照冲突 | fail-closed |
| 同一 symbol 缺失两个 session | fail-closed |
| 错误 source host / CIK / identity date / adjustment evidence | fail-closed |
| `as_of` 早于第一份快照 | 不读取未来证据，走原 provider 校验路径 |
| 完整 canonical run | 无 skipped symbol，生成 manifest、verification artifact 与报告 |

---

## 未采纳方案

| 方案 | 原因 |
|------|------|
| 前向填充、插值、邻日复制 | 伪造市场事实 |
| 把缺口当休市 | benchmark calendar 与 Nasdaq 官方 bar 均证明该日交易 |
| 跳过 FISV | 改变横截面排名与信号语义 |
| runtime 请求 Nasdaq API | 破坏离线重放与 point-in-time 审计 |
| 放宽为任意数量缺口 | 从孤立修复变成未受控第二数据源 |
| 视为完成 Stage C 双源门槛 | 当前只覆盖审阅过的单点事实，不是系统性 reconciliation |

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-24 | accepted | 用户明确批准全部 bounded 数据补充政策；Issue #85 Builder receipt `5069177583` 冻结边界 |
