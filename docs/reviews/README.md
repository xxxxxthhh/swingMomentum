# reviews/ — 复盘与评审

存放**带日期的评估结论**，与实时信号日志区分：

- 周度流程复盘
- 月度研究会议纪要
- Shadow / Paper 阶段门槛检查
- 晋级或拒绝晋级的书面结论
- 计划 / 设计文档评审

要求：写明评估窗口、策略版本、是否改过参、主要指标与下一步，避免事后改写历史结论。

---

## GitHub 评论身份（同账号强制）

Builder 与 Task Reviewer 可能使用**同一 GitHub 账号**，仅靠 `login` 无法区分。因此：

| 角色 | 每条 PR / Issue 评论的**第一行**（固定） |
|------|------------------------------------------|
| **Task Reviewer** | `**Task Reviewer**` |
| Builder（实现方） | `**Builder**`（建议对称遵守，避免混线） |

示例：

```markdown
**Task Reviewer**

## Summary
...
```

- Reviewer loop / 人工代发 review comment **必须**带上述首行。
- Reviewer **只** comment，不 merge、不改业务代码（除非用户另行授权）。
- 无新活动时不重复刷同一结论。

---

当前：

- [2026-07-22_phase1_plan_review.md](./2026-07-22_phase1_plan_review.md)（accepted）
- [2026-07-22_repo_skeleton_review.md](./2026-07-22_repo_skeleton_review.md)（accepted）
