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

## C4 — Signal 与 Monitor 共用 Alert 状态机（commit：fix: unify monitor and signal alert transitions）

- 新增 `app/services/alert_state.py`：纯 domain validator `validate_alert_transition(current, target)`（不访问数据库），`VALID_ALERT_TRANSITIONS` 从 monitors 路由迁入成为单一事实来源（open→acknowledged→resolved；suppressed 可自 open/acknowledged/resolved 进入；重复同状态非法）。
- `MonitorRepository.set_alert_status()` 加最终防线：读 current → validator → 非法抛 `alert_status_transition_invalid` → 更新。
- `SignalService.change_status()` 只做 action mapping（acknowledge/resolve/suppress），合法性由同一 validator 决定；monitors 路由删除本地 `_VALID_TRANSITIONS` 副本。
- 测试：resolved→acknowledge 拒绝、suppressed→resolve 拒绝、resolved→suppress 合法、Signal API 与 Monitor API 跨端操作后非法转换错误码一致（400 `alert_status_transition_invalid`）；既有 Monitor 状态机测试保持通过。
- 专项测试：`pytest tests/test_signals.py tests/test_monitoring.py::test_api_alert_status_machine`（6 passed）。

## C5 — Provenance 修正与双向链路补齐（commit：fix: complete case-scoped provenance relationships）

- `_resolve_artifact()` 直接读取 `ArtifactRecord` 并校验 case_id（真实 Artifact 无 Finding 也 200；不再靠 FindingSourceLink 间接判断存在）。
- `_resolve_finding()`：evidence upstream 逐条校验存在性；坏 link（不存在/跨 case，含 C2 前历史脏数据）不输出伪造 Evidence node，改以 `dangling_evidence_ref` warning 输出；downstream 新增 `report_document`（复用 citation normalizer 判定引用关系，cited_by）。
- 新增 `report_document` root：upstream=citations（Evidence/Finding/Artifact，generic 引用解析实际类型；解析失败 dangling_citation_ref warning）；downstream=同 family 后续 revision（supersedes 链，superseded_by）。
- C3 citation normalizer 提升为模块级 `normalize_citation_refs()`，Provenance 复用同一 parser；`ProvenanceResponse.warnings` 类型为 `list[dict]`。
- 测试（新文件 `tests/test_provenance.py`）：无 Finding 的真实 Artifact 可查、Artifact→Finding、Evidence→Finding、Finding upstream 兼容脏 link + Report downstream、ReportDocument refs + revision 链、跨 case root 统一 404（finding/artifact/report_document）、API 层 report_document root 冒烟。

## C6 — Collection exclusions/filters 真正生效（commit：fix: apply active collection exclusions and supported filters）

- 新增 `app/services/collection_filters.py`：`apply_collection_exclusions()` 在 normalized post 文本字段（title/content/description/summary/text）做 case-insensitive substring 排除，comment 跟随父记录；`validate_collection_filters()` 白名单校验（唯一合法 key `generated_by` 内部标记；未知 key → `collection_filter_unsupported`）。
- 采集链路：crawl handler 在 coverage/persistence 前应用 exclusions（不修改 MediaCrawler DSL、不绕过 SocialCrawlerPort、approval/sandbox 顺序不变）；输出新增 `collection_filter_stats`（before/after/excluded）审计；无 active definition 时旧路径不变。
- 保存与运行双层防线：`create_manual()/revise()` 保存时拒绝未知 filter key；crawl handler 防御性再校验。
- 测试（`tests/test_collection_tool_integration.py` 扩展）：exclusion 真实过滤 + 审计统计、无 active 无 stats、运行时未知 filter fail closed、保存时未知 filter 拒绝（generated_by 允许）。

## C7 — Propagation Network Workspace（commit：feat: implement investigation propagation network workspace）

- 后端：`GET /cases/{id}/propagation-graph`（nodes 按 post 去重聚合：主 role 取最高分 + roles 列表 + score；label/excerpt/platform/published_at 来自 SourcePostRecord join，posts 查询限定 case scope）；`ApplicationRepository.list_propagation_graph()` 一次装配 nodes + edges + 涉及 posts；未新建任何图数据表。
- 前端：新组件 `PropagationGraph.vue`（ECharts graph series：confirmed 实线绿 / 驳回红 / 推断虚线，透明度映射 confidence，角色颜色 source/burst/hub/bridge，节点尺寸映射 score）+ `PropagationDetailPanel.vue`（edge：relation/confidence/算法版本/特征分数/evidence IDs/人工确认 + 复用既有 confirmation API + Evidence 导航；node：roles/score/平台/摘录，candidate 明确标注"算法候选 · 非已证实结论"）。
- `InvestigationNetworkView.vue`：Propagation 模式替换 `VisualSidebar`/`PlatformComparisonCard`；selection → Copilot context（selected_type=propagation_node/propagation_edge）；确认后刷新图。
- 测试：后端 `tests/test_propagation_graph.py`（2）；前端 PropagationGraph 7、PropagationDetailPanel 5、InvestigationNetworkView 5（含"不渲染 PlatformComparisonCard"、selection context、确认 API + 刷新、loading/empty/error）。

