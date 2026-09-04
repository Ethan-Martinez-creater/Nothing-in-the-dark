# Nothing-in-the-dark V3 Intelligence Depth 执行计划

> 面向对象：负责直接修改仓库、补测试、运行验证并提交实现的执行智能体  
> 目标仓库：`Ethan-Martinez-creater/Nothing-in-the-dark`  
> V3 范围仅包含：
>
> 1. V2 能力闭环与质量评估
> 2. Cross-Investigation Intelligence
> 3. Actor / Entity Intelligence
> 4. Advanced Signals
>
> 明确不包含：多人协作、User / Organization / Tenant / RBAC、Public Distribution、Narrative Forecasting、新 Expert Agent。

---

# 1. V3 总目标

V2 已完成从 Chat-centric Multi-Agent Demo 到 Investigation-centric、Evidence-grounded、Agent-assisted Intelligence Workbench 的产品化。

V3 不再进行大规模 UI 重构，而是在不破坏 V2 的前提下，将系统继续深化为：

```text
Investigation Workbench
        ↓
Investigation Quality
        ↓
Workspace Entity Intelligence
        ↓
Cross-Investigation Intelligence
        ↓
Advanced Signals
```

V3 完成后，系统应能稳定回答：

```text
当前调查还缺什么？
当前调查是否达到较高完整度？
某个账号是否曾出现在其他 Investigation？
哪些 Investigation 之间共享账号、帖子、媒体或内容？
当前有哪些跨事件异常？
这些关联是确定观测还是候选推断？
这些 Signal 的依据是什么？
```

---

# 2. 强制架构边界

继续保持 V2 的核心分层：

```text
LLM / Agent
→ 分析、解释、调用 Tool

Deterministic Service
→ Quality 计算
→ Entity materialization
→ Cross-case relation
→ Signal detection
→ 状态规则

Repository
→ 持久化

Evidence
→ 事实引用底座

Human Review
→ verified / rejected 最终事实责任

UI
→ 工作流展示与交互
```

禁止：

```text
重写 AgentRuntime
重写 ToolRegistry
重写 SSE
重写 Approval
重写 Review 状态机
重写 Propagation 算法
重写 Alignment 算法
重写 Integrity 算法
引入第二套 Worker Queue
新增 Quality Agent / Entity Agent / Signal Agent / Cross-case Agent
```

---

# 3. 当前必须复用的已有能力

执行前先核对当前仓库实际路径，原则上应复用：

```text
backend/app/application/signal_service.py
backend/app/application/alignment_service.py
backend/app/application/integrity_service.py
backend/app/application/provenance_service.py
backend/app/application/workspace_service.py
backend/app/application/analysis_job_worker.py

backend/app/infrastructure/database/alignment_repository.py
backend/app/infrastructure/database/analysis_job_repository.py
```

当前实现已有：

```text
SignalService
→ Monitor Alert adapter

Alignment
→ Case 内 CanonicalEntity / EntityMention / ContentFamily

Integrity
→ account risk / coordination cluster

Provenance
→ Finding / Evidence / Artifact / Report 一跳关系

Workspace Overview
→ Home 聚合

AnalysisJob
→ persistent job / lease / retry / cancel / idempotency
```

V3 必须在这些能力上纵向扩展，不能重建并行系统。

---

# 4. V3 导航

Global Shell 增加一个一级入口：

```text
Home
Signals
Intelligence
Investigations
Reports
Administration
```

`Intelligence` 内只保留两个 Tab：

```text
Connections
Entities
```

不要拆成更多一级入口。

---

# 5. 数据库新增模型

执行前先确认当前 Alembic latest revision。

V3 新增以下表：

```text
investigation_quality

workspace_entities
workspace_entity_keys
workspace_entity_case_links

cross_investigation_links

derived_signals
```

不要修改现有 `CanonicalEntityRecord` 的 Case Scope。

---

# 6. InvestigationQualityRecord

字段：

```text
id
case_id

overall_score
grade

dimensions_json
metrics_json
gaps_json
warnings_json

input_fingerprint
algorithm_version

computed_at
created_at
updated_at
```

约束：

```text
UNIQUE(case_id)
FK case_id → cases.id
```

grade：

```text
strong
acceptable
needs_attention
weak
insufficient_data
```

映射：

```text
>=85       strong
70-84.999  acceptable
50-69.999  needs_attention
<50        weak
无足够数据  insufficient_data
```

