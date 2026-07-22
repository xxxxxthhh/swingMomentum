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

## 失败即停机

任何 §12.4 校验失败都会以非零码退出，不写入缓存。**不要**为了让日更跑完而绕过——缓存里混入未经校验的数据，会把问题洗白进之后每一次运行。

## 股票池快照

见 [`configs/universe/README.md`](../../configs/universe/README.md)。快照超过 `universe.max_snapshot_age_days` 即 fail-closed，需人工提交新快照。
