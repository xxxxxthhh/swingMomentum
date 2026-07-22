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

## ⚠️ 会话日历尚未接入校验

`check_session_dates` 的日历语义已经正确（空日历 fail-closed），但**生产路径目前不传 `calendar`**——`validate_bars` 始终在无日历下调用。

因此：**缓存覆盖记录证明「请求过哪些窗口」，不证明「窗口内每个 session 都有 bar」。** IPO、退市、上游截断仍可能在已覆盖窗口内留下空洞，而 SMA200 / 52 周高点碰到空洞会直接算出错数字而不是报错。

接线追踪于 [issue #10](https://github.com/xxxxxthhh/swingMomentum/issues/10)。在它完成前，不要把 #8 读作「生产路径已闭环」。

## 失败即停机

任何 §12.4 校验失败都会以非零码退出，不写入缓存。**不要**为了让日更跑完而绕过——缓存里混入未经校验的数据，会把问题洗白进之后每一次运行。

## 股票池快照

见 [`configs/universe/README.md`](../../configs/universe/README.md)。快照超过 `universe.max_snapshot_age_days` 即 fail-closed，需人工提交新快照。
