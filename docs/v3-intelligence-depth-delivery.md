# V3 Intelligence Depth Delivery

> 执行依据：`docs/Nothing-in-the-dark_V3_Intelligence_Depth_Execution_Plan_Reviewed_Final.md`（审阅修订版，唯一权威规范）
> 本文档随实施进度持续更新。

## 0. V3 Approval Rework（2026-09-05）

审批基线 `04869a9d1c5b0d1ae40f4edb614d3ed83a3a5402`（V3-1~V3-12 全部推送后）收到
**Needs Changes / 暂不批准 V3 Done**，共 10 项（R1-R10）。返工唯一权威规范为
`docs/Nothing-in-the-dark_V3_Approval_Rework_Plan.md`；本章节逐项记录修复实施。

Rework Baseline：

| 项 | 值 |
|---|---|
| Rework Baseline HEAD | `04869a9d1c5b0d1ae40f4edb614d3ed83a3a5402` |
| git status | clean（返工计划文档为 untracked） |
| 边界遵守 | 未新增 Agent/Signal 类型/relation type；未改 AgentRuntime core / ToolRegistry core / Review / Finding / Monitor Alert / CollectionRun lifecycle / Alignment / Integrity threshold / Report publish acceptance |

### R1 (P0) — Production Refresh 补齐 advanced_signal_refresh

- **Files changed**：`analysis_job_worker.py`（`advanced_signal_service` 构造参数 + `intelligence_refresh` 成功后 best-effort `enqueue_advanced_signal_refresh` + `_run` 新增 `advanced_signal_refresh` 分支）、`intelligence_refresh_service.py`（新 `enqueue_advanced_signal_refresh(job_id, case_id)`，idempotency key `v3:advanced:{intelligence_job_id}:{ADVANCED_SIGNAL_VERSION}`，不用分钟 key）、`advanced_signal_service.py`（新 `refresh_global()` 聚合三类 global detector）、`bootstrap.py`（worker 注入 `_advanced_signals`）
- **Implementation**：固定链路 alignment/integrity → intelligence_refresh（quality→entities→cross→coordination）→ best-effort enqueue advanced_signal_refresh → actor_recurrence/media_reuse/cross_case_overlap 三 detector。advanced job 成功后绝不 enqueue 自己或 intelligence_refresh（worker 分支只对 alignment/intelligence 触发 follow-up）
- **Tests**：IR16（follow-up enqueue）、IR17（worker 分支调 refresh_global）、IR18（advanced job 不递归）、IR19（key 用 intelligence_job_id；64 字符截断后仍幂等稳定）、IR20（真实 AdvancedSignalDetectorService 全链 + 跨 case 同 SHA 媒体产出 media_reuse）、E2E-N（真实 AnalysisJobWorker 两次 tick 消费全链）
- **Result**：通过（见本章节测试结果）
- **Remaining limitation**：advanced_signal_refresh 失败不自动重试（与其它 AnalysisJob 一致，依赖 worker 下一轮 follow-up 链）；idempotency key 超 64 字符被仓库截断，但截断保留完整 job_id 前缀，IR19 验证幂等仍稳定

### R2 (P0) — cross_case_overlap 改用真实 Cross Link contract

- **Files changed**：`advanced_signal_service.py`（`_detect_cross_case_overlap` 重写；删除 `_evidence_type`/`_evidence_counts`）
- **Implementation**：不再从 `evidence_refs_json[*].type` 推断；只统计 `is_active AND status="observed"` 的 link，直接累计 `link.evidence_count`（shared_actor/3×0.40 + shared_media/2×0.30 + shared_content/5×0.20 + shared_post/5×0.10）；触发条件 score≥0.60 AND ≥2 observed active relation types；candidate 不贡献
- **Tests**：S14/S15/S16 修正为真实 relation_type + evidence_count 契约；CS01（真实 Service/Repo 链路 score=0.70 warning）、CS02（candidate media 不进 overlap）
- **Result**：通过
- **Remaining limitation**：`list_workspace(limit=200)` 上限不变（与 §55 原约束一致），超过 200 条 link 的 workspace 只对最新 updated_at 的 200 条对账

