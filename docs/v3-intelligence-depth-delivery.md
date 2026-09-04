# V3 Intelligence Depth Delivery

> 执行依据：`docs/Nothing-in-the-dark_V3_Intelligence_Depth_Execution_Plan_Reviewed_Final.md`（审阅修订版，唯一权威规范）
> 本文档随实施进度持续更新。

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