## C8 — Evidence / Timeline / Live Data 工作区深化（3 个 commit，见下方）

### C8.1 Evidence Workspace（feat: deepen investigation evidence workspace）

- 抽内容组件 `EvidenceClaimList.vue`（claim 卡 + stance 分组证据 + 人工确认/驳回）与 `EvidenceDetailPanel.vue`（claim 全文/三 stance 分组/review；evidence 来源 metadata + provenance downstream findings）。
- `InvestigationEvidenceView.vue` 重写为真正工作区：左 filter（all/pending/verified/rejected）+ claim 列表 + 未分组证据计数；右详情面板。selection → Copilot context（workspace=evidence, selected_type=claim/evidence, selected_id）。
- 新增前端 API `getEvidenceProvenance()`（复用既有 provenance endpoint，不复制后端逻辑）；`EvidenceSidebar.vue` 保留（唯一剩余引用为 legacy CaseWorkspaceView，C11 处理）。
- 测试：EvidenceClaimList 4、EvidenceDetailPanel 4、InvestigationEvidenceView 6（filter/selection context/error/empty），旧 Sidebar 测试 8 个不受影响。

### C8.2 Timeline Workspace（feat: integrate investigation timeline context）

- 新组件 `TimelineWorkspaceContent.vue`：Volume Timeline（posts:stats 按天柱状图）/ Platform Timeline（按天×平台堆叠线）/ Narrative Timeline（复用既有 NarrativeTimelineView，新旧 route 临时复用）；时间范围选择进入 Copilot context（workspace=timeline, time_range）并过滤图表；无 Shell 的旧路由静默降级。
- 后端轻量只读聚合：`GET /cases/{id}/posts:stats`（`SocialRepository.list_post_time_rows()` Python 侧按天/平台聚合，双方言安全，无新持久化表）。
- 测试：TimelineWorkspaceContent 5（数据映射/tab 切换/time_range context/error/narrative）。

### C8.3 Live Data Posts（feat: add paginated live data posts）

- 后端：`GET /cases/{id}/posts`（platform/q/from/to 过滤 + limit/offset 分页 + has_more；响应仅暴露稳定字段，raw_payload/embedding/content_hash 不外泄）+ `GET /cases/{id}/posts:stats`。
- 前端：新 `PostsList.vue`（filters + 加载更多 + 打开原文 + selection）；`InvestigationLiveDataView.vue` 三 tab（Posts | Media | Platform Comparison），selection → Copilot context（workspace=live_data, selected_type=social_post, selected_id）。
- 测试：后端 `tests/test_posts.py` 2（分页/过滤/case 隔离/404/stats 聚合/字段白名单）；前端 PostsList 6 + LiveDataView 3（Posts 默认 tab/selection context/tab 切换）。

## C9 — M5.7 与 Subscriptions 分流（commit：refactor: migrate semantics and goals into investigation）

- C9.1：抽 `components/semantics/SemanticAnnotationsPanel.vue`（标注表/词典/在线分析三子区，case 由 prop 提供）；Evidence 工作区增加 `Claims / Semantics` 子 tab；后端 semantics API 不变。
- C9.2：抽 `components/goals/GoalPlanPanel.vue`（目标列表/完成条件/计划版本/计划 DAG/评估）；Investigation Overview 加入 Plan 区域。
- C9.3：新增 `views/admin/AdministrationNotificationsView.vue`（订阅/Webhook 端点/投递记录，保留 case selector，后端不变），Admin 导航加"通知"；Report 分享迁入 Reports 卡片（createShareLink 72h 链接）；`/subscriptions → /admin/notifications`、`/semantics → /investigations`、`/goals → /investigations` 兼容重定向；旧 SubscriptionsView/SemanticAnnotationsView/GoalPlanningView 路由引用移除（文件在 C11 统一清理）。
- 测试：AdministrationNotificationsView 4（tab 切换/创建订阅/无 share tab）、EvidenceView +1（semantics tab）；前端全量 171 passed、typecheck 0、lint 0。