### R3 (P0) — shared_actor 使用完整 Identity Component

- **Files changed**：`cross_investigation_service.py`（`_detect_shared_actor` 重写）
- **Implementation**：anchor entities → 逐个 `WorkspaceEntityService.identity_component(entity.id)` → 按 `component_key` 去重 → 汇总全部 entity_ids → 一次批量 `list_case_links_for_entities(all_ids)` → 按 component 聚合 cases。500 节点硬保护由 entity service 的 `identity_component` 保证（cross service 不绕过）
- **Tests**：C01/C12（既有真实链路回归）、CS06（跨平台 X/Y 经 Case C canonical mentions 传播 → A-B shared_actor observed；retract 后 inactive）
- **Result**：通过
- **Remaining limitation**：`identity_component` 为逐 entity 调用（N 次 BFS）；anchor case 实体数受限（account 维度），与计划伪代码一致

### R4 (P0) — Global Signal stale reconciliation 改为全局对账

- **Files changed**：`derived_signal_repository.py`（新 `reconcile_detector_global`，范围 = signal_type + detector_version + detector_active=true，不按 case 过滤）、`advanced_signal_service.py`（拆 `_flush_detector` case-scoped / `_flush_global_detector` global；coordination→case，其余三 detector→global）
- **Implementation**：不在 expected set 的 active signal → detector_active=false，生命周期 open/acknowledged→resolved、suppressed 保持（§11.2 不变）。消除"主体完全消失后旧 Signal 不落 scope"盲区
- **Tests**：CS04（actor signal 主体消失 → inactive/resolved）、CS05（media signal 资产删除 → inactive/resolved）、E2E-I 既有生命周期回归
- **Result**：通过
- **Remaining limitation**：coordination_cluster 保持 case-scoped（计划明确）

### R5 (P1) — shared_media observed 优先于 candidate

- **Files changed**：`cross_investigation_service.py`（`_detect_shared_media` 重写为按 Pair 聚合）
- **Implementation**：同 Case Pair 输出唯一 payload：有 exact SHA → observed score=1.0（evidence_count 只统计 exact distinct match，按 (SHA, other_asset_id) 去重），phash candidate 仅作为 feature_scores 辅助信息，不降级；只有 phash → candidate score=max similarity，evidence_count=distinct candidate match 数
- **Tests**：CS03（同 Pair exact SHA + phash candidate → 单条 observed score=1.0）、C13 既有回归
- **Result**：通过
- **Remaining limitation**：candidate 辅助信息记录在 feature_scores（`phash_candidate`），不追加 evidence_refs（避免挤占 observed evidence 名额）

### R6 (P1) — Signals Source Filter 参数语义修复

- **Files changed**：`frontend/src/services/api/signals.ts`（新 `sourceFilterParams(filter)` 统一映射 helper + `SignalSourceFilter` 类型）、`frontend/src/views/SignalsView.vue`（load() 改用 helper，SOURCE_OPTIONS 类型化）
- **Implementation**：All→(undefined,undefined)；Monitor→(monitor_alert,undefined)；Coordination/Actor recurrence/Media reuse/Cross-case overlap→(derived, signal_type)。后端沿用现有 source_type+signal_type 参数，未新增 filter API
- **Tests**：SignalsView.test.ts 修正错误预期（原断言 actor_recurrence→source_type=actor_recurrence）+ F-S01~F-S03 + E2E-P 映射用例（cross_case_overlap）
- **Result**：通过
- **Remaining limitation**：浏览器实机 filter 切换依赖 VITE_E2E 环境（见 E2E-P 说明）

### R7 (P1) — Derived Signal Evidence 透传并展示

