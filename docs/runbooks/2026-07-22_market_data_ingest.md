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

- `covers(start, end)` 只在 `[start, end]` 落在已记录窗口内时为真；
- 有 bar **不等于**范围完整——某天没有 bar，可能是休市，也可能是被截断，仅凭 bar 无法区分；
- 多次抓取的窗口会合并为并集；
- 未记录窗口的写入（如直接灌合成数据）不会被当作已覆盖，下次请求会重新抓取。

## 失败即停机

任何 §12.4 校验失败都会以非零码退出，不写入缓存。**不要**为了让日更跑完而绕过——缓存里混入未经校验的数据，会把问题洗白进之后每一次运行。

## 股票池快照

见 [`configs/universe/README.md`](../../configs/universe/README.md)。快照超过 `universe.max_snapshot_age_days` 即 fail-closed，需人工提交新快照。
