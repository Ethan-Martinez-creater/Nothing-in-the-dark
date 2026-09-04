# Nothing-in-the-dark V3 Intelligence Depth 最终执行计划（审阅修订版）

> 面向对象：负责直接修改仓库、补测试、运行验证并提交实现的执行智能体  
> 目标仓库：`Ethan-Martinez-creater/Nothing-in-the-dark`  
> 最终核验基线 HEAD：`22711aca629f28805e6ce2b1577f7a6751f56caa`  
> 本版替代此前生成的 V3 计划，执行智能体只以本文件为准。  
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

# 4.1 固定算法版本与执行常量

执行智能体不得自行重新选择版本号或阈值。本轮固定：

```text
V3_INTELLIGENCE_VERSION = "v3.1.0"

QUALITY_ALGORITHM_VERSION = "quality-1.0.0"

WORKSPACE_ENTITY_VERSION = "workspace-entity-1.0.0"

CROSS_INTELLIGENCE_VERSION = "cross-intel-1.0.0"

ADVANCED_SIGNAL_VERSION = "advanced-signal-1.0.0"
```

固定输出边界：

```text
MAX_ENTITY_ALIASES = 20

MAX_LINK_EVIDENCE_REFS = 50

MAX_ENTITY_RECENT_POSTS = 20

MAX_RELATED_INVESTIGATIONS = 100

MAX_INTELLIGENCE_CONNECTIONS = 200
```

这些版本字符串必须进入：

```text
QualityRecord.algorithm_version

CrossInvestigationLink.algorithm_version

DerivedSignal.detector_version

AnalysisJob idempotency key
```

算法发生不兼容修改时必须提升对应版本，不能静默覆盖旧语义。

---

# 4.2 V2 Closure Gate

V3 开始修改 Schema 前，必须先完成一次 V2 Closure Baseline。

至少验证：

```text
Async CollectionRun 可运行

Collection partial data 可查询

DB01–DB09 Agent 数据库 Tool 可使用

Evidence / Finding / Review / Report 主链正常

Report publish gate 当前测试通过

Alignment 正常

Integrity 正常

Signals Monitor Alert 正常

Provenance 正常

AnalysisJob lease/retry/cancel 正常

Investigation Overview / Live Data / Evidence / Network /
Timeline / Findings / Report / Activity 可访问
```

结果写入：

```text
docs/v3-intelligence-depth-delivery.md
```

中的：

```text
V2 Closure Baseline
```

如果发现会直接阻塞 V3 的 V2 P0 问题：

```text
先做最小修复
再开始 V3 Schema
```

不得把非阻塞的 V2 新需求扩入 V3。

---

# 5. 数据库新增模型

V3 新增以下 8 张表：

```text
investigation_quality

workspace_entities
workspace_entity_keys
workspace_entity_case_links
workspace_entity_relations

cross_investigation_links

derived_signals
derived_signal_case_links
```

并对现有：

```text
source_posts
```

新增一个跨 Case 内容匹配需要的复合索引：

```text
(content_hash, case_id)
```

执行 migration 前先检查当前最新 revision；使用：

```text
latest + 1
```

禁止制造 Alembic branch。

Migration 只创建 Schema / Index。

禁止在 Alembic 中：

```text
扫描历史 Case
运行 Alignment
运行 Integrity
创建 Workspace Entity
创建 Cross-case Link
运行 Signal Detector
```

历史数据回填由后面的 backfill 脚本完成。

---

# 6. InvestigationQualityRecord

字段固定：

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
PK id

FK case_id → cases.id

UNIQUE(case_id)

INDEX(grade)
INDEX(updated_at)
```

Case 删除：

```text
显式删除 InvestigationQualityRecord
```

并在 FK 能力允许时使用：

```text
ON DELETE CASCADE
```

作为第二层保护。

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
无可计算维度 insufficient_data
```

只保存当前最新 Quality。

V3 不保存历史 Quality Snapshot。

---

# 7. WorkspaceEntityRecord

V3 第一版只处理：

```text
entity_type = "account"
```

不要在本轮实现：

```text
person
organization
location
topic
generic NER entity
```

字段：

```text
id

entity_type
canonical_name
aliases_json
attributes_json

status

first_seen_at
last_seen_at

created_by
created_at
updated_at
```

status 第一版固定：

```text
active
```

Workspace Entity 是：

> **稳定平台账号 identity node。**

它不是“全局不可逆身份合并结果”。

canonical name 规则：

```text
优先最新非空 Account.name

否则：
{platform}:{native_id}
```

当 display name 改变：

```text
新名称 → canonical_name

旧 canonical_name → aliases_json
```

alias：

```text
去空
去重
最多 20
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

FK：

```text
entity_id → workspace_entities.id
```

约束：

```text
UNIQUE(key_type, key_value)

INDEX(entity_id)
INDEX(key_type, key_value)
```

第一版稳定 identity key：

```text
key_type = platform_account

key_value = {platform}:{native_id}
```

例如：

```text
weibo:12345678
bilibili:UID123
```

如果 Account 没有 native_id：

不得生成 `platform_account` key。

可生成 Case-local provenance key：

```text
key_type = case_account

key_value = {case_id}:{account_record_id}
```

`case_account`：

```text
只能用于追踪
不得用于跨 Case 自动相同主体判断
```

严禁：

```text
author_name
display_name
normalize_name(name)
```

成为全局唯一 identity key。

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
FK entity_id → workspace_entities.id

FK case_id → cases.id

UNIQUE(case_id, source_type, source_id)

INDEX(entity_id, case_id)
INDEX(case_id)
```

该表必须采用：

```text
reconciliation
```

而不是 append-only。

每次：

```text
WorkspaceEntityService.refresh_case(case_id)
```

必须：

```text
1. 计算本次 expected source links
2. upsert expected
3. 删除当前 case 中本次已不存在的 stale links
4. cleanup orphan entities
```

因此：

```text
Account 被删除
Case 数据被重建
Canonical materialization 被 retract
```

后不会永久保留错误 Case appearance。

---

# 9.1 WorkspaceEntityRelationRecord

新增：

```text
workspace_entity_relations
```

目的：

> 表达可撤销的跨平台 `same_as` identity evidence。

字段：

```text
id

left_entity_id
right_entity_id

relation_type
status

source_case_id
source_type
source_id

confidence
method

first_seen_at
last_seen_at

created_at
updated_at
```

固定：

```text
relation_type = same_as
```

status：

```text
active
retracted
```

pair canonical ordering：

```python
left_entity_id, right_entity_id = sorted(
    (left_entity_id, right_entity_id)
)
```

约束：

```text
FK left_entity_id → workspace_entities.id

FK right_entity_id → workspace_entities.id

FK source_case_id → cases.id

UNIQUE(
    source_case_id,
    left_entity_id,
    right_entity_id,
    relation_type
)

INDEX(left_entity_id, status)
INDEX(right_entity_id, status)
INDEX(source_case_id, status)
```

---

# 9.2 为什么禁止 Workspace Entity 自动 merge

当前 V2 Alignment 明确支持：

```text
materialize_candidate(...)
retract_candidate(...)
```

因此 Case-level confirmed relation：

```text
不是可以安全做不可逆全局 merge 的永久事实。
```

V3 禁止：

```text
confirmed Case Canonical Entity
→ merge WorkspaceEntity A/B
→ source entity status=merged
```

正确方案：

```text
confirmed Case materialization
→ WorkspaceEntityRelation(same_as, active)

materialization retract
→ relation retracted
```

这样 Identity Intelligence 可以随当前可审计证据恢复。

---

# 9.3 Identity Component

Workspace Actor 的“同一主体组”不新增持久表。

运行时基于：

```text
WorkspaceEntity
+
active same_as relations
```

计算 connected component。

component key：

```text
component 内 entity_id 字典序最小值
```

该 key：

```text
仅用于本次 deterministic aggregation
不是新数据库主键
```

Cross-Investigation `shared_actor` 和 `actor_recurrence`：

必须以：

```text
identity component
```

为单位，而不是仅以单个 WorkspaceEntity 节点为单位。

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
is_active

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

relation_type 固定：

```text
shared_actor
shared_post
shared_media
shared_content
```

status 表示证据性质：

```text
observed
candidate
```

`is_active` 表示：

```text
当前刷新后该关系是否仍成立
```

两者不得混用。

语义：

```text
observed
→ deterministic exact relation

candidate
→ heuristic similarity relation
```

pair：

```python
left_case_id, right_case_id = sorted((a, b))
```

fingerprint 固定：

```text
SHA256(
    left_case_id
    + right_case_id
    + relation_type
    + algorithm_version
)
```

即：

> 一个 Case Pair + relation_type + algorithm_version 只有一条聚合 Link。

不要把单个 evidence id 放入 fingerprint，否则会破坏“一 relation type 一条聚合 link”。