只保存当前最新 Quality，不做历史 Quality Snapshot。

---

# 7. WorkspaceEntityRecord

字段：

```text
id
entity_type
canonical_name
aliases_json
attributes_json
status
version
first_seen_at
last_seen_at
created_by
created_at
updated_at
```

status：

```text
active
merged
```

---

# 8. WorkspaceEntityKeyRecord

字段：

```text
id
entity_id
key_type
key_value
confidence
method
created_at
```

约束：

```text
UNIQUE(key_type, key_value)
```

第一版仅允许稳定身份 Key：

```text
platform_account
```

格式：

```text
{platform}:{native_id}
```

辅助来源 Key：

```text
case_entity
```

格式：

```text
{case_id}:{canonical_entity_id}
```

严禁以昵称 / display name 作为跨 Case 自动合并依据。

---

# 9. WorkspaceEntityCaseLinkRecord

字段：

```text
id
entity_id
case_id
source_type
source_id
confidence
method
first_seen_at
last_seen_at
metadata_json
created_at
updated_at
```

source_type 第一版：

```text
account
canonical_entity
```

约束：

```text
UNIQUE(case_id, source_type, source_id)
```

---

# 10. CrossInvestigationLinkRecord

字段：

```text
id
left_case_id
right_case_id
relation_type
score
status
evidence_count
evidence_refs_json
feature_scores_json
fingerprint
algorithm_version
first_seen_at
last_seen_at
created_at
updated_at
```

relation_type 第一版固定：

```text
shared_actor
shared_post
shared_media
shared_content
```

status：

```text
observed
candidate
```

语义：

```text
observed
→ 基于确定身份、原始 Post、精确媒体、确定内容 hash

candidate
→ 基于近似媒体匹配等启发式结果
```

Case Pair 必须 canonical ordering：

```python
left_case_id, right_case_id = sorted((a, b))
```

---

# 11. DerivedSignalRecord

只保存非 Monitor Alert 来源的 Signal。

字段：

```text
id
source_type
source_id
case_id
signal_type
severity
status
title
why_it_matters
confidence
metric_snapshot_json
evidence_refs_json
related_case_ids_json
fingerprint
detector_version
occurrence_count
first_seen_at
last_seen_at
created_at
updated_at
```

status：

```text
open
acknowledged
resolved
suppressed
```

Monitor Alert 继续使用现有表和 MonitorRepository。

---

# Part A — V2 能力闭环与质量评估

# 12. 新增 InvestigationQualityService

新增：

```text
backend/app/application/investigation_quality_service.py
```

新增：

```text
backend/app/infrastructure/database/investigation_quality_repository.py
```

Repository 方法：

```text
get(case_id)
upsert(...)
list_needing_attention(limit)
count_by_grade()
```

---

# 13. Quality 固定 6 个维度

```text
collection_coverage
evidence_coverage
finding_support
review_resolution
provenance_integrity
report_citation
```

执行智能体不得自行增删。

---

# 14. Collection Coverage

Expected Platforms：

```text
优先：
Active Collection Definition.platforms

否则：
Case.platforms
```

Covered：

优先依据最近一次匹配当前 Active Definition/version 的 terminal CollectionRun。

平台：

```text
progress_json.platforms[platform].status == completed
```

则 covered。

历史 Case 无 CollectionRun 时允许 fallback：

```text
当前 SourcePost 中存在该 platform
```

Score：

```python
covered / expected * 100
```

若无 expected platform：

```text
None
```

输出：

```text
expected_platforms
covered_platforms
missing_platforms
collection_in_progress
latest_collection_run_id
```

正在运行的采集不得提前产生最终 missing-platform critical gap。

---

# 15. Evidence Coverage

固定指标：

```text
claims_total
claims_with_evidence
evidence_total
claims_without_evidence_count
```

Score：

```python
if claims_total > 0:
    claims_with_evidence / claims_total * 100
elif evidence_total > 0:
    100
else:
    None
```

未绑定 Evidence 的 Claim：

```text
warning
```

最多返回 20 个具体 claim_id。

---

# 16. Finding Support

指标：

```text
findings_total
findings_with_evidence
verified_findings
verified_findings_without_evidence
```

Score：

```python
findings_with_evidence / findings_total * 100
```

若：

```text
verified Finding
AND
0 Evidence link
```