- **Files changed**：`backend/app/application/signal_service.py`（`_to_derived_signal` 的 evidence_refs 改为 `{"items": list(record.evidence_refs_json)[:50]}`）、`frontend/src/views/SignalsView.vue`（detail 新增 Evidence section）
- **Implementation**：前端读 `selected.evidence_refs.items`，优先展示 account_id/entity_id/sha256/relation_type/component_key/cluster_id 业务键，未知 dict 键以只读 compact key/value 兜底（不静默丢弃），嵌套值 JSON.stringify
- **Tests**：S23（items 非空且含原文键值）、E2E-O（真实 detector 产出 media_reuse → get_signal items 含 sha256）、F-S04（Evidence section 渲染）、F-S05（unknown dict 可见）
- **Result**：通过
- **Remaining limitation**：Monitor Alert 的 evidence_refs 维持原样（不经过 derived 路径）；items 截断 50 条与 MAX_LINK_EVIDENCE_REFS 一致

### R8 (P1) — Report Provenance checked_refs 分母修复

- **Files changed**：`report_document_service.py`（新公共只读 `inspect_citation_links(case_id, citation_links) -> {"checked_refs", "problems"}`；`_validate_citations` 改为其薄封装，单一 parser 实现）、`investigation_quality_service.py`（provenance 维度改用 inspection）
- **Implementation**：复用 `normalize_citation_refs` + `_citation_ref_problem`，未新写 parser。分母 = 全部 refs（valid+invalid+unknown shape），分子 = problems；unknown citation shape：checked_refs+=1 且 problems+=1（fail-closed）。修复前 dangling 被同时计入分子分母（9 valid + 1 invalid → 0%）
- **Tests**：Q20（10 refs / 9 valid / 1 invalid → checked_refs=10、dangling=1、score=90，并直接断言 inspection 返回）、Q09-Q11 既有回归
- **Result**：通过
- **Remaining limitation**：inspect 为逐 ref 查询（与原 _validate_citations 相同复杂度），未做批量 IN 优化

### R9 (P1) — Related Investigation shared_* count 使用 evidence_count

- **Files changed**：`cross_investigation_service.py`（`related_investigations` counts 改为 sum(link.evidence_count)）
- **Implementation**：每 Pair+relation_type 只有一条聚合 Link，共享对象数量 = evidence_count 之和；relation_count 仍为 distinct relation type 数。`query_related_investigations` Tool 复用同一 DTO（intelligence_tools 无独立计算）
- **Tests**：C17（3 actors + 2 media → shared_actor_count=3、shared_media_count=2、relation_count=2）
- **Result**：通过
- **Remaining limitation**：无

### R10 (P2) — unresolved_local_risk 收窄

- **Files changed**：`workspace_entity_service.py`（`_risk_for_component` 增加 entity_names/component_platforms 归属条件；`get_profile` 构造 component 名字与 platform 集合）
- **Implementation**：unresolved 只保留 subject_id 非 exact platform_account 且 platform 与 component 某 platform_account key 一致且 subject name 与 canonical_name/aliases strip+casefold 精确相等的 assessment；无法可靠归属直接忽略；禁止 fuzzy/embedding/LLM
- **Tests**：E13（既有：精确匹配仍进 unresolved）、E15（新增负例：platform 不一致 → 忽略；前缀 fuzzy → 忽略；strip+casefold 精确 → 保留）
- **Result**：通过
- **Remaining limitation**：多冒号 subject_id 取第一个冒号分段（platform:rest），与现有 name-only subject 生成约定一致

### Rework E2E 补充说明

