# ADR：M1 数据源、股票池快照、价格表示与合成数据生成器

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | **accepted**（rev.5；[PR #1](https://github.com/xxxxxthhh/swingMomentum/pull/1) 批准，[PR #2](https://github.com/xxxxxthhh/swingMomentum/pull/2) 实测修订，Issue #4 选择 true-print 方案 A） |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0（**不 bump**；§2.4 新增 config 键仅改变 `config_hash`，划界见 §2.4） |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md)（§10 股票池、§11 数据字段、§12 数据使用原则） |
| 关联边界 | [../system-boundary.md](../system-boundary.md) |
| 关联计划 | [../plans/2026-07-22_phase1_implementation_plan_v1_1.md](../plans/2026-07-22_phase1_implementation_plan_v1_1.md)（M1 / PR2） |
| 关联决策 | [2026-07-22_phase0_repo_foundation.md](./2026-07-22_phase0_repo_foundation.md)、[2026-07-21_phase1_scope_and_stack.md](./2026-07-21_phase1_scope_and_stack.md) |
| 变更摘要 | 冻结 M1 四项决策：yfinance 默认源（optional extra）、静态带日期股票池快照、provider-native `Bar` + true-print `PrintBar` 边界、fixture 改为确定性生成器 |

---

## 背景

Phase 0 已完成（domain / config / FakeProvider / fixtures / CI）。按 Plan v1.1 §6，下一里程碑为 **M1：真实行情、数据校验与合成数据基建**。

Plan v1.1 §10 将「默认免费 provider 具体包名」列为**暂不阻塞、PR2 选定**的开放项。进入 M1 实现前需要冻结它，同时在核查中发现两处**会反噬架构**的问题，一并在本 ADR 决策：

1. 当前 `Bar` 只携带一套 OHLCV，与宪法 §12.1「收益/均线/动量用复权价，模拟成交与止损用当时可交易的未复权价」冲突。
2. 现有 fixture 长度为 36–58 根 bar，无法支撑 M2 起需要的 SMA200 / Return_126 / 52 周高点（≥252 根）。

前者若推迟到 MVP-B（成交与止损真正消费未复权价时）再改，将同时返工 domain、provider 协议与全部 fixture；后者若继续手写 CSV，250+ 行不可维护。故二者必须在 M1 写业务代码之前定型。

---

## 决策

### 1. 默认数据源：yfinance（作为 optional extra）

- M1 默认 provider 实现为 **yfinance**，作为 `DataProvider` 协议（[../../src/smm/data/protocol.py](../../src/smm/data/protocol.py)）的一个实现，**不得**被业务代码绕过直接调用。
- **依赖落点：** `yfinance` 放入 **optional extra `smm[market]`**，不进核心 `dependencies`。

  ```toml
  [project.optional-dependencies]
  market = ["yfinance>=0.2"]
  ```

  **CI 不得依赖真实网络拉行情。** 默认 CI job 只安装 `.[dev]`，只测 FakeProvider / 生成器 / 缓存 fixture。涉及真实网络的测试须标记 `@pytest.mark.network`，默认 deselect。

  这同时使 Phase 0 DoD「依赖树无 yfinance」在**默认安装路径上继续成立**——该约束的意图是防止在骨架完成前把真实数据接进主路径，本决策以 extra 的形式保留了它的实质。

#### 1.1 provider 已知限制（必须写入 docstring + runbook）

| 限制 | 影响 |
|------|------|
| 非官方 API，无 SLA，接口可能无预告变更 | 日更流程可能停机（fail-closed 的预期表现） |
| 偶发限流与静默空返回 | 必须由校验层识别为异常而非"当日无数据" |
| **复权序列非严格 point-in-time** | 见下 |
| 基本面与财报数据非 point-in-time | **不得**用于宪法 §12.2 约束下的任何历史结论 |

**关于复权序列（宪法 §12.2）：** yfinance 提供的是**下载日视角下的 backward-adjusted** 序列，不是完整的公司行动历史。同一个历史日期在不同下载日可能得到不同的 `adj_close`。因此：

- 对 V1 的工程验证与信号形态观察，**可接受**；
- **不得**把 yfinance 的 adj 序列当作审计级真相，尤其是跨拆股/分红的长窗口回测；
- 正式策略结论需要独立的公司行动数据源，另立 ADR。

---

### 2. 股票池：静态带日期快照文件

- 成分股以**签入仓库的带日期快照文件**提供：

  ```text
  configs/universe/YYYY-MM-DD_sp500_ndx.csv
  ```

- 列至少包含：`symbol`、`name`、`index_membership`（`sp500` / `ndx100` / `both`）、`snapshot_date`。

#### 2.1 快照选取语义（无歧义表述）

| | 规则 |
|---|---|
| **允许** | 选取 `snapshot_date <= as_of` 中 **`snapshot_date` 最大**的那一份 |
| **禁止** | 使用 `snapshot_date > as_of` 的未来快照（前视） |
| **禁止** | 无任何满足 `snapshot_date <= as_of` 的快照时，返回空池、返回全部 symbol、或改用 `snapshot_date > as_of` 的快照 —— 必须 `fail-closed` |
| **禁止** | 选中的快照超过 `max_snapshot_age_days` 时静默沿用 —— 必须 `fail-closed` |

> 评审意见：原文「不回退到最近一份」与「取 ≤ as_of 的最新一份」读起来互相打架。已按上表重写。原意是禁止*无快照时*编造回退，不是禁止取最新的历史快照。

#### 2.2 快照过期

新增 config 键：

```yaml
universe:
  max_snapshot_age_days: 90   # 首次冻结值，可调
```

`as_of - snapshot_date > max_snapshot_age_days` → `DataValidationError`（fail-closed），要求人工提交新快照。

**取舍说明：** 选择「过期即停机」而非「旧快照一直服务」，理由是成分股会持续变动，静默沿用两年前的池子会让横截面排名建立在错误的样本上，而这种错误不会自己暴露。代价是忘记刷新会导致日更停机——这与宪法 fail-closed 哲学一致，是有意的。

**经 PR #1 二次评审确认：维持 fail-closed，不改为「告警继续跑」**（后者会破坏 fail-closed 一致性）。若实践中过吵，应调大 `max_snapshot_age_days` 或补 runbook 提醒，而非降级为告警。首值 90 在 runbook 中校准即可，不再单独讨论。

#### 2.4 版本纪律划界（经 PR #1 确认）

新增 `max_snapshot_age_days` **改变 `config_hash`，但不 bump 策略版本**。一般划界：

| 变更类型 | bump `SMM-Vx.y.z` | 改变 `config_hash` |
|----------|-------------------|--------------------|
| 过滤 / 评分 / 仓位 / 进出场规则 | **是** | 是 |
| 数据治理护栏（校验阈值、快照年龄、provider 运维参数） | **否**（V1） | **是**（便于审计「当时用了哪套护栏」） |
| domain 字段形状（如 `Bar` 扩列）且不改规则语义 | **否** | 视是否写入 config；以 fixture / 测试迁移表达即可 |

**「凡进 YAML 皆 bump 策略版本」未采纳**——那会把运维旋钮与策略身份绑死，迫使无 alpha 变更也升版。若日后有人以不同 `max_snapshot_age_days` 跑出两套 Shadow 统计，靠 **`config_hash` + 实验隔离** 区分，不靠策略大版本号。

#### 2.3 幸存者偏差声明（宪法 §12.3）

每份快照文件头部与 `configs/universe/README.md` 必须携带：

> 本快照为**当前**成分股，仅供工程测试与日更流程使用；**不得**用于产出正式历史回测结论。正式结论需要历史成分股数据。

**理由（本决策整体）：** 完全可复现、可 diff、可审计，且天然绑定 `as_of_date`（system-boundary §2 的硬要求）。运行时抓取会让昨天与今天的同一次回放产生不同结果，直接违反幂等性（Plan v1.1 §2 约束 4）。

---

### 3. 价格表示：provider-native `Bar` 与 true-print `PrintBar` 分离

> 采纳评审建议 **方案 A**。原文只规定「特征层读 `adj_close`」，无法支撑 ATR / 延伸过滤 / range 类特征所需的复权 high/low/open。

#### 3.1 持久化字段

| 字段 | 语义 | 是否持久化 |
|------|------|-----------|
| `Bar.open` / `high` / `low` / `close` | provider primary；Yahoo 为 split-adjusted / dividend-unadjusted | 是 |
| `volume` | 成交量（拆股口径见 §3.4、§5.2） | 是 |
| `adj_close` | provider 提供的复权收盘价 | 是 |
| `adj_factor` | `adj_close / close`（当日标量） | 是 |
| `PrintBar.open` / `high` / `low` / `close` / `volume` | **当时实际成交的 true print**；由 MVP-B 公司行动适配器独立生成 | 独立于 provider-native Bar |

> **rev.5：** 不再把 Yahoo 主序列称作 tradeable / 历史 print。它只能进入特征侧；成交侧必须从独立的 `PrintBar` 起步。具体裁决见 **[§5.3](#53-mvp-b-前置裁决已决方案-a)**。

**不**持久化 `adj_open` / `adj_high` / `adj_low`：它们是无信息增量的派生量，多存三列只会增加"两处不一致"的失败面。

#### 3.2 派生规则（特征层唯一入口）

```text
adj_open  = open  * adj_factor
adj_high  = high  * adj_factor
adj_low   = low   * adj_factor
adj_close = close * adj_factor      # 恒等式，用作校验断言
```

同一日的四个价格共用**同一个** `adj_factor`。校验层必须断言 `close * adj_factor == adj_close`（浮点容差内），否则 `DataValidationError`。

#### 3.3 消费边界（可测不变量）

| 层 | 只能读 | 禁止读 |
|----|--------|--------|
| 特征引擎（收益、均线、ATR、动量、RS、排名、延伸过滤） | `adj_*` 派生值 | `open`/`high`/`low`/`close` 原始字段 |
| 成交、止损、跳空判定（MVP-B） | 独立 `PrintBar` 的 `open`/`high`/`low`/`close` | provider-native `Bar`、`adj_close` / `adj_factor` |

按评审建议，M1 必须把这条做成**可测**而非仅靠 review：

- 特征层通过一个 `AdjustedBarView`（或等价包装）取数，该视图**不暴露**原始 OHLC 字段；
- 成交/止损层只通过 `PrintBar` 取数；`to_tradeable(Bar)` 必须 fail loud；
- 单测断言：特征入口拿不到 raw close，成交入口拿不到 adj_close。

**类型分离的理由：** true print 是当时真实发生的事实，provider primary 与复权价都可能随 provider 口径或下载日变化。把事实建成独立类型，才能让错误在调用边界直接失败，而不是依赖字段名和 code review 猜测语义。

#### 3.4 ⚠️ 新发现：成交量的拆股口径（评审未提，M1 必须处理）

`signal.relative_volume_min: 1.30` 依赖 `今日成交量 / 20日均量`。若窗口内发生 **4:1 拆股**，拆股前的原始成交量约为拆股后的 1/4，会把 20 日均量压低，从而**凭空制造一次放量突破信号**。这是一个会产生假信号的静默错误。

复杂之处在于 Yahoo 对成交量的口径未见于文档承诺：它可能已经返回拆股调整后的成交量（此时 `close` 也可能已是拆股调整价，`adj_factor` 便只反映分红）。**不能靠假设。**

M1 必须：

1. **实测确认** yfinance 在已知拆股样本（如 NVDA 2024-06 10:1、AAPL 2020-08 4:1）上，`close` 与 `volume` 各自是否已拆股调整，把结论写入 provider 限制清单；
2. 校验层增加**拆股边界检查**：`volume` 或 `close` 在单日出现接近整数比（2、3、4、10…）的跳变而未伴随对应 `adj_factor` 变化时 → `DataValidationError`；
3. 相对成交量的计算口径（用哪一个 volume 序列）在实测结论出来后写入 M2 之前。

在结论确认前，**不得**假定 `relative_volume` 计算是安全的。

---

### 4. Fixture 改为确定性生成器

- 新增合成行情生成器，输出 **≥252 根 bar** 的路径，覆盖至少：突破成功、假突破、Risk-Off SPY。
- 生成器必须是**确定性的**（固定种子/参数 ⇒ 逐 bar 相同输出）。

#### 4.1 产物是否入库（回应评审）

采纳评审倾向：

| | 决定 |
|---|---|
| 真相源 | **生成器代码 + 固定种子参数** |
| 测试取数 | **运行时调用生成器**，不读磁盘 CSV |
| git 跟踪 | **不提交 252 行 CSV**；现有 3 个手写 CSV 在 M1 中删除 |
| 回归保护 | 可选：对生成序列取 golden hash 存为短小 fixture，生成器行为变更时必须显式更新该 hash |

`FakeProvider` 保留从 CSV 目录读取的能力（它是 `DataProvider` 的合法实现，且便于临时排查），但**测试默认走生成器**而非磁盘。

**理由：** 硬过滤需要 SMA200 / Return_126 / 52 周高点，最少 252 根 bar；手写 CSV 在该长度下不可维护，且无法保证形态确实满足待测条件。Plan v1.1 §8 已将「黄金 synthetic」与「禁止用未来 bar 算信号日指标」列为最低测试集，生成器是它们的前置条件。

生成的路径必须能支撑**无前视测试**：第 T 日的突破判定只允许消费 `date <= T` 的 bar。

---

### 5. 附录（rev.4）：Yahoo 主序列的真实口径

> 本节由 [PR #2](https://github.com/xxxxxthhh/swingMomentum/pull/2) 的实测结果与二次评审裁决加入。**修正 §3.1 的一处措辞错误**，其余决策不变。

§3.4 要求的实测已完成（NVDA 10:1 2024-06-10、AAPL 4:1 2020-08-31、TSLA 3:1 2022-08-25，`auto_adjust=False`）：

| 序列 | 实测口径 |
|------|----------|
| `Close` / OHLC | **已拆股调整**（NVDA 拆股前收盘返回 120.888，非当时成交的 1208.88） |
| `Volume` | **已拆股调整**，边界无跳变 |
| `Adj Close` | 相对 `Close` 仅差**分红**（TSLA 不分红，跨拆股两者末位小数相同） |

#### 5.1 措辞修正

§3.1 把主字段称为「**未复权**、当时实际可交易价」。对本 provider **字面不成立**：它是 **split-adjusted, dividend-unadjusted**，不是审计级的「当日交易所成交 print」。

该序列只承担 provider primary 角色，**不得**继续承担 fill/stop。`TradeableBar` 只能由 `PrintBar` 投影，Yahoo provider 只产出 `Bar`，二者在类型边界隔离。

**不变的部分：** 缺 `adj_close` 仍**禁止**用 `close` 顶替；§3.3 的消费边界不受影响（特征层只用 total-return 语义，不依赖真 print 价）。

#### 5.2 §3.4 的假信号风险在本 provider 上不成立

因成交量已预调整，20 日均量口径一致，不会凭空产生放量突破。**结论：** M2 可用同一 `volume` 序列计算 `relative_volume`；`check_split_artefacts` **保留为 provider 变更护栏**，而非修复现存缺陷。

#### 5.3 MVP-B 前置裁决（已决：方案 A）

M1/M2 不受影响（特征层不依赖 print 价）。Issue #4 采纳评审推荐的 **方案 A**：

| 方案 | 含义 |
|------|------|
| **A（采纳）** | 用 `actions=True` / `Stock Splits` **重建 true print OHLCV** 专供 fill/stop；主缓存保留 provider 原生序列给特征。Paper 成交显式绑定 `PrintBar` |
| **B（不采纳）** | 修订宪法/ADR 措辞，让 V1 Paper 使用 split-adjusted share units |

当前阶段先落下不可绕过的类型契约：provider-native `Bar` 不能投影为 `TradeableBar`。MVP-B 编码前再实现公司行动适配器，并用已知拆股样本验证 `PrintBar` 重建。适配器缺失或拆股历史不完整时必须 fail-closed，不得退回 Yahoo primary OHLC。

**明确不采纳：** 继续把 Yahoo 的 `Close` 称作「未复权真实成交价」；或在 true-print 重建失败时静默退回 provider primary。

---

## 对 M1 实现的约束

1. 所有数据访问经 `DataProvider`；业务代码**不得**出现 `yfinance` import。
2. 校验层覆盖宪法 §12.4 全清单：缺失日期、重复记录、零或负价、单日异常跳变、成交量异常、复权因子异常、时区错误。任一失败 → 抛 `DataValidationError`（`FailClosedError` 子类）→ **停止产出可执行动作**，不降级继续。
3. **「时区错误」在日线场景的具体定义**（回应评审——原文只抄了宪法词条，实现时无处下手）：
   - `Bar.date` 是 **date-only**，语义为 **US/Eastern 交易 session 日期**；
   - **拒绝**带时区或带时刻的 timestamp 混入（provider 返回 tz-aware datetime 时，必须显式转 US/Eastern 后取 date，不得靠本地时区隐式转换）；
   - **拒绝**同一 symbol 出现重复 session date；
   - **拒绝** session date 落在交易日历之外（日历可用时）；
   - 跨时区运行（如 UTC 的 CI）与本地运行必须得到**逐 bar 相同**的 date，单测覆盖。
4. Parquet 缓存幂等：同一 `as_of` 重跑，缓存内容与下游结果一致。
5. CLI 新增 `smm ingest --as-of YYYY-MM-DD`。
6. 策略阈值仍然只能来自 config；**校验阈值本身也是参数**（如"单日跳变多少算异常"、`max_snapshot_age_days`），须进 YAML 而非硬编码。
7. §3.3 的消费边界必须以类型/视图强制，并有单测；`to_tradeable` 只接受 `PrintBar`，不接受 provider-native `Bar`。

---

## 后果

### 正面

- M2 特征引擎可以直接假设「adj 价可派生且已校验」，不必各自处理复权。
- 股票池与行情都绑定 `as_of`，回放可复现。
- 数据质量问题在入口停机，不会污染下游信号统计。
- 默认安装与默认 CI 无网络依赖，单测保持 hermetic。

### 代价

- `Bar` 扩字段会破坏 Phase 0 已冻结的 fixture 列格式与 4 个既有单测，需在同一 PR 内一并更新。
- 静态快照需要人工定期刷新；超过 `max_snapshot_age_days` 会停机。
- yfinance 无 SLA，日更流程可能因上游波动而停机。这是 fail-closed 的预期表现，不是缺陷。
- §3.4 的拆股口径实测会给 M1 增加工作量，但它挡的是一类**会产生假信号**的静默错误。

### 必须遵守

- provider-native / adjusted / true-print 三种语义的消费边界不得互串，且必须可测（§3.3）。
- 快照文件的 survivorship disclaimer 不得删除。
- 任何"数据缺失时用某个默认值继续"的补丁都视为违反宪法原则 11 与 fail-closed 约定。
- 缺 `adj_close` 时**不得**用 `close` 顶替。

---

## 备选（未采纳）

| 方案 | 原因 |
|------|------|
| Tiingo / Polygon 免费档 | 数据质量更稳，但需 API key 与密钥管理；V1 阶段收益不足以抵消接入成本。provider 可替换，未来质量成为瓶颈时再切 |
| 仅本地 CSV 导入，推迟选型 | 把选型推到更晚只是延后同一个决定，且需要人工准备数据，反而拖慢 M1 |
| yfinance 进核心 `dependencies` | 会让纯单测/无网环境也强依赖它，且 CI 易滑向依赖真实网络。改为 `smm[market]` extra |
| 运行时抓取维基百科成分股 | 页面结构易变、结果不可复现、无法绑定 `as_of`，违反幂等与边界要求 |
| 手工 20–50 只小池子 | 最快见到端到端结果，但横截面排名（动量 / RS 的核心）在小池子上无意义，会给出误导性的信号形态 |
| 旧快照可无限期服务 | 成分股持续变动，静默沿用会让横截面排名建立在错误样本上且不会自我暴露 |
| `Bar` 只存复权价 | 直接违反宪法 §12.1；且止损价会随未来的分红拆股而变化，历史成交无法复现 |
| 特征层只读 `adj_close`（本 ADR rev.1 原案） | ATR、延伸过滤、range 类特征需要复权 high/low/open，只有一条收盘序列会导致「动量用 adj、ATR 用 raw」的静默混用 |
| 持久化 `adj_open/high/low` 三列 | 无信息增量（可由 `adj_factor` 派生），多存只增加不一致的失败面 |
| 提交 252 行生成后 CSV + CI 校验一致 | 可行但 diff 噪声大；生成器 + 固定种子已足够，golden hash 可补回归保护 |
| 继续手写长 fixture CSV | 252+ 行不可维护，且无法保证形态满足待测条件 |
| 推迟双价格改造到 MVP-B | 届时需同时返工 domain、provider 协议与全部 fixture，成本远高于现在 |

---

## 开放项（不阻塞 M1）

| 项 | 归属 | 说明 |
|----|------|------|
| 板块 RS 缺失时的处理 | M2 | config 有 `rs_sector_63_weight: 0.20` 但暂无板块数据源。**非规范偏好（不约束 M2 决策）：** 缺板块时该子项标记 missing、不填有利默认，与现有 fund-as-filter 哲学一致；**是否重新归一化 RS 子权重留待 M2 定** |
| 成交量拆股口径的最终计算规则 | M1 实测 → M2 | 见 §3.4。实测结论出来前不得假定 `relative_volume` 安全 |
| 快照刷新频率与流程 | M1 后 | 建议进 runbook；`max_snapshot_age_days` 首值 90 待实践校准 |
| 财报日历数据源 | M3+ | 事件过滤所需；yfinance 的财报日期可靠性待评估 |
| 公司行动的审计级数据源 | 正式回测前 | yfinance adj 非 PIT，正式结论需独立源，另立 ADR |

---

## 评审回应记录

针对 [PR #1](https://github.com/xxxxxthhh/swingMomentum/pull/1) 评审意见的处置：

| # | 评审意见 | 处置 |
|---|----------|------|
| 1 | #3 仅 `adj_close` 不足以支撑 ATR / 延伸过滤 | **采纳方案 A** — §3.1/§3.2 改为 `adj_factor` 派生全 OHLC，只持久化 `adj_close` + `adj_factor` |
| 2 | yfinance adj 非严格 PIT 须写入限制清单 | **采纳** — §1.1 |
| 3 | 消费边界应做成可测不变量 | **采纳** — §3.3 改为视图强制 + 单测，§「约束」第 7 条 |
| 4 | 快照「最新 / 不回退」表述打架 | **采纳** — §2.1 重写为允许/禁止表 |
| 5 | 未定义快照过期 | **采纳** — §2.2 新增 `max_snapshot_age_days`（选择停机而非沿用），并标注 config_hash 影响待确认 |
| 6 | fixture 产物是否入库未写清 | **采纳** — §4.1 定为生成器为真相源、测试内生成、不提交长 CSV |
| 7 | yfinance 宜作 optional extra | **采纳** — §1 改为 `smm[market]`，并写明 CI 不得依赖真实网络 |
| 8 | 日线场景「时区错误」需可实现的定义 | **采纳** — §「约束」第 3 条给出四条具体判据 |
| 9 | 板块 RS 建议写入非规范偏好 | **采纳** — 开放项表，明确标注不约束 M2 决策 |
| — | *（作者补充，评审未提）* 成交量拆股口径会污染 `relative_volume` | **新增 §3.4**，列为 M1 必须实测确认项 |

### 二次评审裁决（rev.3）

九项处置与 §3.4 补充均获确认。两项交由评审裁决的问题已裁定：

| 问题 | 裁决 | 落点 |
|------|------|------|
| 快照过期 fail-closed 是否过苛？ | **不过苛，维持 fail-closed**；过吵则调阈值或补 runbook，不降级为告警 | §2.2 |
| 新增 config 键是否 bump 策略版本？ | **不 bump**；`config_hash` 变、版本号不变。「凡进 YAML 皆 bump」未采纳 | §2.4（新增划界表） |

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-22 | proposed | M1 实现前提交评审 |
| 2026-07-22 | proposed (rev.2) | 回应 PR #1 评审：9 项采纳 + 1 项作者补充（成交量拆股口径） |
| 2026-07-22 | **accepted** (rev.3) | PR #1 二次评审批准；裁定维持快照 fail-closed、不 bump 策略版本；新增 §2.4 版本纪律划界 |
| 2026-07-22 | **accepted** (rev.4) | PR #2 实测修正 §3.1 措辞（Yahoo 主序列为 split-adj/div-unadj）；确认 §3.4 风险在本 provider 不成立；新增 §5 附录与 MVP-B 前置裁决 |
| 2026-07-22 | **accepted** (rev.5) | Issue #4 采纳方案 A；provider-native `Bar` 与 true-print `PrintBar` 类型分离，MVP-B 缺公司行动重建器时 fail-closed |