必须产生：

```text
critical
code=verified_finding_without_evidence
```

---

# 17. Review Resolution

终态：

```text
verified
rejected
superseded
```

未终态：

```text
candidate
under_review
```

Score：

```python
terminal_findings / findings_total * 100
```

UI 名称必须是：

```text
Resolution
```

不能叫：

```text
Accuracy
```

---

# 18. Provenance Integrity

复用现有：

```text
ProvenanceService
ReportDocumentService citation parser
```

检查：

```text
Finding evidence refs
Finding source refs
Report citation refs
```

禁止写第三套 citation parser。

Score：

```python
if checked_refs > 0:
    100 * (checked_refs - dangling_refs) / checked_refs
else:
    None
```

verified Finding 或 published Report 出现 dangling ref：

```text
critical
```

其他 dangling：

```text
warning
```

---

# 19. Report Citation

给 `ReportDocumentService` 增加公共只读方法：

```python
async def validate_for_publish(
    case_id: str,
    report_id: str,
) -> dict:
    ...
```

返回：

```json
{
  "ok": true,
  "problems": []
}
```

必须复用当前 Publish Gate 的同一校验逻辑。

选择当前 Case：

```text
最新 published
否则最新 in_review
否则最新 draft
```

Score：

```text
无 report → None
0 problems → 100
1-2 → 70
3-5 → 40
>5 → 0
```

该分数仅表示 publish readiness。

---

# 20. Overall Quality Score

固定权重：

```text
collection_coverage   25
evidence_coverage     25
finding_support       20
review_resolution     10
provenance_integrity  10
report_citation       10
```

某维度为 None：

```text
从总权重分母移除
```

不能按 0 分处理。

---

# 21. Quality Gap 结构

```json
{
  "code": "missing_collection_platform",
  "severity": "warning",
  "object_type": "collection",
  "object_id": null,
  "message": "...",
  "action": {
    "type": "navigate",
    "target": "/investigations/{case_id}/overview"
  }
}
```

severity：

```text
critical
warning
info
```

---

# 22. Quality Fingerprint

避免每次 GET 都完整重算。

fingerprint 至少纳入：

```text
case.updated_at
active collection id/version
latest collection run id/status/updated_at
post count + latest timestamp
claim count + latest timestamp
evidence count + latest timestamp
finding count + latest updated_at
review decision count + latest timestamp
latest report id/status/version/updated_at
```

canonical JSON + SHA256。

若未变化：

```text
直接返回缓存 QualityRecord
```

---

# 23. Quality API

新增：

```http
GET /api/v1/cases/{case_id}/quality

POST /api/v1/cases/{case_id}/quality:refresh
```

GET 默认：

```text
fresh-if-needed
```

---

# 24. Quality UI

修改：

```text
InvestigationOverviewView.vue
```

增加：

```text
Investigation Quality
```

必须展示：

```text
Overall grade
Overall score
6 dimensions
Top gaps
```

文案明确：

```text
Quality Score 表示调查完整度与准备度，
不代表事件结论真实性。
```

---

# 25. Home Quality

扩展：

```text
WorkspaceOverviewService
WorkspaceOverviewResponse
HomeView.vue
```

增加：

```text
investigations_needing_attention
```

定义：

```text
needs_attention
weak
```

最多显示 5 个。

---

# Part B — Actor / Entity Intelligence

# 26. WorkspaceEntityRepository

新增：

```text
backend/app/infrastructure/database/workspace_entity_repository.py
```

方法：

```text
get
list
count
find_by_key
create_entity
add_key
link_case_object
list_case_links
list_entities_for_case
merge_entities
delete_orphans
```

---

# 27. WorkspaceEntityService

新增：

```text
backend/app/application/workspace_entity_service.py
```

依赖：

```text
WorkspaceEntityRepository
AlignmentRepository
ApplicationRepository
SocialRepository
IntegrityRepository
```

核心方法：

```python
refresh_case(case_id)
get_profile(entity_id)
```

---

# 28. refresh_case 固定流程

```text
1. 读取 Case Accounts
2. 读取 confirmed Case Canonical account entities
3. 生成 platform_account deterministic keys
4. 匹配或创建 WorkspaceEntity
5. 建 WorkspaceEntityCaseLink
6. 更新 first_seen / last_seen
7. 处理 confirmed canonical entity 导致的 merge
8. 返回统计
```

