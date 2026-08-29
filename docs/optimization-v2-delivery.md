# Optimization V2 交付记录（M0–M8）

> 生成时间：2026-08-29。执行依据：`docs/Nothing-in-the-dark_Optimization_Execution_Plan_V2.md`。
> 基线记录见 `docs/optimization-v2-baseline.md`。

## 提交索引

| Commit | 里程碑 | 内容 |
|---|---|---|
| `b1e6c21` | M0 | 基线快照 + `test_legacy_compatibility.py` 兼容护栏 |
| `c8490e0` | M1 | Investigation Router/Global Shell/产品文案/Operational Home v1 |
| `4188ec2` | M2.1–M2.3 | `useRunSubscriptions` 提取、结构化 `ui_context`、Investigation Shell |
| `d53d702` | M2.4–M3 | Copilot Drawer、Activity 语义层、版本化 Collection Definition 全链 |
| `3ac88ff` | M4 | Findings + Provenance + Review 原子同步 + Evidence/Findings 工作区 |
| `8fd2673` | M5 | Network/Timeline/Live Data 一等工作区 |
| `1194d0a` | M6 | Global Signals Inbox + Workspace Overview + Home v2 |
| `622b521` | M7 | ReportDocument 发布流（draft→review→publish→archive + HTML 导出） |
| （本次） | M8 | lint 基线清零 + 交付记录 |

## 关键设计落地

- **E-04 保护区**：LangGraph/Durable Run/Approval/Sandbox/SSE 全程未动语义；crawl 仅接入 Active Collection Definition 的关键词来源（approval/sandbox 顺序不变，输出附 `collection_definition` 审计引用）。
- **数据库**：三次迁移（0046 collection_definitions、0047 findings 三表、0048 report_documents），均为新增表，向后兼容；PG DDL 已离线验证（含 partial unique index）；`delete_case()` 显式级联已同步扩展。
- **状态机**：Finding（candidate→under_review→verified/rejected；verified 只能来自 Review 决策，`decide_review_item` 同一事务同步）；Report（draft→in_review→published→archived，乐观锁 `lock_version`）；Signal（完全复用 Alert 状态机，无新表）。
- **产品语言**：UI 全面切换为"调查/Investigation"；后端 `case_id`/`cases` 表保留（计划书 3.1）。
- **API 模块化**：新增 collections/findings/signals/reports/workspace 五个前端 API 模块，`services/api.ts` 不再膨胀。

## 测试结果

- 后端基线全量：**778 passed**（改动前，2026-08-29）。
- 本轮新增专项：collection 10、findings 7、signals 3、report 6、ui_context 5、legacy_compat 2 ≈ **33 个新测试全绿**。
- **M8 核心回归套件：133 passed / 0 failed**（19 个测试文件，覆盖全部新增模块 + durable runtime/approval/sandbox/security/crawl/tool system/case deletion/review/context builder 等被改区域）。
- 前端：typecheck ✓、vitest **121 passed**、build ✓、**eslint 0 error**（基线 39 → 0：e2e cjs 加入 ignores，unused import 清理）。

## 已知限制（如实记录，非未来愿望清单）

1. **M5.7 深迁移未完成**：`SemanticAnnotationsView`（→Evidence 子 tab）与 `GoalPlanningView`（→Overview Plan）仍是独立 legacy 路由（`/semantics`、`/goals`），侧栏入口已移除但页面未内嵌进新工作区。
2. **CaseWorkspaceView 保留**：已无路由引用，但因 Semantics/Goals 未完全迁入（见上），M8.3 删除条件未全部满足；文件与其 34 个组件测试保留。
3. **SubscriptionsView 分流**：按计划书"先阅读实现再分流"的要求未及处理，路由保留（`/subscriptions`）。
4. **Live Data 的 Posts 列表**：无统一 raw-post 列表 API，页面先提供 Platform Comparison + Media 两个 tab（不伪造完整列表）。
5. **E2E（Playwright）**：`e2e:smoke` / `e2e:interact` 依赖运行中的前后端服务，本轮未在 CI 环境执行；Scenario A–H 的浏览器级 E2E 待环境具备后补跑。
6. Copilot Drawer 的历史构建为简化版顺序配对（与旧工作台的重建算法存在差异），属于过渡实现。
