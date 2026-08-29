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

---

# Optimization V2 Closure（返工阶段）记录

> 依据：`docs/optimization-v2-review-and-closure-plan.md`（2026-08-29 评审结论）
> 返工基线 HEAD：`543d267`（"chore: ignore pytest basetemp directories"）
> 执行协议：C-01 原子工作包（读代码 → 实现 → 专项测试 → 回归 → 独立提交）、C-02 不回退新架构、C-03 Harness 保护区不动、C-04 复用唯一生产路径、C-05 后端约束优先、C-06 错误路径必须测。

## C0 — 返工基线与仓库清理

- 清理对象：`backend/.pytest-*-tmp/` 下 21 个 pytest basetemp 目录、93 个被误跟踪的运行时产物（测试 SQLite `.db`、MediaCrawler 运行 JSONL、MediaCrawler stub `main.py`）。
- 处理方式：`git rm --cached`（仅解除 Git 跟踪，磁盘文件保留，由 `.gitignore` 的 `.pytest-*-tmp/` 规则持续忽略）；不触碰任何静态 fixture。
- 验收：`git ls-files` 中不再出现 `.pytest-*-tmp/`；`git status` 仅含本次清理与文档记录。

## C1 — 封死 Finding 绕过 Human Review 的状态路径（commit：fix: enforce review-only finding verification）

- `finding_service.py`：`ALLOWED_TRANSITIONS` 移除 `under_review→verified/rejected`；普通 `update_status()` 对终审态返回专用错误码 `finding_review_required`（不复用模糊的 `finding_invalid_transition`）；合法迁移保留 candidate⇄under_review、verified/rejected→under_review（重新提交复审）、全部→superseded。
- Review 唯一裁决路径不动：`decide_review_item()` 同事务同步 ReviewItem/ReviewDecision/Finding 保持原样。
- `schemas/findings.py`：`UpdateFindingStatusRequest.status` 收窄为 `Literal["candidate","under_review","superseded"]`，Service 保留最终防线。
- 测试（`tests/test_findings.py`）：candidate→verified 拒绝、under_review→verified/rejected 拒绝（finding_review_required）、Review approved→verified、Review rejected→rejected、Review 冲突（乐观锁失败）Finding 状态不变、verified→under_review 重开后再 verified 仍需 Review、API 层 verified 请求 422 + under_review 200。旧 `under_review→verified` 通路测试已改写。
- 专项测试：`pytest tests/test_findings.py`（10 passed）；受影响回归：`pytest tests/test_findings.py tests/test_claim_review.py tests/test_legacy_compatibility.py`（15 passed, 0 failed）。

## C2 — Finding Evidence Integrity 与 Case Scope（commit：fix: validate finding evidence references and case scope）

- `finding_service.py` 新增 `_evidence_ref_problem()` / `_validate_evidence_ref()`：只认数据库 `EvidenceRecord`（禁止 `ev-` 前缀猜测）；不存在 → `finding_evidence_not_found`，跨 case → `finding_evidence_scope_mismatch`。
- 手动路径 fail closed：`add_evidence_link()` 顺序为 Finding → relation → Evidence 校验 → 创建；`create_manual()` 混入非法引用时整体拒绝。
- Materializer 宽容化：`sync_from_artifact()`/`_materialize()` 对 artifact 引用的 Evidence ID 逐条校验，无效引用跳过 link、Finding 照常物化、返回可审计 `warnings`（type/artifact_id/finding_source_path/evidence_ref/reason）；`FindingSyncResponse` 增加 `warnings` 字段；幂等保持（重复 sync 不重复 link）。
- 测试：真实同 case Evidence 成功、不存在/跨 case 拒绝（service + API 400）、混合合法/非法只保存合法 link、无效引用 warning、幂等、provenance 无幽灵节点。
- 专项测试：`pytest tests/test_findings.py tests/test_report_documents.py::test_delete_case_removes_finding_tables`（10 passed）。

## C3 — Report Publish Gate citation 校验重写（commit：fix: validate real report citation structures before publish）

- `report_document_service.py`：删除基于前缀的 `_evidence_id_from_ref()`，重写为 `_normalize_citation_refs()`（归一化为 `(type, id, path)`，支持字符串 / evidence(_id)(_ids) / finding(_id)(_ids) / artifact(_id)(_ids) / generic ref|id）+ `_citation_ref_problem()`（Evidence→Finding→Artifact 顺序在当前 case 内解析，generic 无类型时依次尝试）。
- Unknown shape fail closed：无可解析引用（如只有 `conclusion` 文本）→ `unresolvable_ref` 阻止 publish。
- `ApplicationError` 增加可选 `details`，publish 失败响应携带逐条定位（如 `citation_links[0].evidence_ids[1] → evidence_not_found` / `evidence_not_in_case`）。
- 测试：`evidence_ids[]` 全合法通过、不存在/跨 case 阻止（details 精确断言）、finding/artifact/generic 引用合法、unknown shape 阻止、generic 幽灵对象 `unresolvable_ref`、API 层跨 case citation publish 被 400 + details 阻止。
- 专项测试：`pytest tests/test_report_documents.py`（9 passed）。