---

# 29. 自动身份规则

若：

```text
platform + native_id
```

一致：

```text
自动视为同 Workspace Entity
```

若只有：

```text
author_name
```

禁止跨 Case merge。

---

# 30. Confirmed Case Entity

当前 Case-level Alignment 已 confirmed 的：

```text
same_as account
```

可以作为 Workspace Entity 合并依据。

如果其多个 account mention 已分别映射到不同 Workspace Entity：

```text
merge Workspace Entity
```

这属于已确认 identity evidence，不是昵称推断。

---

# 31. Merge 规则

迁移：

```text
WorkspaceEntityKey
WorkspaceEntityCaseLink
```

到 target。

source：

```text
status=merged
```

禁止 hard delete。

---

# 32. Entity Profile

返回：

```text
entity id/type/name/aliases

investigation_count
investigations

platform identities

post_count
comment_count

first_seen_at
last_seen_at

engagement_total

risk assessments

coordination cluster memberships

related cross-investigation links
```

Risk 必须复用当前 Integrity 结果，不重新计算。

---

# 33. Entity API

```http
GET /api/v1/intelligence/entities

GET /api/v1/intelligence/entities/{entity_id}

GET /api/v1/cases/{case_id}/entities
```

Filters：

```text
entity_type
query
min_investigations
platform
limit
offset
```

默认列表不返回全部 Post。

Detail recent posts 最大：

```text
20
```

---

# Part C — Cross-Investigation Intelligence

# 34. CrossInvestigationRepository

新增：

```text
backend/app/infrastructure/database/cross_investigation_repository.py
```

方法：

```text
upsert_link
list_for_case
list_between
list_workspace
delete_for_case_relation_type
count_for_case
related_case_ids
```

---

# 35. CrossInvestigationService

新增：

```text
backend/app/application/cross_investigation_service.py
```

依赖：

```text
CrossInvestigationRepository
WorkspaceEntityRepository
SocialRepository
MediaPipelineRepository
ApplicationRepository
```

核心：

```python
refresh_case(case_id)
```

---

# 36. shared_actor

如果一个 WorkspaceEntity 同时有：

```text
Case A link
Case B link
```

生成：

```text
relation_type=shared_actor
status=observed
score=1.0
```

同 Case Pair 多个 Actor：

```text
合并为一条 shared_actor link
evidence_count=N
```

最多保留 50 个 evidence refs。

---

# 37. shared_post

如果：

```text
同 platform
同 native_id
不同 case_id
```

则：

```text
shared_post
observed
score=1.0
```

语义只是：

```text
两个 Investigation 采到了同一原始 Post
```

---

# 38. shared_media

优先：

```text
MediaAsset.actual_sha256
```

相同：

```text
observed
score=1.0
```

若仅有 phash：

复用当前 Alignment 算法已有 similarity 和 threshold。

达到 possible threshold：

```text
candidate
```

不得新造 threshold。

---

# 39. shared_content

优先确认当前：

```text
SourcePost.content_hash
```

是否确实为稳定规范内容 hash。

若是：

```text
同 hash
→ observed
```

若不是：

复用当前：

```text
alignment.normalize_text
```

再 SHA256。

不要第三套 normalization。

---

# 40. 禁止 O(N²)

严禁：

```text
所有 Case 两两全文本 pairwise similarity
```

必须依赖：

```text
Workspace Entity Key
platform/native id
content hash
media SHA/phash blocking
```

---

# 41. Related Investigation 聚合

API 层组合：

```json
{
  "case_id": "...",
  "title": "...",
  "relation_types": [
    "shared_actor",
    "shared_media"
  ],
  "relation_count": 2,
  "max_score": 1.0,
  "shared_actor_count": 3,
  "shared_media_count": 1
}
```

无需 summary table。

---

# 42. Cross-case API

```http
GET /api/v1/cases/{case_id}/related-investigations

GET /api/v1/intelligence/connections

GET /api/v1/intelligence/connections/{left_case_id}/{right_case_id}
```

---

# 43. Investigation Overview

增加：

```text
Related Investigations
```

最多 5 个：

```text
Case title
relation types
shared actor count
shared media/content count
```

---

# Part D — Intelligence Global UI

# 44. IntelligenceView

新增：

```text
frontend/src/views/IntelligenceView.vue
```

Tab：

```text
Connections
Entities
```