## C10 — Copilot 历史重建共享（commit：fix: share robust chat history reconstruction with copilot）

- 新增 `services/chat/buildChatItems.ts`：迁入旧 CaseWorkspaceView 已验证的完整算法（expert assistant turn 归属、coordinator final answer 向后匹配且不消费专家 turn、orphan artifacts、无 turn run 兜底）+ `makeRunItem` + `preserveRunLiveState`（重建时保留 approvals/trace/traceLoading/liveEvents/liveToolCalls/liveModelCalls）。
- CopilotDrawer 删除简化版 `buildItems`/`findAssistantAfter`，改用共享 helper；`loadHistory()` 重建时保留 live 状态（修复终态重拉导致审批卡闪烁的问题）；CaseWorkspaceView 的 refreshChatItems 改用共享 `preserveRunLiveState`（行为不变）。
- 测试：buildChatItems 6（coordinator+expert、多 assistant turns、expert turn 不被 coordinator 消费、orphan artifact、无 turn run、refresh 保留 approvals/trace）；CaseWorkspaceView 34 个既有测试全部保持。

## C11 — Legacy 删除与最终 Closure（commits：chore: remove retired legacy workspace paths / test: update browser e2e scenarios to investigation ia / fix: add sqlite busy timeout）

### Legacy 删除（删除条件全部满足后执行）

- 删除 7 个退役文件：`CaseWorkspaceView.vue`（+其 34 项测试文件）、`EvidenceSidebar.vue`（+测试）、`VisualSidebar.vue`、`SubscriptionsView.vue`、`SemanticAnnotationsView.vue`、`GoalPlanningView.vue`。删除前验证：router 无生产引用、Copilot 历史已共享（C10）、Semantics/Goals 已迁入（C9）、Propagation/Live Data 已由新组件接管（C7/C8）。
- 保留：`NarrativeTimelineView.vue`（Timeline workspace 的 Narrative tab 仍复用，/narratives 兼容路由保留）。

### 浏览器级 E2E（frontend e2e-smoke.cjs / e2e-interact.cjs 扩展，未新建第三套框架）

- `e2e-smoke.cjs`：15/15 passed —— 新 IA 全路由（Home/Investigations/Signals/Reports/Admin 7 子页/Narrative）+ 3 个 legacy 重定向路由（/semantics /goals /subscriptions）。
- `e2e-interact.cjs`：41 checks 全过（1 项 SKIPPED 见下）。Scenario A–F 全部覆盖：
  - **A Investigation Shell**：创建调查 → overview/evidence/network/live-data/findings/report 六 tab 真实渲染（h1=调查标题）；
  - **B Finding Review**：candidate → under_review → 普通 API verified 被 422 拒 → Review item 创建/claim/approve → Finding 自动 verified（闭环）；
  - **C Report Publish Gate**：GET /reports 可用 + 不存在 artifact import 被 4xx 拒（publish gate 阻断语义已由 C3 单测覆盖正反两路）；
  - **D Propagation Graph**：GET /propagation-graph 200（nodes/edges 结构）；
  - **E Live Data**：GET /posts 分页结构（posts/has_more）+ /posts:stats；
  - **F Signals**：GET /signals 200。
  - Kill Switch fail-closed（409）通过；成功路径 SKIPPED（环境无 approved policy_exception 审批前置数据，审批由 Harness 运行时产生，无法自包含造数；该路径由 test_resilience 单测覆盖）。
- E2E 环境真实启动 backend（uvicorn :8000）+ frontend（vite :5173），全程无 console/pageerror。

### 完整后端回归（评审 17.2 硬性要求）

- **执行方式**：本机环境下单进程后台任务不可靠（多次被会话中断终止）且 4-worker 全量曾出现 xdist 并发死锁（test_api.py 的 SSE 测试在并行下 2h 无进展、单跑 49s 通过）。最终按 92 个测试文件均衡分为 16 个批次（14 个并行分片 + test_api.py/test_reports.py 串行——两者含 SSE 重交互测试，串行规避 xdist 死锁），**批次合集与全量测试文件集合 1:1 对应（脚本校验 0 遗漏）**。
- **最终结果：833 passed / 0 failed / 0 skipped**（baseline 778 + 本轮 Closure 新增 55）。
- 过程中发现并修复 1 个 flake 根因：SQLite 默认 busy timeout 为 0，并发写偶发 `database is locked`（`test_propagation_expert_artifact_backfills_edge_ids` 修复前 5 次中 2 次失败）→ `engine.py` 增加 `connect_args={"timeout": 30}`（仅 SQLite；PG 不受影响），修复后 5/5 稳定通过，并在最终批次结果中 0 失败。

