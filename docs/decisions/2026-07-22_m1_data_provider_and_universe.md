# ADR：M1 数据源、股票池快照、双价格表示与合成数据生成器

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | proposed |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0（本 ADR 不改变冻结参数，故不 bump） |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（§10 股票池、§11 数据字段、§12 数据使用原则） |
| 关联边界 | [../system-boundary.md](../system-boundary.md) |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)（M1 / PR2） |
| 关联决策 | [2026-07-22_phase0_repo_foundation.md](./2026-07-22_phase0_repo_foundation.md)、[2026-07-21_phase1_scope_and_stack.md](./2026-07-21_phase1_scope_and_stack.md) |
| 变更摘要 | 冻结 M1 四项决策：yfinance 默认源、静态带日期股票池快照、Bar 双价格表示、fixture 改为确定性生成器 |

---

## 背景

Phase 0 已完成（domain / config / FakeProvider / fixtures / CI）。按 Plan v1.1 §6，下一里程碑为 **M1：真实行情、数据校验与合成数据基建**。

Plan v1.1 §10 将「默认免费 provider 具体包名」列为**暂不阻塞、PR2 选定**的开放项。进入 M1 实现前需要冻结它，同时在核查中发现两处**会反噬架构**的问题，一并在本 ADR 决策：

1. 当前 `Bar` 只携带一套 OHLCV，与宪法 §12.1「收益/均线/动量用复权价，模拟成交与止损用当时可交易的未复权价」冲突。
2. 现有 fixture 长度为 36–58 根 bar，无法支撑 M2 起需要的 SMA200 / Return_126 / 52 周高点（≥252 根）。

前者若推迟到 MVP-B（成交与止损真正消费未复权价时）再改，将同时返工 domain、provider 协议与全部 fixture；后者若继续手写 CSV，250+ 行不可维护。故二者必须在 M1 写业务代码之前定型。

---

## 决策

### 1. 默认数据源：yfinance

- M1 默认 provider 实现为 **yfinance**，作为 `DataProvider` 协议（[../../src/smm/data/protocol.py](../../src/smm/data/protocol.py)）的一个实现，**不得**被业务代码绕过直接调用。
- `yfinance` 进入 `pyproject.toml` 的 `dependencies`。这**解除**了 Phase 0 DoD 中「依赖树无 yfinance」的约束——该约束的意图是防止在骨架完成前接真实数据，M1 正是解除它的合法时点。
- provider 的已知限制必须写入模块 docstring 与 [runbooks](../runbooks/)，至少包含：
  - 非官方 API，无 SLA，接口可能无预告变更；
  - 偶发限流与静默空返回；
  - 基本面与财报数据 **非 point-in-time**，因此 **不得**用于宪法 §12.2 约束下的任何历史结论。

**理由：** 免费、零配置、提供复权价与 corporate actions，足以支撑 V1 的日线多头验证。选型风险被两层结构吸收：provider 在 `DataProvider` 协议背后可整体替换；数据质量风险由 §3 的 validation 层 fail-closed 拦截，而不是静默通过。属低后悔决策。

### 2. 股票池：静态带日期快照文件

- 成分股以**签入仓库的带日期快照文件**提供：

  ```text
  configs/universe/YYYY-MM-DD_sp500_ndx.csv
  ```

- 列至少包含：`symbol`、`name`、`index_membership`（`sp500` / `ndx100` / `both`）、`snapshot_date`。
- `get_universe(as_of)` 选取 `snapshot_date <= as_of` 中最新的一份快照；**无可用快照时 fail-closed**，不回退到"最近一份"或空列表。
- 每份快照文件头部与 `configs/universe/README.md` 必须携带宪法 §12.3 要求的声明：

  > 本快照为**当前**成分股，仅供工程测试与日更流程使用；**不得**用于产出正式历史回测结论。正式结论需要历史成分股数据。

**理由：** 完全可复现、可 diff、可审计，且天然绑定 `as_of_date`（system-boundary §2 的硬要求）。运行时抓取会让昨天与今天的同一次回放产生不同结果，直接违反幂等性（Plan v1.1 §2 约束 4）。

### 3. `Bar` 双价格表示：OHLCV 为未复权可交易价 + 复权字段

`Bar`（[../../src/smm/domain/models.py](../../src/smm/domain/models.py)）扩展为：

| 字段 | 语义 | 消费方 |
|------|------|--------|
| `open` / `high` / `low` / `close` | **未复权**、当时实际可交易价 | 模拟成交、止损、跳空判定（MVP-B） |
| `volume` | 当时成交量 | 相对成交量、美元成交额 |
| `adj_close` | 复权收盘价 | 收益率、均线、动量、RS、横截面排名（M2+） |
| `adj_factor` | `adj_close / close` | 校验与审计；复权因子异常检测 |

规则：

- **特征层只读 `adj_close`**；**成交与止损层只读未复权 OHLC**。二者不得互串。
- 缺 `adj_close` 时按 §4 fail-closed 处理，**不得**用 `close` 顶替（那是"填有利默认"的一种）。
- fixture CSV 列格式同步扩展；`FakeProvider` 与现有 4 个单测随之更新。