- **E2E-J（Cross → Cross Link → overlap Signal）**：由 CS01/CS02 补齐——真实 CrossInvestigationService 由真实 workspace 数据产出 Cross Link，真实 AdvancedSignalDetectorService 消费并产出 overlap Signal，全程无 fake evidence contract。
- **E2E-N**：新增（test_v3_e2e.py），真实 AnalysisJobWorker 两次 tick 消费 intelligence_refresh → advanced_signal_refresh 全链。期间发现并修复一个真实链路 bug：quality `_response` 的 `computed_at` 为 datetime，导致 worker result_json 序列化失败、follow-up enqueue 被跳过（demo 环境掩盖）。现已 ISO 字符串化（API 层 pydantic 解析回 datetime，接口契约不变）。
- **E2E-O**：新增（test_v3_e2e.py），真实 detector 产出 media_reuse 后 `get_signal` 返回 `evidence_refs.items` 含 sha256；前端侧由 F-S04/F-S05 组件测试覆盖。
- **E2E-P（浏览器 Source filter）**：参数映射由组件测试（F-S01~F-S03 + cross_case_overlap 映射用例）验证；`VITE_E2E=true` 的浏览器实机切换未在本环境执行，不声称"已实跑"。

### Rework 测试结果

- 新增/修正测试：IR16-IR20（test_v3_refresh.py，IR01-IR20 全绿）、S14-S16 契约修正 + S23-S25（test_advanced_signals.py 全绿）、C17（test_cross_investigation.py C01-C17 全绿）、CS01-CS06（新 test_v3_cross_signal_integration.py 全过，真实 Service/Repo 无 fake contract）、Q20（test_investigation_quality.py 全绿）、E15（test_workspace_entities.py E01-E15 全绿）、E2E-N/O（test_v3_e2e.py 10/10）、F-S01~F-S05 + 错误预期修正（SignalsView.test.ts 13 tests 全绿）
- **V3 targeted + Adjacent 后端回归**：20 个测试文件 **274 passed / 0 failed**（3362s；含 test_investigation_quality / workspace_entities / cross_investigation / advanced_signals / v3_refresh / intelligence_tools / intelligence_api / v3_case_deletion / v3_e2e / v3_cross_signal_integration + analysis_jobs / alignment / integrity / signals / monitoring / report_documents / report_publish_refs / collection_runs / agent_database_tools / expert_agents）
- **前端四项门**：typecheck ✓、lint ✓（--max-warnings=0）、test **202/202**（首跑 2 个 flaky 为后台回归并发负载，重跑全绿）、build ✓
- Full Backend Regression：未触发升级条件（未改 AgentRuntime/ToolRegistry core、CollectionRun terminal、Alignment materialize/retract、Integrity threshold、Monitor Alert transition、Report publish gate、Finding/Review 状态机、Database engine）——quality `_response` computed_at 序列化修复属于 payload 序列化层，API 契约不变（pydantic ISO→datetime）。



## 1. Baseline

| 项 | 值 |
|---|---|
| Baseline HEAD | `22711aca629f28805e6ce2b1577f7a6751f56caa`（fix: report publish gate rejects post/comment/aggregate citations） |
| Baseline 日期 | 2026-09-03 |
| git status | clean（除 V3 计划文档两份为 untracked） |
| Latest Alembic revision | `20260901_0050_collection_runs`（V3 migration 从 0051 起） |

## 2. V2 Closure Baseline（§4.2 / V3-0）

执行方式：`docs/Nothing-in-the-dark_V3_Intelligence_Depth_Execution_Plan_Reviewed_Final.md` §86 Mandatory Adjacent Regression 清单在基线 HEAD 上全量运行（22 个测试文件，pytest xdist `-n 4 --dist loadfile`）+ 前端四项门（typecheck / lint / test / build）。

结果：后端 22 个测试文件 263 passed（50 分钟）；前端四项门 155/155 全绿
（首跑 1 个 router flaky 超时，重跑全绿）——基线满足 §88「未触发 Full
Regression 升级条件」的判定前提。

## 3. 当前代码事实核查（执行前已确认）