### 前端四项质量门（评审 17.3）

- `npm run typecheck` ✓（0 error）｜ `npm run lint` ✓（0 error）｜ `npm run test` ✓（135 passed / 23 files）｜ `npm run build` ✓。

---

# Optimization V2 CLOSED

- [x] Finding verified/rejected 只能来自 Review（C1）
- [x] Finding 不存在/跨 Case Evidence 被拒绝（C2）
- [x] Report 真实 citation schema 全量校验（C3）
- [x] Signal / Monitor 共用状态机（C4）
- [x] Provenance 无伪造 Evidence node（C5）
- [x] Collection UI 已激活字段真实影响 crawl（C6）
- [x] Network/Propagation 是真实 graph workspace（C7）
- [x] Evidence 是真正 workspace（C8.1）
- [x] Timeline 有 time-range context（C8.2）
- [x] Live Data 有分页 Posts（C8.3）
- [x] Semantics/Goals 完成迁移（C9）
- [x] Subscriptions 完成新 IA 分流（C9）
- [x] Copilot 使用共享完整历史重建（C10）
- [x] legacy 满足条件后删除/redirect（C11）
- [x] tracked test temp artifacts 清理（C0）
- [x] full backend pytest 0 failed（833 passed / 0 failed / 0 skipped）
- [x] frontend typecheck 通过
- [x] frontend lint 通过
- [x] frontend test 通过
- [x] frontend build 通过
- [x] browser E2E Scenario A–F 通过

## 真正剩余限制（如实记录）

1. E2E Kill Switch 成功路径依赖 Harness 运行时产生的 policy_exception 审批数据，CI 环境无法自包含造数（fail-closed 路径已在 E2E 验证，成功路径由 test_resilience 单测覆盖）。
2. `/narratives` 旧路由与 NarrativeTimelineView 保留（Timeline workspace 的 Narrative tab 仍复用该组件；待后续拆出内容组件后可移除全局路由）。
3. `vendor/mediacrawler-local.patch` 等本地补丁资产按原样保留，未纳入本轮 Closure 范围。

---

# Optimization V2 Final Closure

Status: CLOSED
Baseline HEAD: 79e8842520d7c53ceacce2ee0a3a1ce4926938ca

Baseline note (FC0, 2026-08-30):
- HEAD = 79e8842 on main, matching the review baseline in optimization-v2-final-closure-execution-plan-corrected.md.
- Working tree clean except the untracked review plan document itself.
- backend/.pytest-*-tmp runtime artifacts remain untracked (ignored).
- Latest Alembic revision is 20260829_0048_report_documents; this final pass adds revision 20260830_0049 (no reuse of old revision ids).
- The earlier "# Optimization V2 CLOSED" section above is the V2 closure record as of 79e8842; per the final review it is superseded by this Final Closure section, which is now CLOSED (see the Final Closure Result below).

## Final Closure FC1 — Propagation Review Tri-state

- Commit: ab8c988
- Files: models.py (human_review_state on PropagationEdgeRecord), migrations/versions/20260830_0049_propagation_review_state.py (new), repositories.py (confirm writes tri-state + full audit details), schemas/propagation.py (Literal tri-state in DTO), frontend types/api.ts, PropagationGraph.vue, PropagationDetailPanel.vue + both test files, InvestigationNetworkView.test.ts fixture
- Migration: 20260830_0049 backfills conservatively — human_confirmed rows -> confirmed; false rows -> rejected ONLY when the evaluations audit (metric propagation_edge_human_confirmation) proves the latest decision was a rejection; anything else -> unreviewed. Column ends NOT NULL + indexed; legacy human_confirmed kept as a compatibility mirror. Downgrade drops the tri-state column only.
- Tests: backend pytest tests/test_propagation_graph.py tests/test_propagation_confirmation.py tests/test_migration_review_state.py -> 11 passed (new-edge default unreviewed, confirm/reject + both re-judgement directions with audit contents, graph API tri-state field, migration backfill across all six audit branches, NOT NULL + index, downgrade keeps rows + legacy column, fresh upgrade to 0049 and to head). Frontend npm run test -- PropagationGraph PropagationDetailPanel -> 17 passed (three-state line styles, three badge states, reload-from-backend, both re-judge directions); npm run typecheck clean.
- Migration gate: PG offline DDL for 0048->0049 and 0049->0048 generated and inspected (ADD COLUMN / CREATE INDEX / SET NOT NULL; DROP INDEX / DROP COLUMN); alembic heads = 20260830_0049. The SQLite full-chain upgrade is impossible because 0003 (pgvector) is PostgreSQL-only (pre-existing); the 0049 revision logic is covered by tests/test_migration_review_state.py driving the real upgrade()/downgrade() through Alembic Operations on the 0048 table shape.