---

# 45. Connections

布局：

```text
Filter bar

Left:
Connection list

Center:
Case-to-case graph

Right:
Connection detail
```

继续复用当前 ECharts。

Graph 节点只放：

```text
Investigation
```

Detail 才展示：

```text
shared actors
shared posts
shared media
shared content
```

---

# 46. observed / candidate

必须区分：

```text
observed → solid
candidate → dashed
```

Detail 显示：

```text
algorithm
score
evidence
```

candidate 文案：

```text
候选关联
```

---

# 47. Entities

列表：

```text
Entity Name
Type
Platforms
Investigations
Posts
Last Seen
Risk Summary
```

Detail：

```text
Identities
Investigation appearances
Recent posts
Integrity risk
Coordination memberships
Related investigations
```

---

# 48. Frontend API

新增：

```text
frontend/src/services/api/intelligence.ts
```

不要继续把所有方法塞入旧 `services/api.ts`。

---

# Part E — Advanced Signals

# 49. V3 Signal 架构

```text
SignalService
├── Monitor Alert Source
└── Derived Signal Source
```

统一输出：

```text
SignalResponse
```

---

# 50. DerivedSignalRepository

新增：

```text
backend/app/infrastructure/database/derived_signal_repository.py
```

方法：

```text
upsert_by_fingerprint
get
list
set_status
increment_occurrence
delete_stale_detector_version
```

---

# 51. AdvancedSignalDetectorService

新增：

```text
backend/app/application/advanced_signal_service.py
```

固定 4 种：

```text
coordination_cluster
actor_recurrence
media_reuse
cross_case_overlap
```

---

# 52. coordination_cluster

复用 Integrity cluster。

触发：

```text
cluster.size >= 3
AND
cluster.score >= 0.75
```

severity：

```text
score >= 0.90 AND size >= 5
→ critical

otherwise
→ warning
```

文案必须使用：

```text
疑似协调行为模式
```

禁止：

```text
确认水军
确认机器人
确认恶意操控
```

---

# 53. actor_recurrence

Workspace Entity 出现在：

```text
>=3 Investigation
```

触发。

```text
3-4 → warning
>=5 → critical
```

文案：

```text
该主体在多个 Investigation 中重复出现
```

不能写成幕后操控主体。

---

# 54. media_reuse

基于：

```text
shared_media
```

若一个媒体证据关联：

```text
>=2 Cases
```

产生 Signal。

```text
2-3 Cases → warning
>=4 Cases → critical
```

---

# 55. cross_case_overlap

对 Case Pair 聚合：

```python
actor = min(shared_actor_count / 3, 1.0)
media = min(shared_media_count / 2, 1.0)
content = min(shared_content_count / 5, 1.0)
post = min(shared_post_count / 5, 1.0)

score = (
    actor * 0.40
    + media * 0.30
    + content * 0.20
    + post * 0.10
)
```

触发：

```text
score >= 0.60
AND
至少两种 relation type
```

severity：

```text
0.60-0.84 → warning
>=0.85 → critical
```

---

# 56. SignalService 改造

现有 `SignalService.list_signals()`：

```text
Monitor Alert
+
Derived Signal
→ merge
→ sort
→ limit
```

排序：

```text
severity
detected_at DESC
```

---

# 57. change_status

```text
source_type == monitor_alert
→ MonitorRepository

otherwise
→ DerivedSignalRepository
```

现有：

```text
acknowledge
resolve
suppress
```

保持一致。

---

# 58. Signals API

扩展：

```text
source_type
signal_type
severity
status
case_id
```

---

# 59. Signals UI

修改：

```text
SignalsView.vue
```

subtitle：

```text
全局情报信号收件箱
```

新增 Source filter：

```text
All
Monitor
Coordination
Actor recurrence
Cross-investigation
```

Detail 增加：

```text
source type
confidence
related investigations
evidence refs
detector/source label
```

---

# Part F — Intelligence Refresh

# 60. 不新增 Worker

继续复用：

```text
AnalysisJobRecord
AnalysisJobRepository
AnalysisJobWorker
```

新增：

```text
job_type=intelligence_refresh
```

---

# 61. IntelligenceRefreshService

新增：

```text
backend/app/application/intelligence_refresh_service.py
```

固定顺序：

