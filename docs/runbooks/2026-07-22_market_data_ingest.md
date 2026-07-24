# Runbook — 行情摄取（`smm ingest`）

| 字段 | 值 |
|------|-----|
| 文档类型 | runbook |
| 状态 | active |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0 |
| 关联决策 | [../decisions/2026-07-22_m1_data_provider_and_universe.md](../decisions/2026-07-22_m1_data_provider_and_universe.md) |
| 关联 issue | [#5](https://github.com/xxxxxthhh/swingMomentum/issues/5) |

---

## 离线（默认）

```bash
smm ingest --as-of YYYY-MM-DD --cache-dir data/cache
```

走确定性合成路径，不联网。用于验证管道本身。

## 真实行情

```bash
pip install -e ".[market]"
smm ingest --as-of YYYY-MM-DD --source market --cache-dir data/cache
```

### benchmark 必须在缓存中

`market_regime.benchmark`（SPY）**不是股票池成员**——宪法 §10 限定股票池为普通股，SPY 是 ETF。但两处依赖它：

1. **市场状态**（regime）直接读它；
2. **交易日历** `get_calendar()` 从它派生。

`smm ingest --source market` 会**自动把 benchmark 排在抓取列表最前**，无需手工指定。若用 `-s` 限定了符号，benchmark 仍会被补入。

**benchmark 未缓存时 `get_calendar()` 返回空列表。** 那是「未知」而非「无交易日」：把空日历传给 `check_session_dates` 会 **fail-closed** 并明确提示 benchmark 未缓存，而不是静默放行。

### 板块 ETF（M2 起）

M2 的 `sector_benchmarks`（11 只 SPDR）同样是**基准而非股票池成员**，必须由 ingest 路径显式拉取，且**不得**写入股票池快照。

## 缓存覆盖语义

缓存记录**请求过的窗口**，而不只是落盘的 bar。

- `covers(start, end)` 只在 `[start, end]` 落在**单个**已记录窗口内时为真；
- 有 bar **不等于**范围完整——某天没有 bar，可能是休市，也可能是被截断，仅凭 bar 无法区分；
- **有覆盖记录也不等于每个 session 都有 bar**。记录的是「请求过什么」，不是「供给完整」。IPO、退市、上游截断仍可能在已覆盖窗口内留下空洞；查出它们要靠校验层 + benchmark 日历（M2 接入）；
- 覆盖是**区间列表**而非单一区间。两次不相交的抓取不会把中间从未请求过的部分算作已覆盖——否则「缺尾巴变错数字」的问题会被搬到中段；
- 仅当两个窗口之间**不存在未请求的工作日**时才合并（周五↔周一之间只有周末，视为连续）。间隔中若有工作日则保持分离，即便那天恰好是节假日——无日历时二者无法区分，宁可多抓一次；
- 未记录窗口的写入（如直接灌合成数据）不会被当作已覆盖，下次请求会重新抓取。

## 会话日历与完整性校验

market 路径现在对每个非基准标的传入日历,做两项**不同**的检查:

| 检查 | 问题 | 抓什么 |
|---|---|---|
| `check_session_dates` | 每根 bar 是否落在交易日 | 非交易日出现 bar（坏数据） |
| `check_session_completeness` | 每个交易日是否都有 bar | **空洞**——会静默缩短滚动窗口 |

第二项才是 SMA200 / 52 周高点算错数字的根因。空洞不会自己报错,它只是让窗口悄悄变短。

**基准先于成员抓取**是硬依赖,不是巧合:日历由基准定义,基准无法与自己对照。`_ingest_market` 与 M2 pipeline 都把基准排在最前,基准自身以 `calendar=None` 抓取。

**窗口按标的自身生命期收窄**到 `[首根 bar, 末根 bar]`。首根之前、末根之后的交易日不算空洞——公司尚未上市或已退市。否则每只新上市股票都会被拒,而过严的 fail-closed 最终会被人绕过。

**缓存覆盖仍不等于完整性**:覆盖记录的是「请求过什么」。完整性由上表第二项保证,且只在有日历时生效。

### 完整性检查抓不到的两件事

1. **尾部截断。** 完整性只扫 `[首根 bar, 末根 bar]`。若请求的 `end` 是交易日、但数据源只返回到更早的日期(延迟或截断),这个尾部缺口**不算 missing**——它和 IPO 之前的缺口一样被钳掉了。而 coverage 仍会记录完整的 `requested=(start, end)`,下次直接 cache hit。要钉住尾部需要另一条规则(例如末根 bar 必须对齐 `calendar ∩ request` 的最后一个 session)。

   **因此不要把「有日历」读作「请求窗口内每个 session 都有 bar」。**

2. **既有缓存会重验，但不会自动重抓。** `covers` 为真时 provider 读取缓存并
   再跑完整校验；因此带洞缓存会 fail-closed，不会静默服务。普通供应商异常先
   原命令重跑：

   ```bash
   smm ingest --as-of YYYY-MM-DD --source market
   ```

   Provider 会对同一个 symbol/window 请求做有限、确定性的重试：总计最多
   3 次，失败后分别等待 2 秒和 8 秒，不加随机抖动。每次尝试都会写一条
   结构化日志；三次都失败时整次运行仍然 fail closed，不会生成完成
   manifest，也不会把空响应、截断响应或校验失败的数据写进缓存。

   短暂限流、传输失败或偶发空响应：先让内建重试完成；仍失败时，等待
   vendor 恢复后使用完全相同的 `as_of`、配置和命令手工重跑。日级 vendor
   修订或一个真实但超过阈值的大幅波动不会被重试“修好”，三次耗尽是预期
   的保护性失败，需要人工核实数据源。

   默认不要删除缓存。只有旧校验规则已经写入过缓存，或缓存完整性/coverage
   无法证明时，才在保留审计证据后移走对应 symbol 的
   `data/cache/<SYMBOL>.parquet` 并重跑；只有无法界定受影响 symbol 时才重建
   整个缓存。不要用全量清缓存来掩盖单个 symbol 的 provider 故障。

## 失败即停机

任何 §12.4 校验失败都会以非零码退出，不写入缓存。**不要**为了让日更跑完而绕过——缓存里混入未经校验的数据，会把问题洗白进之后每一次运行。

## 极端成交量与官方事件快照

超过冻结 `max_volume_spike_ratio` 的成交量仍先视为异常。只有
`configs/market_events/` 中 `snapshot_date <= as_of` 的最新快照存在唯一的
S&P 500 或 Nasdaq-100 官方 addition/deletion 记录，index 与 source host 精确
匹配，且 spike 恰好发生在 effective date 前一 provider session 或 effective
date 当天，原始 volume 才可继续进入计算。

遇到停机时：

1. 不提高 25 倍阈值、不剔除 symbol、不改写 Parquet；
2. S&P 500 使用 S&P Global / S&P DJI 一手公告；Nasdaq-100 使用 Nasdaq 一手
   公告，核实 ticker、index、action、公告日与生效日；
3. 新增一份日期更晚的通用
   `YYYY-MM-DD_index_constituent_changes.csv` 快照，保留旧快照，不覆盖历史；
4. 走正常 issue/PR review 后，用完全相同的 `as_of` 和 config 重跑；
5. 成功 bundle 必须包含 `market_data_verifications.json`，manifest 同时绑定该
   文件 SHA-256 与实际使用的事件快照 id/digest。

第三方新闻、搜索摘要、其它指数事件、index/source host 错配或运行时网页抓取
不能让异常通过。缺失、重复、冲突、未来公告或窗口不匹配均维持整轮
fail-closed。

## 单一缺失 session 与官方交易所补充

若三次同 provider 重试后仍缺少同一个真实交易日：

1. 先用 benchmark calendar 确认该日是 session；
2. 用官方交易所 OHLCV 确认完整原始 bar；
3. 用 SEC 一手文件确认 registrant、security class、ticker 与生效日连续；
4. 用缺口前后 provider `Close == Adj Close` 证明冻结 adjustment method；
5. 在 `configs/official_bar_supplements/` 新增日期快照并走 review；
6. 用完全相同的 as-of/config/cache/runs 参数重跑。

当前政策只允许同一 symbol/window 的**一个精确缺口**。不允许 runtime 抓官方
API、不允许手改 Parquet、不允许插值/前向填充/休市假设/跳过 symbol。快照与
provider/cache 同 session 任一字段冲突时整轮 fail-closed。

成功 bundle 的 `market_data_verifications.json` 必须包含
`official_bar_supplement` 记录；manifest 必须绑定
`market_data_snapshots.official_bar_supplement` 的 snapshot id/SHA-256。

## 股票池快照

见 [`configs/universe/README.md`](../../configs/universe/README.md)。快照超过 `universe.max_snapshot_age_days` 即 fail-closed，需人工提交新快照。