约束：

```text
UNIQUE(fingerprint)

INDEX(left_case_id, is_active)
INDEX(right_case_id, is_active)
INDEX(relation_type, status, is_active)
```

---

# 10.1 Cross Link reconciliation

每个 detector 刷新完成后必须使用：

```text
expected fingerprint set
```

执行：

```text
upsert current expected links

mark stale links:
is_active = false
```

范围：

```text
touch 当前 anchor case
AND
relation_type == 当前 detector
AND
algorithm_version == 当前版本
```

不得物理删除历史 Link。

API 默认：

```text
is_active=true
```

如果用户明确查看历史才允许返回 inactive。

---

# 11. DerivedSignalRecord

只保存非 Monitor Alert 来源。

字段：

```text
id

case_id

source_type
source_id

signal_type
severity

status
detector_active

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
status_updated_at

created_at
updated_at
```

`case_id`：

```text
当前 Signal 的 primary Case
```

Cross-case / actor recurrence 等多 Case Signal：

```text
primary Case = 相关 case_id 字典序最小值
```

其余 Case 通过：

```text
derived_signal_case_links
```

关联。

status：

```text
open
acknowledged
resolved
suppressed
```

`detector_active`：

```text
true
false
```

status 是用户/工作流状态。

detector_active 是：

```text
当前 detector 条件是否仍成立
```

两者必须分离。

---

# 11.1 DerivedSignalCaseLinkRecord

新增：

```text
derived_signal_case_links
```

字段：

```text
id
signal_id
case_id
created_at
```

约束：

```text
FK signal_id → derived_signals.id

FK case_id → cases.id

UNIQUE(signal_id, case_id)

INDEX(case_id)
```

所有：

```text
query_signals(case_id)
Signals API ?case_id=
```

对 Derived Signal 必须通过该表过滤。

不得依赖：

```text
related_case_ids_json contains
```

做跨方言 JSON 查询。

`related_case_ids_json` 只作为：

```text
bounded response snapshot
```

保留。

---

# 11.2 Derived Signal lifecycle

每个 detector refresh：

```text
1. 计算 expected signal fingerprints
2. upsert expected signals
3. reconcile 当前 detector scope 中未出现的旧 Signal
```

规则：

### 新建

```text
detector_active=true
status=open
occurrence_count=1
```

### 连续仍然成立

```text
true → true

更新：
last_seen_at
metric_snapshot
evidence

不增加 occurrence_count
不改变 acknowledged/resolved/suppressed
```

### 条件消失

```text
detector_active=true → false
```

如果 status：

```text
open
acknowledged
```

自动：

```text
status=resolved
```

如果：

```text
suppressed
```

保持 suppressed。

如果本来：

```text
resolved
```

保持 resolved。

### 条件消失后重新出现

```text
false → true
```

如果不是：

```text
suppressed
```

则：

```text
status=open
occurrence_count += 1
```

如果：

```text
suppressed
```

保持 suppressed，只更新 detector_active / evidence / last_seen。

这套规则必须写测试。

---

# 11.3 Derived Signal fingerprint

fingerprint 必须表示：

```text
一个持续可追踪的 detector subject
```

固定：

```text
coordination_cluster
→ SHA256(signal_type + cluster_id + detector_version)

actor_recurrence
→ SHA256(signal_type + identity_component_key + detector_version)

media_reuse
→ SHA256(signal_type + actual_sha256 + detector_version)

cross_case_overlap
→ SHA256(signal_type + left_case_id + right_case_id + detector_version)
```

禁止使用：

```text
refresh timestamp
job id
```

作为 fingerprint。

否则每次刷新都会生成重复 Signal。

---

# Part A — V2 能力闭环与质量评估

# 12. InvestigationQualityService

新增：

```text
backend/app/application/investigation_quality_service.py
```

依赖固定：

```text
ApplicationRepository
SocialRepository
CollectionRunRepository
FindingRepository
ProvenanceService
ReportDocumentService
InvestigationQualityRepository
CollectionDefinitionService
```

不要在 Service 中直接：

```text
select ORM
```

缺少聚合查询时：

```text
扩展对应 Repository
```

---

# 12.1 InvestigationQualityRepository

新增：

```text
backend/app/infrastructure/database/investigation_quality_repository.py
```

方法：

```text
get(case_id)

upsert(...)

list_needing_attention(limit)

count_by_grade()

count_unassessed(total_cases)
```

---

# 12.2 Quality 所需 Repository helper

为了避免 N+1，必须新增/复用以下批量读取能力。

## ApplicationRepository

新增：

```text
get_claim_evidence_quality_metrics(case_id)
```

返回：

```text
claims_total
claims_with_evidence
evidence_total
latest_claim_at
latest_evidence_at
```

Evidence 与 Claim 的关联直接使用当前：

```text
EvidenceRecord.claim_id
```

不得逐 Claim 调：

```text
list_evidence_by_claim
```

---

## FindingRepository

新增：

```text
get_quality_metrics(case_id)
```

至少返回：

```text
findings_total
findings_with_support
verified_findings
verified_findings_without_support

evidence_link_count
latest_evidence_link_at

source_link_count
latest_source_link_at
```

---

## CollectionRunRepository

新增：

```text
latest_terminal_for_definition(
    case_id,
    definition_id,
    definition_version
)
```

terminal：

```text
completed
completed_with_errors
failed
cancelled
```

Collection Coverage 只使用：

```text
completed
completed_with_errors
```

作为成功覆盖来源。

---

## ReportDocumentRepository / Service

必须能确定：

```text
latest published
否则 latest in_review
否则 latest draft
```

并取得：

```text
lock_version
updated_at
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

不得自行增删。

---

# 14. Collection Coverage

Expected Platforms：

```text
优先：
Active Collection Definition.platforms

否则：
Case.platforms
```

Covered Platform：

优先使用：

```text
latest terminal CollectionRun
matching exact definition id/version
```

平台状态：

```text
completed
```

才计入 covered。

如果最新 Run：

```text
completed_with_errors
```

仍按各 platform progress 独立判断。

如果当前存在：

```text
queued / running
```

matching CollectionRun：

```text
collection_in_progress=true
```

正在执行的平台：

不得提前成为最终 missing critical gap。

历史 Case 完全没有 CollectionRun 时：

允许 fallback：

```text
SourcePost 中存在 platform
```

Score：

```python
if expected_platforms:
    score = covered / len(expected_platforms) * 100
else:
    score = None
```

缺口：

```text
terminal 后 missing >= 50%
→ critical

terminal 后 0 < missing < 50%
→ warning

active run 尚未完成
→ info only
```

---

# 15. Evidence Coverage

指标：

```text
claims_total
claims_with_evidence
evidence_total
claims_without_evidence_count
```

Score：

```python
if claims_total > 0:
    score = claims_with_evidence / claims_total * 100
elif evidence_total > 0:
    score = 100
else:
    score = None
```

Claim 没任何 Evidence：

```text
warning
```

最多返回：

```text
20 个 claim_id
```

---

# 16. Finding Support

这里必须区分 Evidence relation。

当前 Finding Evidence Link 支持：

```text
supports
contradicts
context
```

“Finding Support”只统计：

```text
relation == supports
```

不能把：

```text
contradicts
context
```

算作“已被支持”。

指标：

```text
findings_total

findings_with_support

verified_findings

verified_findings_without_support
```

Score：

```python
if findings_total > 0:
    score = findings_with_support / findings_total * 100
else:
    score = None
```

任何：

```text
verified Finding
AND
0 supports link
```

必须：

```text
critical
code=verified_finding_without_supporting_evidence
```

即使它有：

```text
contradicts/context
```

也仍然属于 critical。

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
if findings_total > 0:
    score = terminal_findings / findings_total * 100
else:
    score = None
```

名称：

```text
Resolution
```

禁止叫 Accuracy。

---

# 18. Provenance Integrity

复用：

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

不得创建第三套 citation parser。

Score：

```python
if checked_refs > 0:
    score = 100 * (
        checked_refs - dangling_refs
    ) / checked_refs
else:
    score = None
```

dangling：

```text
verified Finding / published Report
→ critical

其它
→ warning
```

---

# 19. Report Citation / Publish Readiness

当前 Report publish gate 已经支持：

```text
Evidence
Finding
Artifact
Social Post
Social Comment
aggregate_social_data
```

V3 必须直接复用当前逻辑。

给：

```text
ReportDocumentService
```

增加公共只读方法：

```python
async def validate_for_publish(
    self,
    case_id: str,
    report_id: str,
) -> dict[str, object]:
    ...
```

实现要求：

```text
调用与 change_status(... published)
相同的底层 deterministic validator
```

禁止复制 `_validate_citations`。

返回：

```json
{
  "ok": false,
  "problems": [...]
}
```