```python
quality = await quality_service.evaluate(case_id)

entities = await workspace_entity_service.refresh_case(case_id)

cross_case = await cross_investigation_service.refresh_case(case_id)

signals = await advanced_signal_service.refresh_case(case_id)
```

顺序不能调整。

---

# 62. AnalysisJobWorker

增加：

```python
if job_type == "intelligence_refresh":
    return await intelligence_service.refresh_case(case_id)
```

其余：

```text
lease
retry
cancel
heartbeat
```

全部保持原逻辑。

---

# 63. Refresh API

新增：

```http
POST /api/v1/cases/{case_id}/intelligence:refresh
```

内部只做：

```text
create AnalysisJob
return 202
```

不等待执行完成。

---

# 64. Refresh idempotency

key：

```text
intel:{case_id}:{YYYYMMDDHHmm}
```

一分钟内重复请求复用同一 job。

---

# 65. 自动触发

CollectionRun：

```text
completed
completed_with_errors
```

后 best-effort enqueue。

enqueue 失败：

```text
只 log warning
```

不能影响 CollectionRun 已完成状态。

Alignment / Integrity job 完成后：

```text
enqueue intelligence_refresh
```

但：

```text
intelligence_refresh
```

不能递归 enqueue 自己。

---

# Part G — Agent / Tool

# 66. 新增 5 个只读 Tool

新增：

```text
backend/app/harness/intelligence_tools.py
```

Tool：

```text
get_investigation_quality
query_related_investigations
query_workspace_entities
get_workspace_entity
query_signals
```

---

# 67. Tool 统一权限

```text
permissions=("read_database",)
side_effect="none"
requires_approval=False
cache_ttl_seconds=0
```

必须走当前 ToolRegistry。

---

# 68. Coordinator Prompt

增加明确路由：

```text
当前调查完整度 / 缺口
→ get_investigation_quality

这个事件与哪些历史 Investigation 有关联
→ query_related_investigations

某账号/主体是否在其它事件出现
→ query_workspace_entities / get_workspace_entity

当前异常 / coordination / recurrence / cross-case anomaly
→ query_signals
```

明确：

```text
candidate relation 和 risk Signal 是 intelligence indicator，
不得自动当作 verified fact。
```

---

# 69. Expert Allowlist

Propagation：

```text
query_related_investigations
query_workspace_entities
get_workspace_entity
```

Verification：

```text
query_related_investigations
get_workspace_entity
```

Evidence Critic：

```text
get_investigation_quality
```

Report：

```text
get_investigation_quality
```

Opinion / Citation Validator：

```text
本轮不增加 V3 Tool
```

---

# Part H — 测试

# 70. Backend 新测试

至少新增：

```text
test_investigation_quality.py
test_workspace_entities.py
test_cross_investigation.py
test_advanced_signals.py
test_intelligence_refresh.py
test_intelligence_tools.py
test_intelligence_api.py
```

---

# 71. Quality 核心测试

```text
Q01 empty Case → insufficient_data

Q02 collection 5/5 → 100

Q03 running collection 不提前报 missing critical

Q04 terminal missing >=50% → critical

Q05 claim without evidence → warning

Q06 verified finding without evidence → critical

Q07 candidate finding 不算 resolved

Q08 dangling provenance 降分

Q09 published report dangling citation → critical

Q10 validate_for_publish 与 publish gate 共用 validator

Q11 unavailable dimension 不按 0 分

Q12 fingerprint unchanged → cached

Q13 Finding 变化 → fingerprint 变化
```

---

# 72. Entity 核心测试

```text
E01 same platform/native_id across cases → same WorkspaceEntity

E02 same name different native_id → not merged

E03 confirmed Case canonical entity → Workspace entities converge

E04 unconfirmed candidate → no merge

E05 case link unique

E06 merge migrates keys

E07 merge migrates case links

E08 source status=merged

E09 profile case_count correct

E10 Integrity risk reused
```

---

# 73. Cross-case 测试

```text
C01 shared actor observed

C02 shared post observed

C03 same media SHA observed

C04 phash possible → candidate

C05 same content hash observed

C06 candidate != observed

C07 pair ordering deterministic

C08 refresh idempotent

C09 fingerprint unique

C10 evidence_count aggregated

C11 case delete removes links

C12 no all-pairs semantic scan
```

---

# 74. Advanced Signal 测试