| 事实 | 位置 |
|---|---|
| `SourcePostRecord.content_hash`（SHA256 raw content，indexed） | `models.py:484` |
| `MediaAssetRecord.actual_sha256`（nullable） | `models.py:685` |
| `alignment.normalize_text` / `POSSIBLE_THRESHOLD` / `content_alignment` | `backend/app/services/alignment.py:60/17` |
| Alignment 四段 phash blocking（offset 0/4/8/12） | `alignment_service.py` |
| `AlignmentService.materialize_candidate` / `retract_candidate` | `alignment_service.py:41/55` |
| Account mention：`platform_object_type="account"`, `platform_object_id=AccountRecord.id` | `alignment_service.py:94-99` |
| `AccountRecord` 全局唯一 `(platform, native_id)`；`case_id`=首次观察 case；`list_accounts(case_id=...)` = Case Accounts 语义 | `models.py:614+`、`repositories.py:2060` |
| `IntegrityService.analyze_case` 返回 `{"assessments": N, "clusters": N}`（需 additive 加 cluster_ids/window） | `integrity_service.py:139` |
| `AnalysisJobRepository.create_job(idempotency_key=...)`：**key 截断至 64 字符**，IntegrityError→复用 existing | `analysis_job_repository.py:25-59` |
| `AnalysisJobWorker.tick()`：`complete_job(...)` 成功后为 follow-up enqueue 插入点 | `analysis_job_worker.py:60-105` |
| `CollectionRunWorker.run_case`：`_mark_terminal(run_id, terminal, result)`（`finally` 前的最后一行业务逻辑）为 Collection terminal enqueue 插入点 | `collection_run_worker.py:171-173` |
| `SignalService`：Monitor adapter，`list_signals`/`get_signal`/`change_status`，`_to_signal(row)` 组装 `SignalResponse` | `signal_service.py` |
| `IntegrityRepository.list_clusters(case_id)` / `list_cluster_members` / `get_cluster` | `integrity_repository.py:185/202/194` |
| `CanonicalEntityRecord`：`case_id + entity_type + canonical_name` unique；status 默认 proposed | `models.py:1096+` |
| `EntityMentionRecord`：`(entity_id, platform_object_type, platform_object_id)` unique | `models.py:1117+` |
| API 注册模式：`api_router.include_router(xxx.router, prefix="/cases", tags=[...])` | `api/router.py` |
| GlobalSidebar 一级导航数组在 `NAV_ITEMS`（Home/信号/调查/报告 + admin 组） | `GlobalSidebar.vue:18-34` |
| 前端 API 模块化模式：`services/api/*.ts`（collectionRuns/collections/findings/reports/signals） | `frontend/src/services/api/` |
| pytest：bettafish python，basetemp 已固定，xdist 可用；test_api/test_reports 含 SSE 需串行（不在 adjacent 清单） | `pyproject.toml` |

## 4. 实施进度（按 §102-114 / Part J）

| 阶段 | 状态 | Commit |
|---|---|---|
| V3-0 Baseline / Closure | 已完成 | （V2 Closure Baseline：后端 22 文件 263 passed；前端四项门 155/155 全绿，含 1 次 flake 重跑） |
| V3-1 Schema（8 表 + content_hash 索引 + migration 0051） | 已完成 | 40fbaee |
| V3-2 Quality | 已完成 | 8b74ff1 |
| V3-3 Workspace Entity | 已完成 | d022acf |
| V3-4 Cross Investigation | 已完成 | 40e686d |
| V3-4.5 装配（bootstrap + router 8 路由） | 已完成 | 577bf63 |
| V3-5 Intelligence UI | 已完成 | 8d87547 |
| V3-6 Advanced Signals | 已完成 | 3768995 |
| V3-7 Durable Refresh | 已完成 | 93c4aaa |
| V3-8 Agent Integration | 已完成 | c933bc3 |
| V3-9 Case Delete / Cleanup | 已完成 | （并入 93c4aaa，D01-D10） |
| V3-10 Frontend / E2E | 已完成（E2E 数据面 8 场景全绿；浏览器块 V1-V4 已扩展；回归批 1/2 全绿；前端四项门通过） | 9 |
| V3-11 Historical Backfill | 已完成（脚本验证：backfill-v3 keys + pending jobs） | 9 |
| V3-12 Delivery | 已完成 | 10 |

