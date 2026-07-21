# System Boundary — Swing Momentum (SMM)

| 字段 | 值 |
|------|-----|
| 文档类型 | spec（边界） |
| 状态 | active |
| 日期 | 2026-07-22 |
| 策略版本 | SMM-V1.0.0 |
| 关联规格 | [../CONSTITUTION.md](../CONSTITUTION.md) |
| 关联决策 | [decisions/2026-07-22_phase0_repo_foundation.md](./decisions/2026-07-22_phase0_repo_foundation.md) |
| 变更摘要 | 明确系统知道什么 / 不知道什么 / 不做什么 |

---

## 1. 目的

防止系统在演化中滑向「预测机器」或「情绪交易器」。  
凡新增数据源、特征或自动化能力，必须先对照本文；越界须新 ADR + 策略版本评估。

---

## 2. System knows（允许进入决策的信息）

| 类别 | 示例 | 阶段 |
|------|------|------|
| 价格与成交量 | OHLCV、复权字段（及限制说明） | Phase 0+ |
| 交易日历 | 交易日 / 休市 | Phase 0+ |
| 衍生特征（由价格量计算） | 收益、均线、ATR、相对量、横截面排名、RS | Phase 1 MVP-A |
| 市场状态 | SPY（及可选 QQQ）regime | Phase 1 MVP-A |
| 组合与持仓状态 | 纸面持仓、heat、板块/簇暴露 | Phase 1 MVP-B |
| 风险规则输出 | 接受/拒绝、仓位、熔断状态 | Phase 1 MVP-B |
| 基本面 / 事件（受限） | 红旗过滤、财报窗口（若有可靠日历） | V1 过滤优先，非主评分 |
| 版本与审计 | strategy_version、config_hash、reason codes、人工否决日志 | 全程 |
| 合成/测试数据 | fixtures 与 Fake provider | Phase 0+ |

所有上述输入必须可绑定到 `as_of_date`，并满足无前视约束（实现阶段强制测试）。

---

## 3. System does not know（禁止作为信号/下单依据）

| 类别 | 示例 | 原因 |
|------|------|------|
| 社交媒体与舆论 | Twitter/X、Reddit、热搜 | 不可审计、不可稳定复现 |
| 个人信念 | 「我觉得还会涨」、朋友推荐 | 违反证据优先 |
| 分析师目标价 / 评级 | Street target、升级降级叙事 | 非本策略边；易叙事驱动 |
| 新闻 NLP 情绪分 | 标题情感、主题热度 | Phase 边界外；另立项另版本 |
| 未版本化模型分数 | 临时 notebook 里的「AI 分」 | 破坏可复现与混统计禁令 |
| 内幕或非公开信息 | — | 合规与宪法禁止 |
| 盘中噪声（Phase 1） | Level-2、秒级 tick | 非日线 swing 范围 |

**人工否决**可以发生，但：

- 只能 **SKIP / 否决**，不能强行加候选；  
- 必须写入 `manual_decisions`（或等价审计）；  
- 否决理由不自动变成下一版特征，除非走正式研究 → decision → config 变更。

---

## 4. System does not do（Phase 1 及默认）

- 自动真实下单（live broker 路由）  
- 做空、杠杆、期权执行（期权为后续独立策略编号）  
- 以信号日收盘价假设成交  
- 数据缺失时静默填有利默认并继续交易  
- Scanner 绕过 Risk Engine  
- 研究配置与执行中 Shadow/Paper 混统计  
- 用当前成分股回测结果宣称正式历史绩效（无 disclaimer）  

---

## 5. 扩展准入（如何合法变大）

若希望加入例如「新闻分析」或「AI 研究助手」：

1. 写清假设与是否影响 **可执行信号**；  
2. 新 ADR + 评估策略版本 bump；  
3. 默认识别为 **研究轨**（experiments/），不写入执行 config；  
4. 仅当独立验证通过且不破坏 fail-closed / 可审计时，才考虑进入执行路径。  

**AI Research Assistant** 可以辅助复盘与文档，但 **不得** 在未版本化情况下直接产生订单。

---

## 6. 与仓库结构的映射

| 边界概念 | 落点 |
|----------|------|
| 允许输入 | `DataProvider`、features、config |
| 核心决策对象 | `src/smm/domain/` |
| 禁止输入 | 无模块；code review 与本文共同约束 |
| 研究越界实验 | `experiments/`、`notebooks/` only |
| 为何排除某输入 | `docs/decisions/` |

---

## 7. 状态历史

| 日期 | 状态 | 说明 |
|------|------|------|
| 2026-07-22 | active | 初版；随 Phase 0 生效 |
