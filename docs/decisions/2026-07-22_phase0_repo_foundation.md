# ADR：Phase 0 仓库基础、Domain 优先与研究边界

| 字段 | 值 |
|------|-----|
| 文档类型 | decision |
| 状态 | accepted |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0 |
| 关联规格 | [../../CONSTITUTION.md](../../CONSTITUTION.md) |
| 关联评审 | [../reviews/2026-07-22_repo_skeleton_review.md](../reviews/2026-07-22_repo_skeleton_review.md) |
| 关联计划 | [../plans/2026-07-22_phase0_foundation_implementation_plan.md](../plans/2026-07-22_phase0_foundation_implementation_plan.md) |
| 关联边界 | [../system-boundary.md](../system-boundary.md) |
| 变更摘要 | 编码前增加 Phase 0；domain/config/fixtures/experiments/CI；禁止过早 Scanner |

---

## 背景

仓库已具备 docs 审计链与 Phase 1 Plan v1.1，但尚无实现代码。[骨架评审](../reviews/2026-07-22_repo_skeleton_review.md) 指出：若直接实现 Scanner / yfinance，会跳过可演化操作系统所需的中间层（领域模型、配置、合成测试、实验治理）。

---

## 决策

### 1. 插入 Phase 0（Foundation）

在 Phase 1 业务里程碑（真实数据、特征、Scanner）之前，必须完成 **Phase 0**：

| 做 | 不做 |
|----|------|
| 包结构、`pyproject.toml`、lint/test | yfinance / 付费行情接入 |
| Domain 类型与不变量 | 真实突破扫描业务完整实现 |
| Config 加载与校验 + config_hash | 指标工厂大而全 |
| Mock/Fake provider 接口 + fixtures | Paper 成交与熔断完整业务 |
| pytest + CI（至少 PR 上 test） | 回测收益结论 |
| `experiments/` 与 `notebooks/` 约定 | 实验统计与执行 Shadow 混用 |

### 2. 目录约定（冻结）

```text
swingMomentum/
  configs/                 # 冻结策略参数（真相源之一）
  src/smm/
    domain/                # Signal, Order, Position, Trade, RiskDecision, ...
    config/                # load + validate + hash
    core/                  # 共享错误类型、as_of、版本身份等
    data/                  # Provider 协议；Phase 0 仅 Fake/Mock
    # features/scanner/risk/paper... 在 Phase 1 按 plan 追加
  tests/
    fixtures/              # 合成 OHLCV 与期望状态
    unit/
  experiments/             # 研究实验；非执行路径
  notebooks/               # 只读分析；禁止写执行库
  docs/
  .github/workflows/       # CI
```

### 3. Domain 优先于 DataFrame

- 跨模块边界传递 **领域对象**（或明确的 DTO），而不是裸 DataFrame 作为唯一契约。  
- DataFrame 可用于内部计算与 Parquet I/O，但 **Signal / Order / Position / Trade / RiskDecision** 必须有稳定类型与字段语义。  
- 状态机状态为 domain 一等概念（与 Plan v1.1 一致）。

### 4. 配置纪律

- 策略阈值与权重 **只** 来自 `configs/*.yaml`（经校验）。  
- **禁止** 在业务代码中硬编码如 `1.3`、`0.005` 等策略参数（测试可读常量除外，且应与 config fixture 对齐）。  
- 每次加载计算 `config_hash`；写入审计链预留。

### 5. ADR 存放

- 继续使用 **`docs/decisions/`** 作为 ADR 目录（不另建 `docs/adr/`，避免双轨）。  
- 命名保持项目约定：`YYYY-MM-DD_short_snake_topic.md`。  
- 可选：文内增加 `ADR-ID` 字段便于引用；不强制改文件名为 `0001-...`。

### 6. 实验与研究治理

- **`experiments/`**：每个实验一目录，至少 `hypothesis.md`、`config.yaml`（或指向 configs 变体）、`result.md`。  
- **`notebooks/`**：探索与可视化；不写入执行库；结论要晋级必须回写 reviews/decisions/config。  
- 执行中的 Shadow/Paper **不得** 与未版本化实验改参混统计。

### 7. 系统边界

- 以 [docs/system-boundary.md](../system-boundary.md) 为准。  
- 系统输入限于价格、成交量、（可选）基本面/财报日历、组合与风险状态。  
- **排除：** 社交媒体情绪、个人信念、分析师目标价、未版本化的「AI 直接下单」预测。

### 8. 与 Phase 1 Plan v1.1 的关系

- Plan v1.1 的 M0 由 Phase 0 **细化并前置**；Phase 0 完成后才进入 v1.1 的 M1+（真实数据等）。  
- MVP-A / MVP-B 切片 **不变**。

---

## 理由

1. 交易系统长期演化依赖稳定 domain 与配置边界，而非先堆指标。  
2. Synthetic + Fake provider 使逻辑可测，不绑外部 API。  
3. experiments 治理避免「notebook 科学」污染执行统计。  
4. 单一 decisions 目录保持现有审计地图简单。

---

## 后果

### 正面

- Phase 1 业务可挂在清晰接口上  
- 未来 Portfolio Risk / Options / 研究助手可共享 domain 与 config  
- CI 从第一天约束回归  

### 代价

- 首周可见「可交易信号」更晚（可接受）  
- Domain 过设计风险 → Phase 0 只建 **V1 最小对象集**，禁止抽象过度  

### 必须遵守

- Phase 0 合并前：无 yfinance 依赖作为主路径、无完整 Scanner 业务 PR  
- 新增策略参数必须进 config 并变更 hash  

---

## 备选（未采纳）

| 方案 | 原因 |
|------|------|
| 直接 M1 接 yfinance + Scanner | 跳过可测骨架，易形成脚本坟场 |
| 另建 docs/adr/ 与 decisions 并存 | 双轨混乱 |
| 纯 DataFrame 管道无 domain | 边界模糊，状态机与审计难 |

---

## 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-22 | accepted | 骨架评审后冻结 Phase 0 |
