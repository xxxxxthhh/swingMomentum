# 项目文档库（Audit Trail）

本目录是 swingMomentum 项目的**唯一正式文档入口**。  
凡影响策略身份、风险边界、实现路径、验证结论或运行纪律的文字，都应落在此处，并可被追溯。

交易系统的核心原则之一是：**每个决策必须可解释、可计算、可复现、可审计**。  
文档与代码、配置、信号快照属于同一审计链，不可只存在于聊天记录或个人笔记中。

---

## 目录结构

| 路径 | 用途 | 典型内容 |
|------|------|----------|
| [`specs/`](./specs/) | 规格与宪法 | 交易宪法、策略规格、数据/验证规范 |
| [`plans/`](./plans/) | 实施与路线图 | Phase 计划、里程碑、PR 切分 |
| [`decisions/`](./decisions/) | 决策记录（ADR 风格） | 技术选型、范围裁剪、参数冻结理由 |
| [`runbooks/`](./runbooks/) | 运行手册 | 日更流程、故障停机、备份恢复 |
| [`reviews/`](./reviews/) | 复盘与评审 | 周/月复盘、Shadow/Paper 评估、晋级门槛检查 |

---

## 当前基线文档

| 文档 | 状态 | 说明 |
|------|------|------|
| [specs/Personal_Trading_Constitution_and_Swing_Momentum_Spec_v2.md](./specs/Personal_Trading_Constitution_and_Swing_Momentum_Spec_v2.md) | Baseline v2.0 | 最高层设计：宪法、策略、验证、工程约束 |
| [system-boundary.md](./system-boundary.md) | Active | 系统知道什么 / 不知道什么 / 不做什么 |
| [plans/2026-07-22_phase0_foundation_implementation_plan.md](./plans/2026-07-22_phase0_foundation_implementation_plan.md) | Approved | **下一步实现**：Phase 0 工程底座（先于 Scanner） |
| [plans/2026-07-22_phase1_implementation_plan_v1_1.md](./plans/2026-07-22_phase1_implementation_plan_v1_1.md) | Approved | Phase 1 业务实施 v1.1（MVP-A → MVP-B） |
| [decisions/2026-07-21_phase1_scope_and_stack.md](./decisions/2026-07-21_phase1_scope_and_stack.md) | Accepted | Phase 1 技术栈（§1）；§2/§3 见后续 ADR |
| [decisions/2026-07-22_phase1_mvp_slicing_v1_1.md](./decisions/2026-07-22_phase1_mvp_slicing_v1_1.md) | Accepted | 两阶段 MVP、Watchlist、状态机、Setup/Fund 边界 |
| [decisions/2026-07-22_phase0_repo_foundation.md](./decisions/2026-07-22_phase0_repo_foundation.md) | Accepted | Phase 0：domain/config/fixtures/experiments/CI |
| [reviews/2026-07-22_phase1_plan_review.md](./reviews/2026-07-22_phase1_plan_review.md) | Accepted | Phase 1 计划评审结论 |
| [reviews/2026-07-22_repo_skeleton_review.md](./reviews/2026-07-22_repo_skeleton_review.md) | Accepted | Repo 骨架评审；驱动 Phase 0 |

### 历史 / 已替代

| 文档 | 状态 | 说明 |
|------|------|------|
| [plans/2026-07-21_phase1_implementation_plan.md](./plans/2026-07-21_phase1_implementation_plan.md) | Superseded | Phase 1 计划 v1.0；由 v1.1 替代 |

---

## 审计约定

### 1. 单一事实来源

- **策略是什么**：以 `specs/` 为准。实现与文档冲突时，默认实现有问题。
- **系统边界**：以 [`system-boundary.md`](./system-boundary.md) 为准。
- **怎么建系统**：以 `plans/` 为准（先 Phase 0，再 Phase 1）。
- **为什么这样选**：以 `decisions/` 为准（ADR；本目录即 ADR 存放处，不另建 `adr/`）。
- **怎么日常跑**：以 `runbooks/` 为准。
- **结果是否过关**：以 `reviews/` 与运行产物（信号/交易/报告）为准。

### 2. 命名规范

```text
YYYY-MM-DD_short_snake_topic.md
```

示例：

- `2026-07-21_phase1_implementation_plan.md`
- `2026-07-21_phase1_scope_and_stack.md`

版本化规格可用语义化文件名，例如：

- `Personal_Trading_Constitution_and_Swing_Momentum_Spec_v2.md`
- 重大修订升版本，**不覆盖**旧版正文；旧版可移入 `specs/archive/`（需要时再建）。

### 3. 文档头（推荐元数据）

每份正式文档开头应包含：

```markdown
# 标题

| 字段 | 值 |
|------|-----|
| 文档类型 | spec / plan / decision / runbook / review |
| 状态 | draft / approved / accepted / superseded / archived |
| 日期 | YYYY-MM-DD |
| 策略版本 | SMM-Vx.y.z（若相关） |
| 关联 | 链接到相关 specs/plans/decisions |
| 变更摘要 | 一句话 |
```

### 4. 变更纪律

- 不删除历史决策；状态改为 `superseded`，并链接替代文档。
- 策略参数或逻辑变更必须：
  1. 更新或新增 `specs/`（或明确仍由冻结 config 表达）；
  2. 新增 `decisions/` 记录原因与证据；
  3.  bump 策略版本（Major/Minor/Patch 规则见宪法文档）；
  4. **禁止**在同一评估周期中途改参并混合统计。
- 聊天中的结论不算数，直到写入本目录。

### 5. 与代码/数据的关系（后续实现时）

审计链最小集合：

```text
docs/specs + docs/decisions
    → configs/（冻结参数）
    → git commit
    → signals / trades / reports
```

每笔信号与报告应能追溯到：策略版本、config hash、相关文档版本。

---

## 状态词说明

| 状态 | 含义 |
|------|------|
| `draft` | 起草中，不可当作执行依据 |
| `approved` | 计划已批准，可据此实施 |
| `accepted` | 决策已接受并冻结（直到被 superseded） |
| `active` | 当前生效的 runbook/spec |
| `superseded` | 已被更新文档替代，保留供审计 |
| `archived` | 不再使用，仅历史存档 |

---

## 新增文档检查清单

写入前自问：

1. 这份文档解决什么问题？属于哪个子目录？
2. 是否写了日期、状态、关联文档？
3. 是否会让三个月后的自己仍能理解「当时为什么这样」？
4. 若改变系统行为，是否触发了策略版本与实验隔离要求？
5. 是否避免把研究想法直接写成当前执行规则？

---

## 维护说明

- 本 `README.md` 是文档库地图；新增重要基线文档时，更新上方「当前基线文档」表。
- 不在此目录存放大体量原始行情数据；数据与报告路径由后续工程约定，但**规则与结论**必须回写文档或可追溯产物索引。