只读校验：

```text
不能修改 report status
```

Report 选择：

```text
latest published
otherwise latest in_review
otherwise latest draft
```

Score：

```text
无 report → None
0 problems → 100
1–2 → 70
3–5 → 40
>5 → 0
```

这是：

```text
Publish Readiness
```

不是内容质量分。

---

# 20. Overall Quality Score

权重固定：

```text
collection_coverage   25
evidence_coverage     25
finding_support       20
review_resolution     10
provenance_integrity  10
report_citation       10
```

维度 None：

```text
从分母移除
```

公式：

```python
overall_score = (
    sum(score * weight for available)
    /
    sum(weight for available)
)
```

如果：

```text
没有任何 available dimension
```

则：

```text
overall_score = None
grade = insufficient_data
```

---

# 21. Quality Gap

统一：

```json
{
  "code": "...",
  "severity": "critical|warning|info",
  "object_type": "...",
  "object_id": "...",
  "message": "...",
  "action": {
    "type": "navigate",
    "target": "..."
  }
}
```

固定目标：

```text
Collection gap
→ /investigations/{case_id}/overview

Evidence gap
→ /investigations/{case_id}/evidence

Finding support / resolution
→ /investigations/{case_id}/findings

Report citation
→ /investigations/{case_id}/report
```

不要让执行智能体自行发明其它 route。

---

# 22. Quality Fingerprint

原计划仅使用：

```text
Finding count + Finding.updated_at
```

不够，因为：

```text
FindingEvidenceLink
FindingSourceLink
```

增删不会必然更新 Finding.updated_at。

最终 fingerprint 必须包含：

```text
case.updated_at

active Collection Definition:
id
version
updated_at

latest matching CollectionRun:
id
status
updated_at

posts:
count
latest persisted/created timestamp

claims:
count
latest timestamp

evidence:
count
latest timestamp

findings:
count
latest Finding.updated_at

FindingEvidenceLink:
count
latest created_at

FindingSourceLink:
count
latest created_at

ReviewDecision:
count
latest created_at

latest Report:
id
status
lock_version
updated_at
```

canonical JSON：

```text
sort_keys=true
stable separators
SHA256
```

如果 fingerprint 相同：

```text
直接返回 InvestigationQualityRecord
```

否则：

```text
recompute + upsert
```

---

# 23. Quality API

新增：

```http
GET /api/v1/cases/{case_id}/quality

POST /api/v1/cases/{case_id}/quality:refresh
```

GET：

```text
fresh-if-needed
```

POST：

```text
force recompute
```

两者均为：

```text
deterministic
无 LLM
```

---

# 24. Quality UI

修改：

```text
InvestigationOverviewView.vue
```

展示：

```text
Overall Grade
Overall Score

Collection Coverage
Evidence Coverage
Finding Support
Resolution
Provenance Integrity
Report Citation

Top Gaps
computed_at
```

必须显示：

```text
Quality Score 表示调查完整度与准备度，
不代表事实真实性。
```

---

# 25. Home Quality

扩展：

```text
WorkspaceOverviewService
WorkspaceOverviewResponse
HomeView.vue
```

新增：

```text
investigations_needing_attention
quality_unassessed_count
```

`investigations_needing_attention`：

```text
grade in:
needs_attention
weak
```

最多：

```text
5
```

Home 使用持久化 Quality：

```text
不为所有 Case 同步 recompute
```

因此 UI 必须显示：

```text
computed_at
```

并把无 QualityRecord 的 Case 计入：

```text
quality_unassessed_count
```

避免把“尚未评估”错误显示成“质量正常”。

---

# Part B — Actor / Entity Intelligence

# 26. WorkspaceEntityRepository

新增：

```text
backend/app/infrastructure/database/workspace_entity_repository.py
```

方法固定：

```text
get

list

count

find_by_key

create_with_key

upsert_case_link

reconcile_case_links

upsert_relation

reconcile_case_relations

list_case_links

list_entities_for_case

list_active_relations_for_entities

list_case_links_for_entities

delete_orphans
```

删除原计划中的：

```text
merge_entities
```

V3 不做不可逆 Workspace Entity merge。

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

方法：

```python
refresh_case(case_id)

get_profile(entity_id)

identity_component(entity_id)
```

---

# 28. Entity refresh_case 固定流程

```text
1. validate Case

2. load Case Accounts in batch

3. for each account with platform/native_id:
   resolve or create deterministic WorkspaceEntity

4. for account without native_id:
   create/reuse Case-local account entity

5. build expected WorkspaceEntityCaseLink set

6. load current Case-level CanonicalEntity + EntityMention

7. derive expected active same_as WorkspaceEntityRelation set

8. upsert expected links / relations

9. reconcile stale case links

10. retract stale same_as relations sourced by this Case

11. cleanup orphan entities

12. update first_seen / last_seen / canonical name / aliases

13. return counts
```

---

# 29. Deterministic Account Identity

有：

```text
platform
native_id
```

则：

```text
key_type=platform_account

key_value={platform}:{native_id}
```

唯一约束发生竞争时：

```text
捕获 unique conflict
reload existing entity
继续 link
```

不能创建重复实体。

只有名字：

```text
不得跨 Case 合并
```

---

# 30. Case Canonical Identity → Reversible Relation

当前 V2 `AlignmentService.retract_candidate()` 能撤销 materialization。

因此 Workspace refresh 不读取：

```text
candidate.decision == confirmed
```

直接做永久 merge。

正确来源是：

```text
当前仍存在的 Case-level CanonicalEntity
+
当前仍存在的 account EntityMention
```

对于同一个 canonical account entity 中的多个 account mention：

```text
映射到 WorkspaceEntity
```

然后创建 pairwise：

```text
WorkspaceEntityRelation(
    relation_type=same_as,
    status=active,
    source_case_id=case_id,
    source_type=canonical_entity,
    source_id=canonical_entity.id
)
```

如果 materialization 被 retract：

```text
相关 mention 消失
→ refresh expected set 不再包含该 relation
→ relation.status=retracted
```

---

# 31. Identity Component

`identity_component(entity_id)`：

```text
只遍历 status=active
AND relation_type=same_as
```

使用 BFS/DFS。

返回：

```text
entity_ids
component_key=min(entity_ids)
```

必须有：

```text
MAX 500 nodes hard guard
```

超过：

```text
raise/return bounded warning
```

避免坏数据导致无限图遍历。

---

# 32. Entity Profile

Profile 以：

```text
identity component
```

聚合。

返回：

```text
component_key

entity_ids

canonical display name

aliases

platform identities

investigation_count
investigations

post_count
comment_count

first_seen_at
last_seen_at

engagement_total

risk assessments

coordination cluster memberships

related investigations
```

---

# 32.1 Canonical display name

在 identity component 内：

```text
优先 last_seen_at 最新且 canonical_name 非空的 node
```

其它名字进入 aliases。

不要使用 LLM 重新命名。

---

# 32.2 Integrity Risk 映射

Integrity 当前 `subject_id` 使用：

```text
{platform}:{native_id or author_name}
```

Workspace 跨 Case Risk 聚合只自动提升：

```text
能精确匹配 platform_account key
```

的 assessment。

仅名字形成的：

```text
platform:author_name
```

风险：

```text
不作为跨 Case Identity Risk 自动合并依据
```

可在当前 Case detail 中显示：

```text
unresolved local risk
```

但不能跨 Case 汇总。

---

# 32.3 Coordination Membership

复用：

```text
IntegrityRepository.list_clusters
IntegrityRepository.list_cluster_members
```

Profile 可以展示：

```text
历史参与的 cluster
case
score
size
window
```

不得重跑 coordination detector。

---

# 33. Entity API

```http
GET /api/v1/intelligence/entities

GET /api/v1/intelligence/entities/{entity_id}

GET /api/v1/cases/{case_id}/entities
```

List filters：

```text
query
platform
min_investigations
limit<=50
offset<=5000
```

V3 第一版：

```text
entity_type 固定 account
```

Detail：

```text
recent posts <=20
```

不返回：

```text
raw_payload
```

---

# 33.1 Entity stale data test

必须验证：

```text
Case A account 删除
→ refresh_case(A)
→ old case link 被删除

Canonical materialization retract
→ refresh_case
→ same_as relation retracted

relation retract
→ identity component split

component split
→ stale shared_actor CrossLink 后续变 inactive
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

reconcile_for_anchor(
    case_id,
    relation_type,
    algorithm_version,
    expected_fingerprints
)

list_for_case

list_between

list_workspace

count_for_case

related_case_ids
```

API 查询默认：