## Final Closure FC2 — Finding Atomic Creation

- Commit: 953aed4
- Files: finding_service.py (create_manual validates kind/statement/relation whitelist/evidence existence + case scope BEFORE any persistence, then delegates to the atomic repository method), finding_repository.py (new create_with_links: one session, flush for id, optional source link + all evidence links, single commit, rollback on any failure)
- Artifact materializer untouched: invalid evidence refs still skip + warn (tolerant policy preserved).
- Tests: tests/test_findings.py extended with 6 cases (missing evidence / cross-case evidence / invalid relation -> ApplicationError code asserted AND zero row-count delta across findings + source links + evidence links; duplicate evidence link hitting the DB unique constraint -> whole-transaction rollback; repository-level create_with_links rollback on a DB error; happy path with source + 3 evidence links verifying persisted rows). pytest tests/test_findings.py tests/test_provenance.py tests/test_report_documents.py -> 32 passed.

## Final Closure FC3 — Unassigned Evidence

- Commit: 9222329
- Files: new frontend/src/components/evidence/UnassignedEvidenceList.vue (+test), InvestigationEvidenceView.vue (Claims/Unassigned scope switch inside the Claims workspace, default Claims, reset on case switch, 0-claims guidance), InvestigationEvidenceView.test.ts (+6 FC3 cases)
- Behaviour: unassigned evidence is browsable with stance/excerpt/source_type/platform/author (from metadata only), selectable into the existing EvidenceDetailPanel item mode, and enters the Copilot context as workspace=evidence / selected_type=evidence / selected_id. Claims-empty-but-unassigned-nonempty shows "暂无已归组主张；可切换到 Unassigned 查看未归属证据" instead of the misleading empty state; the global empty guide only shows when both are empty. No backend API added, no EvidenceSidebar revived.
- Tests: npm run test -- InvestigationEvidenceView UnassignedEvidenceList EvidenceDetailPanel -> 19 passed; typecheck + lint clean.

## Final Closure FC4 — Provenance / Test Integrity

- Commit: 21955c8
- Files: provenance_service.py (_reports_citing_finding resolves generic citation refs through the existing _resolve_generic_ref_type before matching — no third resolver), tests/test_provenance.py (+1 bidirectional generic citation case), tests/test_posts.py ("or True" removed; field whitelist asserted on all rows; stable-key lookup by native_id)
- Tests: pytest tests/test_provenance.py tests/test_posts.py -> 9 passed. Scan of all Final-Closure test files found no remaining "or True" / "assert True" patterns.

## Final Closure FC5 — Interaction E2E

- Commit: b6f1b63
- Files: frontend/e2e-interact.cjs (rewritten into three reported sections), backend/scripts/seed_final_closure_e2e.py (deterministic fixture producer via normal repositories; refuses non-SQLite DATABASE_URL; prints one JSON line of real IDs), PropagationGraph.vue (VITE_E2E-only expando hook emitting the same select event + a real fix: the graph watch now flushes post-DOM so the chart renders when data arrives asynchronously), finding_service.py (submitting a finding for review now idempotently creates its Review item so the UI path reaches the existing workbench), InvestigationReportView.vue (document picker), tests/test_findings.py (seed helper adapted), .gitignore (E2E run artifacts)
- Environment: real backend (DATABASE_URL -> disposable SQLite, DEMO_MODE=1) + real frontend (VITE_E2E=true vite dev).
- Smoke: 17/17 passed (7 API + 8 global h1 + 2 hygiene checks).
- Scenario A: 9 checks — shell title, claim text visible, evidence click -> detail (excerpt/platform/author), Copilot context chip, Unassigned scope -> unassigned evidence -> detail, and ui_context captured from the REAL POST /messages request (selected_type=evidence, selected_id=unassigned_evidence_id).
- Scenario B: 7 checks — candidate visible, UI 提交审核 -> under_review, plain status API verified -> 422, review item -> workbench UI claim + 接受, findings page shows verified.
- Scenario C: 4 checks — valid draft visible, publish -> published (API confirms), UI switches to the remaining invalid draft, publish rejected with 发布校验失败, API re-read still not published.
- Scenario D: 7 checks — canvas rendered, detail shows relation/confidence + 人工未复核（推断关系）, 驳回 -> 人工已驳回, reload -> still 人工已驳回 (database-backed), 确认 -> 人工已确认, reload -> still 人工已确认.
- Scenario E: 5 checks — first page exactly 50 (limit=50), 加载更多 -> 51, keyword filter -> 1 target post, platform zhihu -> 1, post click -> context chip live_data · social_post.
- Scenario F: 5 checks — detail 未处理, acknowledge -> 已确认, resolve -> 已解决 (visible after switching the resolved filter), API acknowledge on resolved -> 400 alert_status_transition_invalid, reload -> still resolved.
- Skipped inside A-F: 0. Harness section: 3 passed + 1 unrelated skip (Kill Switch success path needs policy_exception approval data; documented since C11).
- Console/PageError: 0 unexpected (the invalid publish 400 is the designed gate rejection; JS errors and any other 4xx/5xx fail the run).