## 5. 算法版本（§4.1 固定，不得改动）

```text
V3_INTELLIGENCE_VERSION   = "v3.1.0"
QUALITY_ALGORITHM_VERSION = "quality-1.0.0"
WORKSPACE_ENTITY_VERSION  = "workspace-entity-1.0.0"
CROSS_INTELLIGENCE_VERSION= "cross-intel-1.0.0"
ADVANCED_SIGNAL_VERSION   = "advanced-signal-1.0.0"

MAX_ENTITY_ALIASES = 20
MAX_LINK_EVIDENCE_REFS = 50
MAX_ENTITY_RECENT_POSTS = 20
MAX_RELATED_INVESTIGATIONS = 100
MAX_INTELLIGENCE_CONNECTIONS = 200
```

## 6. Files Changed

已提交（V3-1 ~ V3-4.5）：

- `backend/app/core/v3.py`（新）：V3 固定常量 + grade 阈值（409baee）
- `backend/app/infrastructure/database/models.py`：+8 Record 类 + source_posts (content_hash, case_id) 复合索引（409baee）
- `backend/migrations/versions/20260903_0051_v3_intelligence.py`（新，revision 20260903_0051）（409baee）
- `backend/tests/test_v3_migration.py`（新）：migration upgrade/downgrade/upgrade（409baee）
- `backend/app/schemas/quality.py`（新）+ `backend/app/api/routes/quality.py`（新）（8b74ff1）
- `backend/app/application/investigation_quality_service.py`（新）：6 维度加权 + fingerprint + gaps（8b74ff1）
- `backend/app/application/repositories.py`：+get_claim_evidence_quality_metrics / get_review_decision_quality_metrics / get_finding_link_integrity_metrics（8b74ff1）
- `backend/app/infrastructure/database/finding_repository.py`：+get_quality_metrics（8b74ff1）
- `backend/app/infrastructure/database/collection_run_repository.py`：+latest_for_definition / latest_terminal_for_definition / has_active_for_definition（8b74ff1）
- `backend/app/application/report_document_service.py`：+validate_for_publish / validate_citation_links（8b74ff1）
- `backend/tests/test_investigation_quality.py`（新）：Q01-Q19（8b74ff1 + V3-5 追加 Q19）
- `backend/app/schemas/workspace_entities.py`（新）+ `backend/app/api/routes/workspace_entities.py`（新）（d022acf）
- `backend/app/application/workspace_entity_service.py`（新）：refresh_case / identity_component / get_profile / list（d022acf）
- `backend/app/infrastructure/database/workspace_entity_repository.py`（新）（d022acf）
- `backend/app/infrastructure/database/alignment_repository.py`：+list_account_mentions_by_entity（d022acf）
- `backend/app/infrastructure/database/social_repository.py`：+latest_post_created_at / list_case_post_authors / cross-case 匹配查询（d022acf）
- `backend/tests/test_workspace_entities.py`（新）：E01-E14（d022acf）
- `backend/app/schemas/cross_investigation.py`（新）+ `backend/app/api/routes/cross_investigation.py`（新）（40e686d）
- `backend/app/application/cross_investigation_service.py`（新）：4 detectors + 对账 + related/connections（40e686d）
- `backend/app/infrastructure/database/cross_investigation_repository.py`（新）（40e686d）
- `backend/app/infrastructure/database/media_pipeline_repository.py`：+list_case_media_hashes / find_cross_case_*（40e686d）
- `backend/tests/test_cross_investigation.py`（新）：C01-C15（40e686d）
- `backend/app/bootstrap.py` + `backend/app/api/router.py`：V3 三服务装配 + 8 路由注册（577bf63）

V3-5（8d87547）：