**理由：** 宪法 §12.1 明确要求两套价格并存。选择「OHLCV 保持未复权、复权作为附加字段」而非反过来，是因为未复权价是**当时真实发生的事实**，复权价是随后续公司行动而变化的派生量——把事实放在主字段，派生量放在附加字段，可以让"误用"在代码里更显眼：任何拿 `bar.close` 去算动量的地方都是可被 review 抓到的错误，反之则不然。

### 4. Fixture 改为确定性生成器

- 新增合成行情生成器，输出 **≥252 根 bar** 的路径，覆盖至少：突破成功、假突破、Risk-Off SPY。
- 生成器必须是**确定性的**（固定种子/参数 ⇒ 逐 bar 相同输出），使 fixture 可重新生成且 diff 可解释。
- CSV 成为**生成产物**；生成脚本是真相源。
- 生成的路径必须能支撑**无前视测试**：第 T 日的突破判定只允许消费 `date <= T` 的 bar。

**理由：** 硬过滤需要 SMA200 / Return_126 / 52 周高点，最少 252 根 bar；手写 CSV 在该长度下不可维护，且无法保证形态确实满足待测条件。Plan v1.1 §8 已将「黄金 synthetic」与「禁止用未来 bar 算信号日指标」列为最低测试集，生成器是它们的前置条件。

---

## 对 M1 实现的约束

1. 所有数据访问经 `DataProvider`；业务代码**不得**出现 `yfinance` import。
2. 校验层覆盖宪法 §12.4 全清单：缺失日期、重复记录、零或负价、单日异常跳变、成交量异常、复权因子异常、时区错误。任一失败 → 抛 `DataValidationError`（`FailClosedError` 子类）→ **停止产出可执行动作**，不降级继续。
3. Parquet 缓存幂等：同一 `as_of` 重跑，缓存内容与下游结果一致。
4. CLI 新增 `smm ingest --as-of YYYY-MM-DD`。
5. 策略阈值仍然只能来自 config；校验阈值（如"单日跳变多少算异常"）本身也是参数，须进 YAML 而非硬编码。

---

## 后果

### 正面

- M2 特征引擎可以直接假设「adj 价可用且已校验」，不必各自处理复权。
- 股票池与行情都绑定 `as_of`，回放可复现。
- 数据质量问题在入口停机，不会污染下游信号统计。

### 代价

- `Bar` 扩字段会破坏 Phase 0 已冻结的 fixture 列格式与 4 个既有单测，需在同一 PR 内一并更新。
- 静态快照需要人工定期刷新；快照过期时系统 fail-closed 而非静默沿用旧池——这是有意的取舍。
- yfinance 无 SLA，日更流程可能因上游波动而停机。这是 fail-closed 的预期表现，不是缺陷。

### 必须遵守

- 未复权价与复权价的消费边界不得互串（code review 检查项）。
- 快照文件的 survivorship disclaimer 不得删除。
- 任何"数据缺失时用某个默认值继续"的补丁都视为违反宪法原则 11 与 fail-closed 约定。

---

## 备选（未采纳）

| 方案 | 原因 |
|------|------|
| Tiingo / Polygon 免费档 | 数据质量更稳，但需 API key 与密钥管理；V1 阶段收益不足以抵消接入成本。provider 可替换，未来质量成为瓶颈时再切 |
| 仅本地 CSV 导入，推迟选型 | 把选型推到更晚只是延后同一个决定，且需要人工准备数据，反而拖慢 M1 |
| 运行时抓取维基百科成分股 | 页面结构易变、结果不可复现、无法绑定 `as_of`，违反幂等与边界要求 |
| 手工 20–50 只小池子 | 最快见到端到端结果，但横截面排名（动量 / RS 的核心）在小池子上无意义，会给出误导性的信号形态 |
| `Bar` 只存复权价 | 直接违反宪法 §12.1；且止损价会随未来的分红拆股而变化，历史成交无法复现 |
| 继续手写长 fixture CSV | 252+ 行不可维护，且无法保证形态满足待测条件 |
| 推迟双价格改造到 MVP-B | 届时需同时返工 domain、provider 协议与全部 fixture，成本远高于现在 |

---

## 开放项（不阻塞 M1）

| 项 | 归属 | 说明 |
|----|------|------|
| 板块 RS 缺失时的处理 | M2 | config 有 `rs_sector_63_weight: 0.20` 但暂无板块数据源。原则已由宪法定（缺失 = missing，不填有利默认），待定的只是：缺板块时**重新归一化 RS 子权重**，还是**整体标记 missing 向下传递** |
| 快照刷新频率与流程 | M1 后 | 建议进 runbook |
| 财报日历数据源 | M3+ | 事件过滤所需；yfinance 的财报日期可靠性待评估 |

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-22 | proposed | M1 实现前提交评审 |