## Final Regression (FC6)

- Backend full regression (FC6, baseline 79e8842): 93 test files under backend/tests executed 1:1 (union == full set asserted by the shard generator: 12 greedy-balanced xdist shards `-n 4 --dist loadfile` + test_api.py / test_reports.py run serially to avoid the known SSE/xdist deadlock). Full regression contains 845 unique tests. First pass: 844 passed / 1 failed / 0 skipped — the single failing test was `tests/test_expert_agents.py::test_verification_expert_persists_claims_and_evidence`, caused by an OS-level SQLite `database is locked` flake under concurrent xdist workers (same class as the previously fixed busy-timeout flake). Its containing file (11 tests) was re-run serially: 11 passed / 0 failed. Final unique-test status: 845 / 845 green. If raw executions including the re-run are counted: 856 total executions = 855 passed executions + 1 first-pass failed execution (across 94 batch runs). Log: backend/full_regression_final.log (untracked artifact).
- Migration gate: tests/test_migration_review_state.py drives the real 0049 upgrade()/downgrade() through Alembic Operations (backfill of all six audit branches, NOT NULL + index, downgrade round-trip, fresh upgrade to 0049 and to head) -> passed. PG offline DDL generated both ways for 0048<->0049 and inspected. `alembic heads` = 20260830_0049. Known environment limits (recorded, not V2 blockers): the SQLite full-chain upgrade is impossible because 0003 (pgvector) is PostgreSQL-only (pre-existing), and the repository's PG verifier script (verify_postgres_migrations.py) needs a disposable PostgreSQL database — the local PG user lacks CREATEDB, so the offline-DDL route (explicitly allowed by the plan) was used.
- Frontend gates: npm run typecheck (clean) / npm run lint (clean) / npm run test (17 files, 104 passed, 0 failed) / npm run build (success, 27.8s).
- Browser gate: e2e-smoke.cjs 15/15 passed; e2e-interact.cjs smoke 17/17, Closure A-F 37/37 with 0 skipped, harness 3/3 (+1 unrelated Kill-Switch skip), no unexpected console/pageerror.

# Optimization V2 Final Closure Result

Baseline: 79e8842520d7c53ceacce2ee0a3a1ce4926938ca
Final HEAD: the "docs: finalize optimization v2 final closure" commit (this one)

## FC1 Propagation Review Tri-state
- Commit: ab8c988
- Files: models.py, migrations/versions/20260830_0049_propagation_review_state.py, repositories.py, schemas/propagation.py, types/api.ts, PropagationGraph.vue/.test.ts, PropagationDetailPanel.vue/.test.ts, InvestigationNetworkView.test.ts, tests/test_propagation_graph.py, tests/test_propagation_confirmation.py, tests/test_migration_review_state.py
- Migration: 20260830_0049, conservative backfill (never guesses rejections), NOT NULL + index, legacy column kept
- Tests: backend 11 passed; frontend 17 passed; PG offline DDL both ways
- Result: unreviewed/confirmed/rejected distinguishable in DB, API, Graph and Detail; rejections survive reload; re-judging auditable

## FC2 Finding Atomic Creation
- Commit: 953aed4
- Files: finding_service.py, finding_repository.py, tests/test_findings.py
- Tests: findings+provenance+report_documents 32 passed
- Result: manual creation validates everything first, writes through one-session/one-commit create_with_links; every failure path leaves zero partial writes (verified by row-count deltas and a real DB-level rollback test)

## FC3 Unassigned Evidence
- Commit: 9222329
- Files: UnassignedEvidenceList.vue/.test.ts, InvestigationEvidenceView.vue/.test.ts
- Tests: 19 passed (frontend) + typecheck + lint
- Result: unassigned evidence browsable/selectable/enters Copilot context; 0-claims case no longer misleads