```text
is_active=true
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

固定：

```text
shared_actor
shared_post
shared_media
shared_content
```

四个 detector。

每个 detector：

```text
先计算完整 expected set
再 upsert
最后 reconcile stale
```

禁止：

```text
发现一个写一个
中途异常后立即清理旧 relation
```

只有 detector 本轮成功计算完整 expected set 后才能执行 stale reconcile。

---

# 36. shared_actor

单位：

```text
Workspace identity component
```

不是单 WorkspaceEntity 节点。

步骤：

```text
1. 获取 anchor Case 的 Workspace Entities

2. 批量获取 active same_as relations

3. 构造 identity components

4. 查询 component 全部 Case links

5. 对每个其它 Case 统计共享 component 数

6. 每个 Case Pair 创建一条 shared_actor link
```

固定：

```text
status=observed
score=1.0
```

evidence：

```text
component_key
entity_ids
platform identity keys
```

同 Pair 共享 N 个 actor component：

```text
evidence_count=N
```

最多保存 50 个 refs。

---

# 37. shared_post

精确条件：

```text
same platform
same native_id
different case_id
```

固定：

```text
observed
score=1.0
```

新增 SocialRepository batch helper：

```text
find_cross_case_native_post_matches(
    case_id,
    platform_native_pairs,
    limit
)
```

禁止逐 Post N+1 查询。

---

# 38. shared_media

## Exact

基于：

```text
MediaAsset.actual_sha256
```

不同 Case 相同：

```text
observed
score=1.0
```

新增 MediaPipelineRepository helper：

```text
find_cross_case_sha_matches(
    case_id,
    sha256_values,
    limit
)
```

---

## Candidate phash

复用当前 Alignment 的：

```text
四段 phash blocking:
offset 0,4,8,12

algo.content_alignment

algo.POSSIBLE_THRESHOLD
```

新增 batch helper：

```text
find_cross_case_phash_candidates(
    case_id,
    block_keys,
    limit
)
```

实现可以使用双方言兼容 SQL substring / bounded query。

但必须满足：

```text
单次 refresh 一个 SQL/batch query
而不是每个 asset 扫全表
```

score：

```text
algo.content_alignment(...).score
```

达到：

```text
POSSIBLE_THRESHOLD
```

才：

```text
status=candidate
```

V3 不把 phash candidate 自动升级 observed。

---

# 39. shared_content

当前 `SourcePost.content_hash` 已确认是：

```text
SHA256(raw content)
```

V3 第一版固定只做：

```text
exact raw-content reuse
```

Detector 使用：

```text
SourcePost.content_hash
```

禁止执行智能体自行改成：

```text
alignment.normalize_text hash
MinHash
embedding similarity
LLM similarity
```

这保证：

```text
可索引
deterministic
无 O(N²)
```

新增/确认索引：

```text
(source_posts.content_hash, source_posts.case_id)
```

新增 batch helper：

```text
find_cross_case_content_hash_matches(
    case_id,
    hashes,
    limit
)
```

额外规则：

```text
空 content 不参与

如果 pair 已满足 shared_post
则不计入 shared_content evidence_count
```

避免同一个原始 Post 在 `cross_case_overlap` 中同时作为：

```text
shared_post
+
shared_content
```

重复加权。

---

# 40. 禁止 O(N²)

严禁：

```text
所有 Case × 所有 Case

所有 Post × 所有 Post

所有 Media × 所有 Media
```

全量 pairwise。

必须使用：

```text
Workspace identity key/component

platform/native_id

content_hash

media SHA

phash blocking
```

生成候选。

---

# 41. Cross Link 聚合与更新

每：

```text
Case Pair + relation_type + algorithm_version
```

只有一条 Link。

refresh 时更新：

```text
score
status
evidence_count
evidence_refs_json
feature_scores_json
last_seen_at
is_active=true
```

第一次：

```text
first_seen_at=now
```

stale：

```text
is_active=false
```

不 hard delete。

---

# 42. Related Investigation DTO

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
  "shared_post_count": 0,
  "shared_media_count": 1,
  "shared_content_count": 0,
  "has_candidate_relation": false
}
```

---

# 43. Cross-case API

```http
GET /api/v1/cases/{case_id}/related-investigations

GET /api/v1/intelligence/connections

GET /api/v1/intelligence/connections/{left_case_id}/{right_case_id}
```

Filters：

```text
relation_type
status
active_only=true
min_score
limit
```

---

# 43.1 Investigation Overview Related Card

最多：

```text
5
```

排序：

```text
relation type count DESC
then max_score DESC
then case updated_at DESC
```

显示：

```text
Case title
observed/candidate badge
relation types
shared actor count
shared media/content count
```

candidate 不得显示成“已确认关联”。

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

保持：

```text
SignalService
├── Monitor Alert Source
└── Derived Signal Source
```

Monitor Alert：

```text
仍使用 MonitorRepository
仍使用原 ID
仍使用原状态机
```

Derived Signal：

```text
使用 DerivedSignalRepository
```

统一输出：

```text
SignalResponse
```

---

# 49.1 SignalResponse additive extension

扩展当前 Schema，新增默认字段：

```text
related_case_ids: list[str] = []

source_label: str | None = None

detector_version: str | None = None

detector_active: bool | None = None
```

保留所有 V2 字段：

```text
id
source_type
source_id
case_id
case_title
signal_type
severity
status
title
why_it_matters
confidence
evidence_refs
trigger_count
first_seen_at
detected_at
updated_at
```

不得删除/改名现有字段。

---

# 50. DerivedSignalRepository

新增：

```text
backend/app/infrastructure/database/derived_signal_repository.py
```

方法：

```text
upsert_observed_signal

reconcile_detector_scope

get

list

list_for_case

set_status

list_case_links

replace_case_links
```

Case filter：

```text
必须 JOIN derived_signal_case_links
```

---

# 51. AdvancedSignalDetectorService

新增：

```text
backend/app/application/advanced_signal_service.py
```

依赖：

```text
DerivedSignalRepository
IntegrityRepository
AnalysisJobRepository
WorkspaceEntityRepository
CrossInvestigationRepository
MediaPipelineRepository
ApplicationRepository
```

固定 4 detector：

```text
coordination_cluster
actor_recurrence
media_reuse
cross_case_overlap
```

---

# 52. coordination_cluster

不能直接：

```text
IntegrityRepository.list_clusters(case)
→ 把所有历史 cluster 都变 signal
```

因为当前 Integrity cluster 是历史累积对象。

V3 必须以：

```text
最新 succeeded integrity AnalysisJob.result_json
```

中的：

```text
cluster_ids
```

为当前 detector scope。

因此对现有：

```text
IntegrityService.analyze_case
```

做兼容性 additive 修改：

原返回保留：

```text
assessments
clusters
```

新增：

```text
cluster_ids
window_start
window_end
```

旧消费者不受影响。

AdvancedSignal：

```text
latest succeeded integrity job
→ result_json.cluster_ids
→ IntegrityRepository.get_cluster
```

如果没有最新 succeeded integrity job：

```text
本轮 coordination detector expected set = empty
```

但不要因此修改旧 Monitor Signal。

---

# 52.1 coordination threshold

触发：

```text
cluster.size >= 3
AND
cluster.score >= 0.75
```

severity：

```text
score >= 0.90
AND size >= 5
→ critical

otherwise
→ warning
```

文案：

```text
检测到疑似协调行为模式
```

禁止：

```text
确认水军
确认机器人
确认恶意操控
```

fingerprint：

```text
signal_type + cluster_id + detector_version
```

Case links：

```text
cluster.case_id only
```

---

# 53. actor_recurrence

以：

```text
Workspace identity component
```

为主体。

条件：

```text
distinct Investigation count >= 3
```

severity：

```text
3–4
→ warning

>=5
→ critical
```

source_id：

```text
identity_component_key
```

Case links：

```text
component 出现过的全部 Cases
```

primary case：

```text
case_id 字典序最小值
```

文案：

```text
该主体在多个 Investigation 中重复出现
```

禁止：

```text
幕后黑手
操控主体
```

---

# 54. media_reuse

Derived Signal 只对：

```text
exact MediaAsset.actual_sha256
```

触发。

不要对 phash candidate 生成 `media_reuse` Signal。

原因：

```text
candidate 适合 Connections Intelligence
不适合直接进入高级告警流
```

条件：

```text
同 SHA 出现在 >=2 distinct Cases
```

severity：

```text
2–3
→ warning

>=4
→ critical
```

fingerprint：

```text
signal_type + actual_sha256 + detector_version
```

Case links：

```text
全部出现该 SHA 的 Cases
```

---

# 55. cross_case_overlap

只使用：

```text
is_active=true
```

Cross Links。

Case Pair 特征：

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

要求：

```text
score >= 0.60

AND

>= 2 active relation types
```

severity：

```text
0.60–0.849999
→ warning

>=0.85
→ critical
```

fingerprint：

```text
signal_type
+
canonical left_case_id
+
canonical right_case_id
+
detector_version
```