- `backend/app/schemas/workspace.py`：+QualityAttentionCase + WorkspaceOverviewResponse 扩展（investigations_needing_attention / quality_unassessed_count）
- `backend/app/application/workspace_service.py`：Home 聚合只读持久化 Quality（V3 §44）
- `backend/app/bootstrap.py`：quality repository 上移注入 WorkspaceOverviewService
- `backend/tests/test_investigation_quality.py`：+Q19 home aggregate
- `frontend/src/services/api/intelligence.ts`（新）：qualityApi / crossApi / entityApi
- `frontend/src/components/intelligence/IntelligenceConnectionsGraph.vue`（新）：observed 实线 / candidate 虚线 ECharts graph
- `frontend/src/components/intelligence/InvestigationQualityCard.vue`（新）：6 维度 + grade + top gaps + disclaimer
- `frontend/src/components/intelligence/RelatedInvestigationsCard.vue`（新）：≤5 关联
- `frontend/src/views/IntelligenceView.vue`（新）：Connections / Entities 双 Tab
- `frontend/src/router/index.ts` + `frontend/src/components/shell/GlobalSidebar.vue`：/intelligence 一级入口
- `frontend/src/views/investigation/InvestigationOverviewView.vue`：+Quality Card + Related Card
- `frontend/src/views/HomeView.vue` + `frontend/src/services/api/signals.ts`：needs-attention + unassessed
- 新测试：IntelligenceView.test.ts / IntelligenceConnectionsGraph.test.ts / InvestigationQualityCard.test.ts / RelatedInvestigationsCard.test.ts / InvestigationOverviewView.test.ts / HomeView.test.ts / GlobalSidebar.test.ts 扩展 / App.test.ts 路由补充

V3-6（3768995）：

- `backend/app/application/integrity_service.py`：analyze_case additive（cluster_ids / window_start / window_end）
- `backend/app/infrastructure/database/derived_signal_repository.py`（新）：fingerprint upsert + §11.2 生命周期 + reconcile_detector_scope + case links JOIN
- `backend/app/application/advanced_signal_service.py`（新）：4 detectors（coordination_cluster / actor_recurrence / media_reuse / cross_case_overlap）+ 逐 detector refresh + reconcile
- `backend/app/application/signal_service.py`：Monitor + Derived 合流 + 确定性排序 + 双源歧义拒绝
- `backend/app/application/workspace_entity_service.py`：+list_components_with_cases（§53）
- `backend/app/infrastructure/database/analysis_job_repository.py`：+latest_succeeded
- `backend/app/infrastructure/database/media_pipeline_repository.py`：+list_sha_case_counts
- `backend/app/schemas/signals.py`：SignalResponse additive（related_case_ids / source_label / detector_version / detector_active）
- `backend/app/api/routes/signals.py`：+source_type / detector_active 过滤
- `frontend/src/services/api/signals.ts` + `frontend/src/views/SignalsView.vue`：V3 §59 UI（Source filter / 详情 / 条件已消失）
- `backend/tests/test_advanced_signals.py`（新）：S01-S22
- `frontend/src/views/SignalsView.test.ts`（新）：8 tests

V3-7 + V3-9（93c4aaa）：

- `backend/app/application/intelligence_refresh_service.py`（新）：refresh_case 固定顺序 + enqueue
- `backend/app/application/analysis_job_worker.py`：intelligence_refresh 分支 + follow-up enqueue（§62.1）
- `backend/app/application/collection_run_worker.py`：terminal 后 best-effort enqueue（§63）
- `backend/app/api/routes/cross_investigation.py`：POST /cases/{id}/intelligence:refresh（§64）
- `backend/app/scripts/refresh_v3_intelligence.py`（新）：backfill（§66）
- `backend/app/application/repositories.py`：delete_case V3 8 步清理（§67）+ list_cases_ordered_by_creation
- `backend/tests/test_v3_refresh.py`（新）：IR01-IR13
- `backend/tests/test_v3_case_deletion.py`（新）：D01-D10