```text
S01 Monitor Alert unchanged

S02 Monitor acknowledge still writes MonitorRepository

S03 coordination below threshold → no signal

S04 coordination threshold → warning

S05 strong coordination → critical

S06 actor in 3 cases → warning

S07 actor in 5 cases → critical

S08 media reuse → signal

S09 overlap requires >=2 relation types

S10 overlap formula exact

S11 derived acknowledge only updates DerivedSignal

S12 fingerprint dedup

S13 no duplicate Monitor Alert

S14 wording 不过度事实化
```

---

# 75. Intelligence Refresh 测试

```text
IR01 job type supported

IR02 order:
quality → entity → cross_case → signals

IR03 lease

IR04 cancel

IR05 retry

IR06 no recursive enqueue

IR07 alignment may enqueue

IR08 integrity may enqueue

IR09 collection enqueue failure does not fail CollectionRun

IR10 repeated refresh idempotent
```

---

# 76. Agent Tool 测试

```text
AT01 read_database

AT02 no write_database

AT03 quality case injection

AT04 related query anchored current Case

AT05 candidate preserved

AT06 entity context scope

AT07 query_signals defaults current Case

AT08 Coordinator gets 5 tools

AT09 Expert allowlists exact

AT10 no new Expert Agent
```

---

# 77. Frontend Gate

必须：

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

---

# 78. Adjacent Regression

至少覆盖：

```text
Alignment
Integrity
Analysis Jobs
Signals
Workspace Overview
Findings
Review
Provenance
ReportDocument
CollectionRun
Agent DB Tools
Agent Runtime
Expert Agents
Tool System
Case deletion
```

默认不要求全量 Backend Regression。

若修改以下核心语义才升级为全量：

```text
Database session/engine
AgentRuntime
ToolRegistry core
Review state machine
Finding mutation
Monitor Alert transition
CollectionRun lifecycle
Alignment materialization semantics
Integrity thresholds
Report publish gate semantics
```

---

# Part I — E2E

# 79. E2E-A Quality

打开已有 Investigation Overview：

```text
Quality Card
6 dimensions
Top gaps
```

均正确显示。

---

# 80. E2E-B Same Actor

```text
Case A:
weibo:123

Case B:
weibo:123
```

Refresh 后：

```text
one WorkspaceEntity
case_count=2

A-B:
shared_actor observed
```

---

# 81. E2E-C No False Name Merge

```text
Case A:
name=张三
native_id=111

Case B:
name=张三
native_id=222
```

结果：

```text
2 WorkspaceEntities
```

---

# 82. E2E-D Media Reuse

相同 media SHA 出现在 A/B：

```text
shared_media observed
media_reuse signal
```

---

# 83. E2E-E Coordination

Integrity cluster：

```text
size=5
score=0.92
```

产生：

```text
coordination_cluster critical
```

文案只能表达“疑似协调行为”。

---

# 84. E2E-F Cross-case Overlap

A/B 至少存在：

```text
shared_actor
shared_media
```

且 composite score 达阈值：

```text
cross_case_overlap Signal
```

---

# 85. E2E-G Copilot Cross-case

用户：

```text
这个事件和之前哪些调查有关？
```

Coordinator 必须：

```text
query_related_investigations
```

不得从 History 猜。

---

# 86. E2E-H Copilot Entity

用户：

```text
这个微博账号以前在哪些事件出现过？
```

Agent：

```text
query_workspace_entities
get_workspace_entity
```

返回实际数据库结果。

---

# Part J — 实施顺序

# 87. V3-0 Baseline

记录：

```text
HEAD
git status
latest migration
backend adjacent regression
frontend gates
```

创建交付文档：

```text
docs/v3-intelligence-depth-delivery.md
```

---

# 88. V3-1 Schema

先实现所有 V3 ORM + migration。

先通过 migration tests，再进入 Service。

---

# 89. V3-2 Quality

实现：

```text
Repository
Service
Report validate_for_publish
API
Tests
Overview Quality Card
Home needs-attention
```

---

# 90. V3-3 Workspace Entity

实现：

```text
Repository
Service
identity keys
confirmed canonical merge
profile
API
tests
```

---

# 91. V3-4 Cross Investigation

实现：

```text
Repository
Service
4 detectors
APIs
Related Investigation card
tests
```

---

# 92. V3-5 Intelligence UI

实现：

```text
Intelligence route
Connections
Entities
intelligence.ts
Sidebar
```

---