Case links：

```text
left
right
```

文案：

```text
多个独立关联特征显示两个 Investigation 之间存在较强重叠，
建议进一步核查。
```

不得表述成同一操控主体已经确认。

---

# 56. Detector reconciliation

每个 detector：

```text
计算 expected fingerprints
```

然后调用：

```text
DerivedSignalRepository.reconcile_detector_scope(...)
```

scope：

```text
signal_type
detector_version
与本次 case/component/media/pair 相关的 Case links
```

任何本次不再成立的 Signal：

```text
detector_active=false
```

并按 #11.2 生命周期处理。

这是 V3-D P0 requirement。

---

# 57. SignalService 改造

`list_signals`：

```text
query Monitor source

query Derived source

map to common SignalResponse

merge

server-side deterministic sort

final limit
```

severity rank：

```text
critical = 0
warning = 1
info = 2
other = 3
```

排序：

```text
severity rank ASC
detected_at DESC
id ASC
```

必须在 Service 统一排序。

不要仅在前端排序。

---

# 57.1 get_signal / change_status

保持 Monitor ID 兼容。

Lookup 顺序：

```text
1. existing Monitor source lookup
2. DerivedSignalRepository.get
3. signal_not_found
```

理论 UUID collision 极低；若两个 source 出现同 ID：

```text
记录 error
拒绝 ambiguous mutation
```

`change_status`：

```text
Monitor
→ MonitorRepository.set_alert_status

Derived
→ DerivedSignalRepository.set_status
```

action：

```text
acknowledge
resolve
suppress
```

保持不变。

---

# 58. Signals API

扩展：

```text
source_type
signal_type
severity
status
case_id
detector_active
```

Derived `case_id` filter：

```text
JOIN derived_signal_case_links
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

Source / Type filter：

```text
All
Monitor
Coordination
Actor recurrence
Media reuse
Cross-case overlap
```

Detail 增加：

```text
source type
confidence
detector active/inactive
related investigations
evidence refs
detector version
```

inactive + resolved：

```text
显示“条件已消失”
```

candidate cross-case relation 本身：

```text
不得直接产生 cross_case_overlap Signal
```

除非综合 active relation 公式达到阈值。

---

# Part F — Intelligence Refresh

# 60. 不新增 Worker

复用：

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

# 60.1 Dependency freshness 原则

V3 Entity / Cross-case / Advanced Signal 依赖：

```text
当前 Alignment materialization
当前 Integrity result
```

因此 Collection 完成后：

禁止只：

```text
enqueue intelligence_refresh
```

然后直接基于旧 Alignment / Integrity 做 V3 刷新。

正确链路：

```text
Collection terminal
    ↓
enqueue alignment job
enqueue integrity job
    ↓
两类 job 各自完成后
    ↓
enqueue intelligence_refresh
```

Quality GET 自身 fresh-if-needed：

```text
不需要等待上述后台链路才能显示。
```

---

# 61. IntelligenceRefreshService

新增：

```text
backend/app/application/intelligence_refresh_service.py
```

依赖：

```text
AnalysisJobRepository

InvestigationQualityService
WorkspaceEntityService
CrossInvestigationService
AdvancedSignalDetectorService
```

提供两个职责不同的方法。

## execute

```python
async def refresh_case(case_id):
    quality = await quality_service.evaluate(case_id)

    entities = await workspace_entity_service.refresh_case(case_id)

    cross_case = await cross_investigation_service.refresh_case(case_id)

    signals = await advanced_signal_service.refresh_case(case_id)

    return {...}
```

固定顺序：

```text
quality
→ entities
→ cross_case
→ signals
```

---

## enqueue

```python
async def enqueue(
    case_id: str,
    *,
    source_key: str,
):
    return await analysis_job_repository.create_job(
        case_id=case_id,
        job_type="intelligence_refresh",
        idempotency_key=source_key,
    )
```

---

# 62. AnalysisJobWorker 扩展

构造增加：

```text
intelligence_service
```

`_run`：

```python
if job_type == "intelligence_refresh":
    return await intelligence_service.refresh_case(case_id)
```

原：

```text
alignment
integrity
```

保持不变。

---

# 62.1 Alignment / Integrity 成功后的 follow-up

只有：

```text
complete_job(...) == True
```

后才允许 enqueue follow-up。

alignment：

```text
source_key =
v3:intel:alignment:{analysis_job_id}:{V3_INTELLIGENCE_VERSION}
```

integrity：

```text
source_key =
v3:intel:integrity:{analysis_job_id}:{V3_INTELLIGENCE_VERSION}
```

`intelligence_refresh`：

```text
绝不 enqueue 自己
```

这避免原计划“按分钟 idempotency”导致：

```text
alignment refresh
和
integrity refresh
```

在同一分钟错误去重，从而遗漏最新 Integrity 结果。

---

# 63. Collection terminal 触发

CollectionRun：

```text
completed
completed_with_errors
```

后 best-effort enqueue：

```text
alignment job

integrity job
```

idempotency：

```text
v3:alignment:{collection_run_id}:{V3_INTELLIGENCE_VERSION}

v3:integrity:{collection_run_id}:{V3_INTELLIGENCE_VERSION}
```

禁止：

```text
直接 enqueue intelligence_refresh
```

Collection Worker 需要注入一个轻量 scheduler/callback。

推荐直接注入：

```text
AnalysisJobRepository
```

或一个只负责：

```text
enqueue_analysis_dependencies(...)
```

的 Service。

不要让 enqueue 失败改变：

```text
CollectionRun terminal status
```

失败只：

```text
log warning
metric
```

---

# 64. Manual Refresh API

新增：

```http
POST /api/v1/cases/{case_id}/intelligence:refresh
```

该 endpoint 表示：

> **完整刷新 V3 Intelligence dependencies。**

所以它不是直接创建：

```text
intelligence_refresh
```

而是创建：

```text
alignment
integrity
```

两个 AnalysisJob。

idempotency：

```text
manual-v3:alignment:{case_id}:{YYYYMMDDHHmm}:{V3_INTELLIGENCE_VERSION}

manual-v3:integrity:{case_id}:{YYYYMMDDHHmm}:{V3_INTELLIGENCE_VERSION}
```

返回：

```json
{
  "status": "accepted",
  "alignment_job_id": "...",
  "integrity_job_id": "..."
}
```

完成后由 Worker follow-up：

```text
intelligence_refresh
```

---

# 65. AnalysisJobRepository 扩展

新增：

```text
latest_succeeded(
    case_id,
    job_type
)
```

用于：

```text
Advanced Signal coordination detector
```

读取最新 Integrity result。

不得：

```text
list_jobs(limit=10000)
→ Python 找最新
```

---

# 66. Historical Backfill

新增脚本：

```text
backend/app/scripts/refresh_v3_intelligence.py
```

命令：

```bash
python -m app.scripts.refresh_v3_intelligence --all
```

默认：

```text
为每个 Case enqueue alignment + integrity
```

而不是直接 enqueue intelligence。

idempotency：

```text
backfill-v3:alignment:{case_id}:{V3_INTELLIGENCE_VERSION}

backfill-v3:integrity:{case_id}:{V3_INTELLIGENCE_VERSION}
```

Case 顺序：

```text
created_at ASC
id ASC
```

脚本只 enqueue：

```text
不等待
不跑算法
```

---

# 67. Case 删除与 V3 cleanup

当前 `ApplicationRepository.delete_case` 是显式级联实现。

V3 必须同步扩展，不得只依赖数据库 FK。

删除 Case 时，在删除 `CaseRecord` 之前：

```text
1. delete InvestigationQualityRecord(case_id)

2. retract/delete WorkspaceEntityRelation
   where source_case_id=case_id

3. delete WorkspaceEntityCaseLink(case_id)

4. delete CrossInvestigationLink
   where left_case_id=case_id
   OR right_case_id=case_id

5. delete DerivedSignalCaseLink(case_id)

6. delete DerivedSignalRecord
   where primary case_id=case_id

7. cleanup DerivedSignal with zero case links

8. cleanup WorkspaceEntity with zero case links
   AND zero active relations
```

Case delete 完成后：

```text
不得残留 active Cross Link
不得残留 Case appearance
不得让 query_signals(case_id) 查到孤儿 Signal
```

---

# 67.1 Refresh / delete concurrency

所有：

```text
Workspace Entity key creation
Case link reconciliation
Entity relation reconciliation
Cross Link upsert/reconcile
Derived Signal upsert/reconcile
```

必须依赖：

```text
数据库 UNIQUE
事务
IntegrityError reload/retry
```

保证并发安全。

禁止用：

```text
先 SELECT
如果没有
再 INSERT
```

而没有 unique conflict handling。

---

# Part G — Agent / Tool

# 68. 不新增 Expert Agent

继续使用现有：

```text
Coordinator
Opinion
Propagation
Verification
Evidence Critic
Report
Citation Validator
```

不新增：

```text
Quality Agent
Entity Agent
Cross-case Agent
Signal Agent
```

---

# 69. 新增 5 个只读 Intelligence Tool

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

全部：

```text
permissions=("read_database",)

