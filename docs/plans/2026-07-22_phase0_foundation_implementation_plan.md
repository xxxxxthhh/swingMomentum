# Phase 0 — Foundation Implementation Plan

| 字段 | 值 |
|------|-----|
| 文档类型 | plan |
| 状态 | approved |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0 |
| 关联规格 | [../specs/Personal_Trading_Constitution_and_Swing_Momentum_Spec_v2.md](../specs/Personal_Trading_Constitution_and_Swing_Momentum_Spec_v2.md) |
| 关联边界 | [../system-boundary.md](../system-boundary.md) |
| 关联决策 | [../decisions/2026-07-22_phase0_repo_foundation.md](../decisions/2026-07-22_phase0_repo_foundation.md) |
| 关联评审 | [../reviews/2026-07-22_repo_skeleton_review.md](../reviews/2026-07-22_repo_skeleton_review.md) |
| 后续计划 | [2026-07-22_phase1_implementation_plan_v1_1.md](./2026-07-22_phase1_implementation_plan_v1_1.md) |
| 变更摘要 | 编码第一阶段：domain + config + fake data + fixtures + CI；不碰 Scanner/yfinance |

---

## 1. 目标

把仓库从「文档骨架」变成 **可安装、可测试、可配置、领域对象清晰** 的工程底座，使后续 Phase 1（MVP-A/B）挂在稳定接口上，而不是脚本堆砌。

**一句话：**

> 先有交易操作系统的骨架，再有 Scanner。

**明确成功画面（Phase 0 Done）：**

```text
configs/smm_v1_0_0.yaml  可加载、可校验、可 hash
src/smm/domain/          最小领域模型 + 基本不变量测试
src/smm/data/            DataProvider 协议 + FakeProvider
tests/fixtures/          至少 3 类合成行情
pytest 本地绿 + CI 绿
experiments/ + notebooks/ 约定 README 就位
无 yfinance、无完整指标引擎、无业务 Scanner
```

---

## 2. 非目标（严格禁止纳入 Phase 0 PR）

| 禁止 | 原因 |
|------|------|
| yfinance / 网络拉真实行情 | 先 Fake + fixtures |
| 完整硬过滤 / 突破扫描业务 | 属 Phase 1 MVP-A |
| SMA/ATR 等指标大全实现 | 属 Phase 1 M2 |
| Risk sizing / Paper fill | 属 Phase 1 MVP-B |
| 回测收益报告 | 研究轨后续 |
| 过度抽象（通用插件框架、DI 容器等） | 个人规模不需要 |

---

## 3. 目标目录树（完成后）

```text
swingMomentum/
  README.md
  pyproject.toml
  .github/workflows/ci.yml
  configs/
    smm_v1_0_0.yaml
  src/smm/
    __init__.py
    py.typed
    domain/
      __init__.py
      enums.py          # SignalState, Regime, Side, ...
      models.py         # Signal, OrderPlan, Position, Trade, RiskDecision, Bar, ...
      identity.py       # setup_key / logical signal id 规则（纯函数）
    config/
      __init__.py
      schema.py         # pydantic 模型
      loader.py         # load_yaml + validate + config_hash
    core/
      __init__.py
      errors.py         # fail-closed 相关错误类型
      types.py          # AsOfDate 等别名
    data/
      __init__.py
      protocol.py       # DataProvider Protocol
      fake.py           # FakeProvider 读 fixtures
    cli/
      __init__.py
      main.py           # 最小 CLI：show-config / version（可选）
  tests/
    unit/
      test_config_loader.py
      test_domain_models.py
      test_signal_identity.py
      test_fake_provider.py
    fixtures/
      ohlcv/
        breakout_success.csv
        false_breakout.csv
        risk_off_spy.csv
      README.md
  experiments/
    README.md
    _template/
      hypothesis.md
      config.yaml
      result.md
  notebooks/
    README.md
  docs/                 # 已有
```