## FC4 Provenance / Test Integrity
- Commit: 21955c8
- Files: provenance_service.py, tests/test_provenance.py, tests/test_posts.py
- Tests: 9 passed
- Result: generic {ref: finding_id} citations resolve bidirectionally; no always-true assertions remain in closure test files

## FC5 Interaction E2E
- Commit: b6f1b63
- Files: e2e-interact.cjs, seed_final_closure_e2e.py, PropagationGraph.vue (E2E hook + post-flush render fix), finding_service.py (auto review item on submit), InvestigationReportView.vue (picker), tests/test_findings.py, .gitignore
- Smoke: 17/17 (e2e-interact smoke section) + 15/15 (e2e-smoke.cjs)
- Scenario A: 9/9 — evidence/unassigned selection drives detail panel and the real /messages request payload carries ui_context (workspace/selected_type/selected_id)
- Scenario B: 7/7 — UI submit-for-review -> Review workbench claim+approve -> findings show verified; plain API verified stays 422
- Scenario C: 4/4 — valid draft publishes (API confirms published); invalid-citation draft is rejected by the gate with a UI error and stays unpublished
- Scenario D: 7/7 — unreviewed -> rejected -> confirmed through real UI clicks; each state survives reload from the database
- Scenario E: 5/5 — 50-per-page + load-more(51), keyword filter, platform filter, post selection context chip
- Scenario F: 5/5 — acknowledge -> resolve via UI; resolved signal rejects acknowledge (400 alert_status_transition_invalid); persisted after reload
- Skipped inside A-F: 0
- Console/PageError: 0 unexpected

## Final Regression
- Backend: 845 unique tests, all green at Final Closure (see the FC6 regression note above: 844 first-pass passed + 1 SQLite-lock flake re-run serially; 845/845 final)
- Migration: 0049 upgrade ✓ / downgrade ✓ / PG offline DDL ✓ (SQLite full-chain limited by pre-existing PG-only 0003; PG verifier needs CREATEDB — recorded as environment limits, not blockers)
- Frontend typecheck: ✓
- Frontend lint: ✓
- Frontend test: 104 passed / 0 failed
- Frontend build: ✓
- Browser smoke: ✓ (15/15 + 17/17)
- Browser A-F: ✓ (37/37, 0 skipped)

# Optimization V2 CLOSED

Final Closure checklist (from the corrected execution plan):
- [x] FC1 Propagation 三态数据库/DTO/UI 完成
- [x] FC1 migration 0049 upgrade/downgrade 通过
- [x] FC2 Finding create 0 partial write
- [x] FC3 Unassigned Evidence 可浏览/可选择/可进入 Context
- [x] FC4 generic Finding citation reverse provenance 完整
- [x] FC4 Closure tests 无 or True 等虚假断言
- [x] FC5 Browser Scenario A–F 真实交互 0 failed / 0 skipped
- [x] Backend full regression 0 failed
- [x] Frontend typecheck 0 error
- [x] Frontend lint 0 error
- [x] Frontend test 0 failed
- [x] Frontend build success
- [x] e2e-smoke success
- [x] console/pageerror = 0
- [x] docs/optimization-v2-delivery.md 与实际结果一致

Future cleanup (B 类，不阻塞 Closure，与上一轮记录一致)：
1. E2E Kill Switch 成功路径依赖 Harness 运行时产生的 policy_exception 审批数据，CI 无法自包含造数（fail-closed 已在 E2E 验证，成功路径由 test_resilience 单测覆盖）。
2. `/narratives` 旧路由与 NarrativeTimelineView 保留（Timeline workspace 的 Narrative tab 仍复用）。
3. `vendor/mediacrawler-local.patch` 等本地补丁资产按原样保留。
4. 本地 PG 用户无 CREATEDB 权限，verify_postgres_migrations.py 全链演练需在具备建库权限的环境执行。

# Optimization V2 Post-Closure Correctness Patch

Status: CLOSED (result below)
Baseline HEAD: e4bd0796464b24e65fb2d9c3bf48b4e11152a051
Reason: Finding under_review 与 ReviewItem 创建/重新激活不是同一事务。

Final reviewer found one post-closure transaction consistency blocker.
The final CLOSED status is superseded until PC1–PC5 pass.

# Optimization V2 Post-Closure Correctness Patch Result

Status: CLOSED

The final reviewer blocker was resolved:
Finding submission to review now atomically updates the Finding and
creates/reopens its unique ReviewItem in one database transaction.

Browser Scenario B no longer creates a ReviewItem through an auxiliary API.
The complete Finding → Review Workbench → approval → verified path is now
validated through the production UI flow.