side_effect="none"

idempotent=True

requires_approval=False

cache_ttl_seconds=0

execution_class="trusted_in_process"
```

必须有：

```text
Pydantic Input Model
Pydantic Output Model
routing-oriented ToolSpec.description
```

---

# 70. Runtime Case Scope

修改现有：

```text
_CASE_SCOPED_TOOLS
```

加入：

```text
get_investigation_quality
query_related_investigations
query_workspace_entities
get_workspace_entity
query_signals
```

Runtime 强制：

```python
arguments["case_id"] = context.case_id
```

LLM 不负责生成 case_id。

---

# 71. Tool Contract

## get_investigation_quality

Input：

```text
case_id
```

Runtime inject。

Output：

```text
overall_score
grade
dimensions
top_gaps
computed_at
algorithm_version
```

Description 必须说明：

```text
Quality = investigation completeness/readiness
NOT truth score
```

---

## query_related_investigations

Input：

```text
case_id runtime

relation_type?
status?
min_score? 0..1
limit default 10 max 50
```

默认：

```text
active_only=true
```

Output：

```text
related case id/title
relation types
observed/candidate
evidence counts
max score
```

---

## query_workspace_entities

Input：

```text
case_id runtime

query?
platform?
min_investigations?
limit default 20 max 50
offset max 5000
```

默认只返回：

```text
当前 Case 直接出现的 Entity / Identity Component
```

不允许默认全 Workspace dump。

---

## get_workspace_entity

Input：

```text
case_id runtime
entity_id required
```

Scope：

允许读取：

```text
当前 Case 有 CaseLink

OR

该 Entity identity component
与当前 Case 存在 active related Investigation 关系
```

否则：

```text
found=false
```

Output：

```text
identity component
platform identities
case appearances
recent posts <=20
risk summary
coordination memberships
related investigations
```

---

## query_signals

Input：

```text
case_id runtime

status?
severity?
signal_type?
source_type?
detector_active?
limit default 20 max 50
```

默认：

```text
当前 Case 相关 Signals
```

Derived source 必须通过：

```text
derived_signal_case_links
```

做 Case Scope。

---

# 72. Tool Description 路由规则

Tool description 必须明确：

```text
Quality question
→ get_investigation_quality

Cross-case relation
→ query_related_investigations

Actor/account recurrence
→ query_workspace_entities / get_workspace_entity

Advanced anomaly / signal
→ query_signals
```

同时明确：

```text
candidate relation
risk assessment
advanced signal
```

都是：

```text
intelligence indicator
```

不是：

```text
verified fact
```

---

# 73. Coordinator Prompt

增加：

```text
【Investigation Quality】

“当前调查还缺什么”
“调查是否完整”
“是否具备报告准备度”

→ get_investigation_quality


【Cross Investigation】

“这个事件和过去哪些调查有关”
“是否存在重复账号/媒体/帖子”

→ query_related_investigations


【Actor / Entity】

“这个账号是否在其它事件出现”
“这个主体有哪些平台身份”

→ query_workspace_entities
→ get_workspace_entity


【Advanced Signals】

“当前有哪些异常”
“是否有协调行为”
“是否有重复主体/媒体”
“哪些 Case 出现高重叠”

→ query_signals
```

强制规则：

```text
observed
≠ verified fact

candidate
≠ observed

risk Signal
≠ malicious actor proof
```

---

# 74. Expert Allowlist

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

Opinion：

```text
不新增 V3 Tool
```

Citation Validator：

```text
不新增 V3 Tool
```

不要给所有 Expert 全部 Tool。

---

# 75. Agent Routing Tests

必须：

```text
A01 “当前调查还缺什么”
→ get_investigation_quality

A02 “与过去哪些调查有关”
→ query_related_investigations

A03 “这个账号以前在哪出现过”
→ workspace entity tools

A04 “当前有哪些跨事件异常”
→ query_signals

A05 candidate relation
→ Agent 明确候选语义

A06 coordination critical
→ Agent 不得回答“确认水军”

A07 Quality 95
→ Agent 不得回答“结论 95% 真实”
```

---

# Part H — 测试

# 76. Backend 新测试文件

至少：

```text
test_investigation_quality.py

test_workspace_entities.py

test_cross_investigation.py

test_advanced_signals.py

test_intelligence_refresh.py

test_intelligence_tools.py

test_intelligence_api.py

test_v3_case_deletion.py
```

---

# 77. Quality Tests

```text
Q01 empty Case → insufficient_data

Q02 collection 5/5 → 100

Q03 running Collection does not create premature critical gap

Q04 terminal missing >=50% → critical

Q05 Claim without Evidence → warning

Q06 Finding with only contradicts/context
→ NOT counted as supported

Q07 verified Finding without supports link
→ critical

Q08 candidate Finding not resolved

Q09 dangling provenance lowers score

Q10 published report dangling citation → critical

Q11 validate_for_publish uses same validator as publish gate

Q12 unavailable dimension removed from denominator

Q13 unchanged fingerprint → no recompute

Q14 Finding updated → fingerprint changes

Q15 FindingEvidenceLink added/removed
→ fingerprint changes

Q16 FindingSourceLink added
→ fingerprint changes

Q17 report lock_version change
→ fingerprint changes

Q18 Quality wording != truth score
```

---

# 78. Entity Tests

```text
E01 same platform/native_id across Cases
→ same WorkspaceEntity

E02 same display name different native_id
→ NOT same WorkspaceEntity

E03 concurrent create same platform key
→ one entity after unique conflict resolution

E04 active Case CanonicalEntity mentions
→ active WorkspaceEntityRelation

E05 retract Case materialization
→ relation retracted

E06 retracted relation
→ identity component splits

E07 unconfirmed/pending Alignment candidate
→ no active relation

E08 stale Account removed from Case
→ stale CaseLink removed

E09 case link unique

E10 identity component max guard works

E11 Entity profile investigation_count correct

E12 exact platform risk reused

E13 name-only risk not promoted cross-case

E14 coordination cluster membership reused
```

---

# 79. Cross-Investigation Tests

```text
C01 shared identity component → shared_actor observed

C02 same platform/native post → shared_post observed

C03 same media SHA → shared_media observed

C04 phash >= existing POSSIBLE_THRESHOLD
→ shared_media candidate

C05 exact raw content_hash across distinct posts
→ shared_content observed

C06 same original Post does not also increment shared_content

C07 candidate != observed

C08 pair ordering deterministic

C09 fingerprint is pair+relation+algorithm version

C10 multiple evidence aggregates into one link

C11 refresh idempotent

C12 stale actor relation → shared_actor is_active=false

C13 stale media/content → old link inactive

C14 no all-pairs semantic scan

C15 refresh detector exception
→ does NOT stale-reconcile partial expected set

C16 Case deletion removes active pair links
```

---

# 80. Advanced Signal Tests

```text
S01 Monitor Alert response unchanged

S02 Monitor acknowledge still writes MonitorRepository

S03 latest integrity job cluster_ids used

S04 historical cluster outside latest integrity result
→ no current coordination signal

S05 coordination below threshold → no signal

S06 coordination threshold → warning

S07 strong coordination → critical

S08 actor component in 3 Cases → warning

S09 actor component in 5 Cases → critical

S10 exact media SHA in >=2 Cases → media_reuse

S11 phash candidate alone
→ no media_reuse signal

S12 cross overlap needs >=2 active relation types

S13 overlap formula exact

S14 detector true→true does not increment occurrence

S15 true→false sets detector_active=false

S16 open/ack signal condition cleared
→ auto resolved

S17 false→true reopens unsuppressed signal
and increments occurrence

S18 suppressed signal remains suppressed on recurrence

S19 derived status mutation does not touch Monitor

S20 fingerprint dedup

S21 derived case filter uses signal_case_links

S22 wording does not overclaim manipulation/bot facts
```

---

# 81. Intelligence Refresh Tests

```text
IR01 intelligence_refresh job type supported

IR02 execute order:
quality → entity → cross_case → signals

IR03 lease unchanged

IR04 cancel unchanged

IR05 retry unchanged

IR06 intelligence job does not recursively enqueue

IR07 Collection terminal enqueues alignment + integrity

IR08 Collection does NOT directly enqueue intelligence_refresh

IR09 alignment successful completion
→ follow-up intelligence job

IR10 integrity successful completion
→ distinct follow-up intelligence job

IR11 alignment/integrity same minute
→ no accidental idempotency collision