V3-8（c933bc3）：

- `backend/app/harness/intelligence_tools.py`（新）：5 只读 Tool + IntelligenceToolReadService（§69-§72）
- `backend/app/harness/runtime.py`：_CASE_SCOPED_TOOLS +5（§70）
- `backend/app/harness/agents.py`：Coordinator prompt + allowlist（§73）+ Expert allowlists（§74）
- `backend/app/bootstrap.py`：IntelligenceToolReadService 装配 + register_intelligence_tools
- `backend/tests/test_intelligence_tools.py`（新）：AT01-AT12

V3-10/11（48c132f）：

- `backend/tests/test_v3_e2e.py`（新）：E2E-B/C/D/E/G/H/I/M 数据面（8 场景全绿）
- `frontend/e2e-interact.cjs`：+V1-V4 V3 浏览器场景块（Quality Card / /intelligence 双 Tab / Signals Source filter）

## 7. 测试结果

- V3-1：3 passed + 1 PG skip（migration）
- V3-2：Q01-Q18 全绿（19 tests）
- V3-3：E01-E14 全绿（14 tests）
- V3-4：C01-C15 全绿（14 tests，C16 移入 V3-9）
- V3-5：Q19 + test_signals 25 passed；前端全量 189/189 passed（含 38 个新测试）+ typecheck / lint / build 全绿
- V3-6：S01-S22 全绿；V3 套件 110 passed（含 signals/entities/cross/intelligence_api/monitoring/ui_context/v3_migration）；前端 197/197
- V3-7/9：IR01-IR13 + D01-D10 全绿；既有 case deletion/cascade 6 passed；V3 套件 79 passed
- V3-8：AT01-AT12 全绿；agent/expert 回归 46 passed（1 次 database-is-locked flaky 重跑绿）
- V3-10：test_v3_e2e 8/8 全绿（确定性无 LLM 链路）；backfill 脚本实跑验证（backfill-v3 keys + pending jobs）
- V3-11：`python -m app.scripts.refresh_v3_intelligence --all` 对临时库验证通过（1 case → alignment + integrity 两 job，幂等 key 正确）
- Adjacent Regression：批 1（alignment/integrity/analysis_jobs/findings/claim_review/review/provenance/report_documents/collection_runs/social_repository/media_features）144 passed；批 2（collection_definitions/migration/harness/tool_integration/media_pipeline/review_concurrency/durable_runtime/context_integration）57 passed + 1 skip

## 8. Known Limitations

- **E2E-K/L（Copilot 路由）**：query_related_investigations / query_workspace_entities /
  get_workspace_entity 的对话路由依赖真实 LLM + 浏览器环境，本交付在
  backend/tests/test_intelligence_tools.py（AT01-AT12）验证了工具注册与
  allowlist，浏览器交互场景（frontend/e2e-interact.cjs V1-V4）需在
  VITE_E2E=true 的完整环境中运行。
- **E2E-F（reversible cross-platform identity）**：relation retract 语义在
  E04-E06 单测覆盖；E2E 数据面未重复构造 Alignment materialization 全流程
  （需真实 alignment 决策数据），retract 后的 stale shared_actor 失效由
  C 系列测试（reconcile）保证。
- **phash candidate**：按 §54 只对 exact SHA 生成 media_reuse Signal，
  phash candidate 仅进入 Connections Intelligence（不进入高级告警流）。
- **account 维度全局唯一**：AccountRecord 只记首次观察，跨 Case 账号出现
  依赖 SourcePost 作者维度（list_case_post_authors），E2E-D 已按此语义验证。
- **backfill 脚本**：只 enqueue 不等待；worker 消费速度取决于环境。
  Script 在 ApplicationContainer.start() 下运行（会启动后台 worker）。
- **ruff 基线**：仓库存在既有 ruff 错误（UP017/F841 等，多为历史文件），
  本次交付仅保证新增/修改文件 ruff 干净。