---

## 4. Domain 最小对象集（V1）

只建 **当前规格需要** 的对象；字段可随 Phase 1 增量扩展，但 Phase 0 必须定名与核心不变量。

| 对象 | 职责 | Phase 0 最低要求 |
|------|------|------------------|
| `Bar` | 单日 OHLCV | symbol, date, o/h/l/c/v |
| `SignalState` | 生命周期枚举 | 与 Plan v1.1 状态名对齐 |
| `Signal` | 信号实体 | id, symbol, as_of, state, setup_key, scores(optional), reason_codes |
| `OrderPlan` | 计划单（未成交） | signal_id, symbol, side, qty?, entry_ref, stop_ref |
| `Position` | 持仓 | symbol, qty, entry, stop, state |
| `Trade` | 已闭合交易 | 开平字段占位即可 |
| `RiskDecision` | 风险引擎输出 | accept/reject, reasons, size? |
| `StrategyIdentity` | 版本身份 | version, config_hash |

**不变量示例（单测）：**

- `SignalState` 非法转移可检测（Phase 0 可只测「允许集合」表，完整状态机引擎可 Phase 1）  
- `setup_key` 对相同锚点输入稳定  
- `RiskDecision` reject 时不得带可执行正仓位（若字段存在）  

**原则：** 交易系统核心不是 DataFrame；DataFrame 是计算工具。

---

## 5. Config 系统

### 5.1 文件

`configs/smm_v1_0_0.yaml` — 对齐规格第二十部分 + Plan v1.1 修订（Fund filter、极简 breakout、state timeout 等）。

建议顶层键：

```yaml
strategy:
  name: Swing Momentum Scanner
  version: SMM-V1.0.0
universe: { ... }
market_regime: { ... }
hard_filters: { ... }
momentum: { ... }
relative_strength: { ... }
scoring: { ... }          # V1 权重；fund_as_filter: true
signal:
  breakout_window: 20
  relative_volume_min: 1.30
  watchlist_expire_bars: 20   # 示例，可调
risk: { ... }
stop: { ... }
exit: { ... }
execution:
  next_day_open: true
  max_open_gap_atr: 1.0
```

### 5.2 行为

- pydantic（或同类）校验：类型、范围（如 risk ∈ (0,1)）  
- `config_hash`：规范化序列化后哈希（算法写入代码注释与 README）  
- CLI 或 `python -m`：打印 version + hash（便于审计演示）  

### 5.3 禁止

```python
# 业务代码中禁止
if volume_ratio > 1.3:
```

应：

```python
if volume_ratio >= cfg.signal.relative_volume_min:
```

---

## 6. DataProvider 与 Fixtures

### 6.1 Protocol（Phase 0）

```text
get_universe(as_of) -> list[str]
get_daily_bars(symbol, start, end) -> Sequence[Bar]
get_calendar(start, end) -> list[date]   # 可简化
```

### 6.2 FakeProvider

- 从 `tests/fixtures/ohlcv/*.csv` 加载  
- 无网络  
- 用于证明：配置 + domain + 数据边界可连成最小管道（**可选** Phase 0 末尾一条「读取 breakout_success 并构造 detected Signal 草稿」的冒烟测试——**不是**完整 Scanner）

### 6.3 最低 fixture 集

| 文件 | 意图 |
|------|------|
| `breakout_success.csv` | 上涨→整理→放量突破形态的价格路径 |
| `false_breakout.csv` | 突破后迅速失败 |
| `risk_off_spy.csv` | SPY 处于 Risk-Off 的简化路径 |

每文件旁或 `fixtures/README.md` 写清：列格式、时区/日期约定、期望用途。

---

## 7. 测试与 CI

### 7.1 本地

- `pytest`  
- 可选：`ruff` check（若加入 pyproject）  

### 7.2 CI

- GitHub Actions：on push/PR → install → pytest  
- Python 3.11+  