# 93. V3-6 Advanced Signals

实现：

```text
DerivedSignalRepository
AdvancedSignalDetectorService
4 signal detectors
SignalService union
Signals API
Signals UI
tests
```

---

# 94. V3-7 Durable Refresh

实现：

```text
IntelligenceRefreshService
AnalysisJob integration
refresh API
Collection completion enqueue
Alignment/Integrity enqueue
tests
```

---

# 95. V3-8 Agent Integration

实现：

```text
5 read tools
Coordinator prompt
Expert allowlists
routing tests
```

---

# 96. V3-9 E2E

必须至少使用：

```text
2 个 Investigation
```

完成全部 Cross-case E2E。

---

# 97. V3-10 Historical Bootstrap

新增脚本：

```text
refresh_v3_intelligence --all
```

只 enqueue 历史 Case。

禁止在 Alembic migration 中执行 Intelligence 算法。

---

# 98. V3-11 Delivery

更新：

```text
docs/v3-intelligence-depth-delivery.md
architecture docs
Signals docs
Alignment / Entity docs
```

明确：

```text
Case Canonical Entity
vs
Workspace Entity
```

---

# Part K — 推荐 Commit

```text
1. feat: add v3 intelligence persistence model
2. feat: add deterministic investigation quality assessment
3. feat: materialize workspace entity intelligence
4. feat: add cross-investigation intelligence links
5. feat: add global intelligence workspace
6. feat: add advanced derived signals
7. feat: orchestrate durable intelligence refresh
8. feat: expose v3 intelligence tools to agents
9. test: complete v3 intelligence e2e coverage
10. docs: finalize v3 intelligence depth delivery
```

---

# Part L — 最终 DoD

## Quality

```text
[ ] 6 dimensions
[ ] deterministic formula
[ ] fingerprint cache
[ ] critical gaps
[ ] Overview UI
[ ] Home attention
[ ] Quality != truth
```

## Entity

```text
[ ] Workspace Entity
[ ] stable platform/native key
[ ] same-name no merge
[ ] confirmed Case identity can merge
[ ] profile
[ ] risk reuse
[ ] coordination reuse
```

## Cross Investigation

```text
[ ] shared_actor
[ ] shared_post
[ ] shared_media
[ ] shared_content
[ ] observed/candidate
[ ] idempotent
[ ] no O(N²)
[ ] Connections UI
```

## Advanced Signals

```text
[ ] Monitor Alert compatibility
[ ] Derived Signal
[ ] coordination_cluster
[ ] actor_recurrence
[ ] media_reuse
[ ] cross_case_overlap
[ ] dedup
[ ] status actions
[ ] Signals UI
```

## Durable

```text
[ ] intelligence_refresh job
[ ] existing worker reused
[ ] lease/retry/cancel unchanged
[ ] collection enqueue
[ ] alignment/integrity enqueue
[ ] no recursion
```

## Agent

```text
[ ] 5 read tools
[ ] no new Expert Agent
[ ] read_database only
[ ] Prompt routing correct
[ ] candidate/risk never treated as verified fact
```

## Compatibility

```text
[ ] V2 Investigation routes unchanged
[ ] Network modes unchanged
[ ] Alignment semantics unchanged
[ ] Integrity semantics unchanged
[ ] Monitor Alert state machine unchanged
[ ] Review semantics unchanged
[ ] Report publish gate unchanged
[ ] CollectionRun lifecycle unchanged
```

## Tests

```text
[ ] V3 backend tests
[ ] adjacent regression
[ ] frontend tests
[ ] typecheck
[ ] lint
[ ] build
[ ] E2E
[ ] historical refresh validation
```

---

# 99. 最终完成状态

V3 完成后，Nothing-in-the-dark 应形成：

```text
Workspace Intelligence

Investigation A
    │
    ├─ Quality / Gaps
    │
    ├─ Workspace Actors ────────┐
    │                           │
    ├─ Shared Content ──────┐   │
    │                       │   │
    ▼                       ▼   ▼
Investigation B ◄── Cross-Investigation Links
    │
    ▼
Advanced Signals
    │
    ├─ Coordination
    ├─ Actor recurrence
    ├─ Media reuse
    └─ Cross-case overlap
```

最终事实边界必须保持：

> Intelligence indicator 用于发现值得调查的关系；Evidence 与 Human Review 才决定最终事实结论。
