# Official bar supplements

本目录只保存经 review 的、离线的官方交易所单日行情补充快照。

- 文件名：`YYYY-MM-DD_official_bar_supplements.csv`
- 运行只选择 `snapshot_date <= as_of` 的最新快照；
- runtime 不访问 `bar_source_url` 或 `identity_source_url`；
- 一行只能解释一个精确 `(symbol, session)` provider 空洞；
- 当前政策最多允许同一 symbol/window 补一个 session；
- 既有 provider/cache bar 与快照冲突时整轮 fail-closed；
- 历史快照不可覆盖，修订必须新增更晚快照。

政策与字段约束见
[`docs/decisions/2026-07-24_official_exchange_missing_bar_supplement.md`](../../docs/decisions/2026-07-24_official_exchange_missing_bar_supplement.md)。