IR12 Collection dependency enqueue failure
→ CollectionRun terminal unchanged

IR13 manual refresh returns alignment/integrity job ids

IR14 latest_succeeded helper deterministic

IR15 backfill uses versioned idempotency keys
```

---

# 82. Agent Tool Tests

```text
AT01 all 5 tools require read_database

AT02 no tool requires write_database

AT03 runtime injects current Case

AT04 related query active-only default

AT05 candidate status preserved

AT06 entity exact read context scoped

AT07 signal case scope via case-link table

AT08 Coordinator has 5 tools

AT09 Expert allowlists exact

AT10 no new Expert Agent

AT11 Tool descriptions explain intelligence-indicator semantics

AT12 Output Models bound evidence/post arrays
```

---

# 83. Case Deletion Tests

```text
D01 delete Case removes Quality

D02 delete Case removes Entity CaseLinks

D03 delete Case retracts/removes relation source

D04 delete Case removes Cross Links touching Case

D05 delete Case removes DerivedSignalCaseLinks

D06 primary DerivedSignal deleted with Case

D07 orphan DerivedSignal cleanup

D08 orphan WorkspaceEntity cleanup

D09 remaining unrelated Workspace intelligence preserved
```

---

# 84. Frontend Tests

新增/扩展：

```text
IntelligenceView.test.ts

InvestigationOverviewView.test.ts

SignalsView.test.ts

HomeView.test.ts
```

覆盖：

```text
Connections tab

Entities tab

observed/candidate visual

inactive relation not default-visible

Quality six dimensions

Quality top gaps

quality_unassessed_count

Related Investigation Card

Derived Signal source filter

detector inactive/resolved wording

empty/loading/error
```

---

# 85. Migration Tests

SQLite：

```text
upgrade
downgrade
upgrade
```

必须验证：

```text
8 new tables

source_posts content_hash/case index

unique constraints

FKs

indexes
```

PostgreSQL 可用时：

```text
至少 upgrade
unique/FK/index behavior
```

不可用：

```text
明确 skip
```

---

# 86. Mandatory Adjacent Regression

必须覆盖：

```text
Alignment
Alignment retract/materialization

Integrity
Analysis Jobs

Signals
Monitor Alert transitions

Workspace Overview

Findings
Finding Evidence links

Review

Provenance

ReportDocument
Report publish refs

CollectionRun

Agent DB Tools
Agent Runtime
Expert Agents
Tool System

Case deletion
SocialRepository
MediaPipelineRepository
```

---

# 87. Frontend Gate

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

全部通过。

---

# 88. Full Regression 升级条件

以下任一发生：

```text
修改 Database engine/session factory

修改 AgentRuntime core

修改 ToolRegistry core

修改 Review verified/rejected state machine

修改 Finding mutation semantics

修改 Monitor Alert transition semantics

修改 CollectionRun lifecycle semantics

修改 Alignment existing candidate/materialization decision semantics

修改 Integrity detector thresholds/algorithm

修改 Report publish gate acceptance semantics

出现无法局部解释的跨域 failure
```

则运行 Full Backend Regression。

仅：

```text
给 Integrity result 增加 cluster_ids/window
```

属于 additive output，不自动触发 Full Regression，但必须跑 Integrity/AnalysisJob adjacent tests。

---

# Part I — E2E

# 89. E2E-A — V2 Closure Baseline

在 V3 修改前确认：

```text
Collection
Evidence
Finding
Review
Report
Alignment
Integrity
Signals
Provenance
Agent DB Tool
```

基础链仍然工作。

---

# 90. E2E-B — Investigation Quality

打开已有 Investigation：

```text
Overview Quality Card
```

必须看到：

```text
6 dimensions
grade
score
top gaps
computed_at
```

seeded DB 与 deterministic expected 完全一致。

---

# 91. E2E-C — Quality Fingerprint Link Mutation

```text
Quality 第一次计算

给 Finding 新增 supports Evidence Link

再次 GET quality
```

必须：

```text
fingerprint 变化
Finding Support 更新
```

---

# 92. E2E-D — Same Actor Exact Identity

```text
Case A:
weibo:123

Case B:
weibo:123
```

refresh 后：

```text
one deterministic WorkspaceEntity node
case appearances=2

A-B:
shared_actor observed
```

---

# 93. E2E-E — No False Name Merge

```text
Case A:
name=张三
native_id=111

Case B:
name=张三
native_id=222
```

必须：

```text
2 independent WorkspaceEntity nodes
```

---

# 94. E2E-F — Reversible Cross-platform Identity

在 Case C：

```text
Account X
Account Y

Alignment materialized confirmed same_as
```

V3 refresh：

```text
active WorkspaceEntityRelation(X,Y)
```

随后：

```text
retract candidate materialization
refresh
```

必须：

```text
relation=retracted

identity component splits

依赖该 relation 的 stale shared_actor
最终 is_active=false
```

---

# 95. E2E-G — Media Reuse

相同：

```text
actual_sha256
```

出现在 A/B。

必须：

```text
shared_media observed

media_reuse Derived Signal
```

如果仅 phash 相似：

```text
shared_media candidate

不得产生 media_reuse Signal
```

---

# 96. E2E-H — Coordination Signal Currentness

准备：

```text
旧 Integrity cluster high score

最新 Integrity job 只产出另一个 cluster
```

Advanced Signal refresh：

```text
只根据最新 job cluster_ids
```

不得把历史 cluster 重新作为当前 Signal。

---

# 97. E2E-I — Signal Condition Clears

先满足：

```text
actor recurrence >=3 cases
```

产生：

```text
open Signal
detector_active=true
```

移除一个 Case appearance，使：

```text
count <3
```

refresh 后：

```text
detector_active=false
status=resolved
```

恢复到：

```text
>=3
```

refresh 后：

```text
detector_active=true
status=open
occurrence_count+1
```

---

# 98. E2E-J — Cross-case Overlap

A/B：

```text
>=2 active relation types
score >= 0.60
```

产生：

```text
cross_case_overlap
```

移除其中一个关键 relation：

如果不再达阈值：

```text
Signal detector_active=false
```

---

# 99. E2E-K — Copilot Cross-case

用户：

```text
这个事件和之前哪些调查有关？
```

Coordinator 必须调用：

```text
query_related_investigations
```

不能从 History 猜。

candidate：

```text
必须说候选
```

---

# 100. E2E-L — Copilot Entity

用户：

```text
这个微博账号以前在哪些事件出现过？
```

必须：

```text
query_workspace_entities
get_workspace_entity
```

返回当前 Workspace Intelligence。

---

# 101. E2E-M — Case Delete

创建：

```text
Quality
Entity CaseLink
Entity Relation
Cross Link
Derived Signal
Signal CaseLink
```

删除一个 Case。

必须：

```text
V3 数据清理完成
其它 Case Intelligence 保留
无孤儿 active relation/signal
```

---

# Part J — 实施顺序

# 102. V3-0 — Baseline / Closure

执行：

```text
git rev-parse HEAD
git status
latest migration

V2 Closure Baseline

Mandatory adjacent backend baseline

frontend:
typecheck
lint
test
build
```

创建：

```text
docs/v3-intelligence-depth-delivery.md
```

记录 baseline。

---

# 103. V3-1 — Schema

实现：

```text
InvestigationQualityRecord

WorkspaceEntityRecord
WorkspaceEntityKeyRecord
WorkspaceEntityCaseLinkRecord
WorkspaceEntityRelationRecord

CrossInvestigationLinkRecord

DerivedSignalRecord
DerivedSignalCaseLinkRecord

source_posts(content_hash, case_id) index

migration
```

先过：

```text
migration tests
```

再写 Service。

---

# 104. V3-2 — Quality

实现顺序：

```text
Repository helper aggregate queries

InvestigationQualityRepository

ReportDocumentService.validate_for_publish

InvestigationQualityService

Quality API

Quality tests

Overview Quality Card

Home quality attention/unassessed
```

Gate：

```text
supports relation correctly handled

link mutation fingerprint invalidation

publish validator reused

Quality != truth
```

---

# 105. V3-3 — Workspace Entity

实现：

```text
WorkspaceEntityRepository

deterministic keys

case link reconciliation

reversible WorkspaceEntityRelation

Identity component traversal

WorkspaceEntityService

Entity API

tests
```

Gate：

```text
same exact platform ID → same node

same name only → no merge

Case materialization retract → relation retract

stale case link removed
```

---

# 106. V3-4 — Cross Investigation

实现：

```text
Repository

Social cross-match batch helpers

Media cross-match batch helpers

shared_actor

shared_post

shared_media

shared_content

reconcile stale links

APIs

Related Investigation Card

tests
```

Gate：

```text
no O(N²)

stale link becomes inactive

content_hash exact semantics fixed