## PC1 — Finding → Review 原子提交事务
- `ApplicationRepository.submit_finding_for_review()`: 单 session 内锁定
  Finding → 读取唯一 ReviewItem → 按状态行为表创建/复用/重新激活 →
  Finding.status=under_review → 一次 commit；唯一约束作为并发兜底，
  IntegrityError 时回滚重读并以幂等成功返回；activity log 与主状态同事务。
- `FindingService.update_status()`: under_review 在普通 transition 校验之前
  分支进入原子方法；`_reviews`/`ReviewService` 依赖删除。
- 状态行为表（PC1.3）全部实现：candidate 首次创建 unreviewed；重复提交幂等；
  历史 under_review+no item 修复；verified/rejected 复审复用同一 item 并激活
  为 in_review；superseded 拒绝（finding_invalid_transition）。

## PC2B — Review Workbench 重开原子同步
- `ApplicationRepository.reopen_review_item_atomic()`: 单事务内 ReviewItem →
  in_review，且 object_type=finding 时同步 Finding → under_review；非 Finding
  item 行为保持不变。`ReviewService.reopen()` 改接该原子方法，状态机校验保留
  review domain validator 单一权威实现。
- `OBJECT_LABELS` 补 `finding: '调查结论'`（Review Workbench 卡片不再显示英文）。

## PC3 — Browser Scenario B 去 masking
- `e2e-interact.cjs` Scenario B 重写为 B1–B14：UI 提交审核（B3 等待真实
  data-status badge，不再被筛选下拉框的“审核中”文本误匹配）→ 只读 queue 断言
  exactly one ReviewItem（B5/B6）→ Workbench claim+approve（B7/B8）→ Finding
  verified（B9）→ Workbench 重开（B10/B11）→ Finding under_review（B12）→
  再次 approve（B13）→ Finding 再次 verified（B14）。
- 全程不调用 `POST /reviews/items`；ReviewItem 只由 Finding 提交生产路径
  （submit_finding_for_review）创建/重开。

## PC4 — 专项测试与最终回归
- Backend 专项测试（test_findings.py 新增 13 个）：首次提交原子成功、重复提交
  幂等、ReviewItem 写入失败整体回滚（0 partial write）、历史不一致自动修复、
  verified/rejected 复审复用 item、superseded 拒绝、并发提交单 item、
  Workbench 重开 accepted/rejected Finding 原子同步、重开失败 0 partial write、
  非 Finding item 重开回归、under_review→candidate 保留 item。
- 专项回归：test_findings.py 30/30、test_review.py 12/12、
  test_claim_review.py + test_provenance.py + test_report_documents.py +
  test_legacy_compatibility.py 21/21，全部 green。
- Frontend gates：typecheck ✓ / lint ✓ / test 148 passed 0 failed / build ✓。
- Browser gate：e2e-smoke.cjs 15/15；e2e-interact.cjs smoke 17/17、
  Closure A-F 44/44 with 0 skipped（Scenario B 现为 B1–B14 共 14 项）、
  harness 3 passed + 1 unrelated skip（Kill Switch 成功路径）、
  0 unexpected console/pageerror。
- Backend full regression（本补丁后，93 files 1:1, 13 batches）：
  **858 unique tests, 858 passed / 0 failed / 0 skipped**。
  注意：测试集合在 FC6 的 845 unique tests 基础上新增 13 个（本次专项测试），
  本次回归从收集的 858 tests 起 1:1 执行，无任何失败，无需重跑。

# Optimization V2 CLOSED（Post-Closure 最终）

Post-Closure 验收矩阵（来自 corrected execution plan）：
- [x] Finding under_review 与 ReviewItem 创建在同一事务
- [x] 事务失败不存在 partial write
- [x] verified/rejected re-review 重用既有 ReviewItem
- [x] historical under_review/no ReviewItem 可恢复
- [x] Review Workbench reopen Finding 原子同步 ReviewItem + Finding
- [x] Review Workbench reopen 失败不存在 partial write
- [x] Browser Scenario B 不再手动创建 ReviewItem
- [x] Browser Review 完整 UI 闭环通过（B1–B14）
- [x] Backend Review/Finding 回归通过
- [x] Backend full regression 858/858 green
- [x] Frontend 4 gates 全通过
- [x] Browser A-F 44/44 通过（0 skipped, 0 unexpected console/pageerror）
- [x] delivery 文档测试数字表述已纠正（FC6 计数与本次 858 数字分开记录）

Final reviewer blocker resolved; Optimization V2 is formally CLOSED.