### 7.3 Phase 0 测试清单

| 测试 | 断言 |
|------|------|
| config 合法样例 | load 成功，hash 稳定 |
| config 非法样例 | 校验失败 |
| domain 构造 | 必填字段、枚举 |
| setup_key 稳定 | 相同输入相同输出 |
| FakeProvider | 读 fixture 条数与日期序 |
| （可选）冒烟 | fixture → 手工规则生成一个 `Signal` 对象字段完整 |

---

## 8. experiments / notebooks

### 8.1 `experiments/README.md`

- 何时建实验  
- 目录模板：`hypothesis.md` / `config.yaml` / `result.md`  
- 禁止：未关闭实验的参数直接进执行 config  
- 晋级路径：result → review → decision → bump config version  

### 8.2 `notebooks/README.md`

- 只读分析  
- 禁止写 SQLite 执行库 / 覆盖生产契约路径  
- 与 experiments 的分工：notebook 探索，experiments 结构化结论  

---

## 9. 工作包切分（建议 PR）

| ID | 内容 | 验收 |
|----|------|------|
| **P0-PR1** | `pyproject.toml`、包骨架、`core` 错误类型、README 开发小节 | 可 `pip install -e ".[dev]"` |
| **P0-PR2** | `config` schema + loader + `smm_v1_0_0.yaml` + 单测 | 非法 config 失败；hash 稳定 |
| **P0-PR3** | `domain` 模型 + enums + identity + 单测 | 对象可构造；不变量绿 |
| **P0-PR4** | `DataProvider` protocol + FakeProvider + fixtures + 单测 | 无网络读 CSV |
| **P0-PR5** | CI workflow + experiments/notebooks 约定 + system-boundary 链到 README | CI 绿；文档交叉链接完整 |

可合并为 1–2 个 PR，但逻辑顺序不变。

---

## 10. 与 Phase 1 的衔接

```text
Phase 0 Done
    → Phase 1 M1：真实 DataProvider（yfinance 等）+ validation
    → M2：features + regime
    → M3–M4：Watchlist + Scanner + State machine（MVP-A）
    → M5–M7：Risk + Paper（MVP-B）
```

Phase 0 产出的接口 **不得** 在 M1 被绕过（例如业务直接读 CSV 全局变量）。

Plan v1.1 的 **M0** 由本 Phase 0 完整覆盖并加严（domain / CI / experiments）。

---

## 11. 验收检查清单（Definition of Done）

- [ ] `pip install -e ".[dev]"` 成功  
- [ ] `pytest` 全绿  
- [ ] CI 在默认分支/PR 全绿  
- [ ] `configs/smm_v1_0_0.yaml` 存在且通过 schema  
- [ ] `config_hash` 对同一文件两次一致  
- [ ] domain 最小对象可导入  
- [ ] FakeProvider + ≥3 fixtures  
- [ ] `experiments/README.md`、`notebooks/README.md`、`tests/fixtures/README.md`  
- [ ] 根 README 指向 Phase 0/1 计划与 system-boundary  
- [ ] **依赖树无 yfinance**（或仅 optional extra 且无默认安装、无默认代码路径）  
- [ ] **无** `scanner.py` 完整业务实现  

---

## 12. 建议执行顺序（确认本计划后）

1. P0-PR1 包骨架  
2. P0-PR2 config  
3. P0-PR3 domain  
4. P0-PR4 fake data + fixtures  
5. P0-PR5 CI + 研究目录约定  
6. 合并后打 tag 可选：`phase0-foundation`  
7. **再** 开 Phase 1 M1  

---

## 13. 总结

Phase 0 把「宪法 → 代码」之间的缺失层补上：**Domain、Config、Fake Data、Fixtures、CI、实验治理**。  
完成后仓库才具备长期挂载 Portfolio Risk、期权子系统与研究助手的底座；在此之前写 Scanner 是过早优化业务、欠下工程债。