phash only candidate
```

---

# 107. V3-5 — Intelligence UI

实现：

```text
/intelligence

Connections

Entities

frontend/src/services/api/intelligence.ts

Sidebar entry
```

不要修改 Investigation Network 三个既有 mode。

---

# 108. V3-6 — Advanced Signals

实现：

```text
Integrity additive cluster_ids output

AnalysisJob latest_succeeded helper

DerivedSignalRepository

DerivedSignalCaseLink

4 detectors

detector active/inactive lifecycle

SignalService union

Signal Schema additive fields

Signals API

Signals UI

tests
```

Gate：

```text
historical Integrity cluster 不误报

stale Signal 自动 inactive/resolved

suppressed 不自动 reopen

Monitor Alert 兼容
```

---

# 109. V3-7 — Durable Dependency Pipeline

实现：

```text
IntelligenceRefreshService.execute

IntelligenceRefreshService.enqueue

AnalysisJobWorker intelligence job

AnalysisJob follow-up scheduling

Collection terminal enqueue alignment + integrity

Manual full refresh API

Historical backfill script

tests
```

Gate：

```text
Collection 不直接基于 stale Alignment/Integrity refresh V3

alignment / integrity follow-up keys 不冲突

no recursive intelligence job
```

---

# 110. V3-8 — Agent Integration

实现：

```text
intelligence_tools.py

5 Tool Input/Output Models

_CASE_SCOPED_TOOLS

ToolSpec descriptions

Coordinator prompt

Expert allowlists

routing tests
```

---

# 111. V3-9 — Case Delete / Cleanup

修改：

```text
ApplicationRepository.delete_case
```

加入 V3 清理。

完成：

```text
test_v3_case_deletion.py
```

---

# 112. V3-10 — Frontend / E2E

完成：

```text
frontend gates

E2E A–M
```

至少：

```text
2 Cases
```

用于 Cross-case。

至少：

```text
1 retract alignment scenario
```

用于可逆 Identity。

---

# 113. V3-11 — Historical Backfill

运行：

```bash
python -m app.scripts.refresh_v3_intelligence --all
```

确认：

```text
历史 Case 入队成功

alignment/integrity job 成功

follow-up intelligence 成功

Quality 可生成

Entity 可生成

Cross Link 可生成

Derived Signal 无错误
```

---

# 114. V3-12 — Delivery

更新：

```text
docs/v3-intelligence-depth-delivery.md

architecture docs

Signals docs

Alignment / Workspace Entity docs

Quality docs
```

Delivery 必须记录：

```text
Baseline HEAD
Final HEAD
Migration revision

Files changed

Algorithm versions

V2 Closure result

Quality test result

Entity/retraction result

Cross-case result

Signal stale lifecycle result

Dependency pipeline result

Agent routing result

E2E result

Historical backfill result

Known limitations
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

## V2 Closure

```text
[ ] V2 core baseline recorded
[ ] no blocking V2 P0 remains
[ ] Collection / Evidence / Finding / Review / Report still work
[ ] Alignment / Integrity / Monitor Signals still work
```

## Quality

```text
[ ] 6 deterministic dimensions

[ ] supports / contradicts / context semantics correct

[ ] verified without supporting evidence → critical

[ ] Finding Evidence/Source Link mutations invalidate fingerprint

[ ] report validator shared with publish gate

[ ] Quality != truth wording

[ ] Overview Quality

[ ] Home needs-attention + unassessed
```

## Workspace Entity

```text
[ ] deterministic platform/native identity

[ ] name-only does not merge

[ ] Case links reconcile stale data

[ ] reversible same_as relation exists

[ ] Alignment retract retracts Workspace relation

[ ] no irreversible global merge from retractable V2 relation

[ ] identity component deterministic

[ ] exact Integrity risk reused

[ ] name-only risk not cross-case promoted
```

## Cross Investigation

```text
[ ] shared_actor uses identity component

[ ] shared_post exact

[ ] shared_media SHA observed

[ ] shared_media phash candidate

[ ] shared_content uses exact raw content_hash

[ ] same original post not double counted as shared_content

[ ] relation fingerprint one per pair/type/version

[ ] stale links become inactive

[ ] no all-pairs semantic O(N²)

[ ] candidate/observed separated
```

## Advanced Signals

```text
[ ] Monitor Alert remains source-of-truth

[ ] DerivedSignalCaseLink supports multi-case scope

[ ] detector_active separate from workflow status

[ ] stale detector condition auto-resolves open/ack

[ ] recurrence can reopen unsuppressed signal

[ ] suppressed stays suppressed

[ ] latest Integrity job cluster_ids used

[ ] historical clusters do not become current Signal

[ ] actor recurrence uses identity component

[ ] media reuse only exact SHA

[ ] cross-case overlap only active links

[ ] SignalService globally sorts monitor + derived

[ ] wording does not claim manipulation/bot facts
```

## Durable Pipeline

```text
[ ] intelligence_refresh job exists

[ ] AnalysisJob worker reused

[ ] lease/retry/cancel unchanged

[ ] Collection terminal enqueues alignment + integrity

[ ] Collection does not directly derive V3 from stale analysis

[ ] alignment completion enqueues intelligence follow-up

[ ] integrity completion enqueues distinct intelligence follow-up

[ ] no minute-key collision

[ ] manual refresh runs dependency pipeline

[ ] historical backfill versioned

[ ] no recursion
```

## Agent

```text
[ ] 5 read-only V3 Tools

[ ] all read_database only

[ ] runtime injects case_id

[ ] Input/Output Models bounded

[ ] Coordinator routing correct

[ ] Experts minimal allowlists

[ ] candidate/risk/quality never promoted to verified truth
```

## Compatibility

```text
[ ] V2 routes unchanged

[ ] Investigation Network modes unchanged

[ ] existing Alignment candidate/materialization semantics unchanged

[ ] Integrity thresholds/algorithm unchanged

[ ] Monitor Alert state machine unchanged

[ ] Review semantics unchanged

[ ] Report publish acceptance semantics unchanged

[ ] CollectionRun lifecycle unchanged

[ ] Agent DB Tool Pack unchanged except additive compatibility
```

## Cleanup

```text
[ ] Case deletion cleans V3 rows

[ ] stale Entity CaseLinks removed

[ ] stale Entity Relations retract

[ ] stale Cross Links inactive

[ ] orphan WorkspaceEntity cleanup

[ ] orphan DerivedSignal cleanup
```

## Tests

```text
[ ] migration tests

[ ] Quality tests

[ ] Entity tests

[ ] Cross-case tests

[ ] Advanced Signal tests

[ ] Intelligence Refresh tests

[ ] Agent Tool tests

[ ] Case deletion tests

[ ] mandatory adjacent regression

[ ] frontend typecheck

[ ] frontend lint

[ ] frontend tests

[ ] frontend build

[ ] E2E A–M

[ ] historical backfill validation
```

---

# 114.1 最终审阅结论

本计划经过最后一轮“文档—当前仓库实现”对照后，已修正以下原计划缺口：

```text
1. Cross-Investigation Link 不再 append-only，
   增加 is_active + stale reconciliation。

2. Derived Signal 增加 detector_active 生命周期，
   条件消失后不会永久保持 open。

3. Workspace Entity 不再基于可 retract 的 Alignment
   做不可逆 merge，改为可撤销 same_as relation。

4. shared_content 唯一实现固定为当前
   SourcePost.content_hash 的 exact raw-content hash，
   不再让执行智能体自行选择 normalization。

5. Quality fingerprint 纳入 FindingEvidenceLink /
   FindingSourceLink 变化。

6. Finding Support 只计算 relation=supports，
   不再把 contradicts/context 当作支持。

7. Collection 完成后先刷新 Alignment / Integrity，
   再由成功 job follow-up 刷新 V3 Intelligence，
   避免基于陈旧分析产生高级 Signal。

8. Intelligence Refresh idempotency 改为 source-job based，
   不再使用会吞掉同一分钟不同依赖更新的单一 minute key。

9. Advanced coordination Signal 只使用最新 succeeded
   Integrity job 产出的 cluster_ids，
   不把历史 cluster 当当前异常。

10. 增加 DerivedSignalCaseLink，
    多 Case Signal 不依赖 JSON contains 做 Case scope。

11. 补齐 Case 删除与 orphan cleanup。

12. 补齐 Tool Input/Output、Runtime case scope、
    Prompt 路由与 Agent 验收。
```

完成上述修订后，文档已经达到：

> **执行智能体可以直接按顺序实施，不需要重新做核心方案选择。**

若实现过程中某个具体生产文件名与本文略有差异：

```text
映射到当前等价生产路径
```

但不得改变本文已经固定的：

```text
数据边界
relation lifecycle
signal lifecycle
dependency order
事实语义
兼容性约束
验收标准
```


---

# 115. 最终完成状态

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
