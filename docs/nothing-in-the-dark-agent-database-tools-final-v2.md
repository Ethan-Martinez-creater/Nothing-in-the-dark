# Nothing-in-the-dark Agent 数据库查询 Tool Pack 最终执行方案（V2）

> 文档性质：本轮 Agent 数据访问正确性优化的最终实施规格  
> 目标仓库：`Ethan-Martinez-creater/Nothing-in-the-dark`  
> 已核验代码基线：`769b352e4e5f672604246857044b59a425918a03`  
> 目标问题：Agent 在回答“当前已采集数据 / 当前数据库状态”类问题时，缺少确定性的结构化数据库读取工具，容易从 Conversation History、Memory 或语义检索历史结果中回答，从而与当前数据库真实状态不一致。  
> 面向对象：负责直接修改仓库、补测试、执行真实 Case 验证并提交实现的执行智能体。  
>
> **本 V2 文档替代此前所有 Agent 数据库 Tool 方案。执行智能体应只以本文件作为本轮实现规范。**

---

# 1. 本轮目标

本轮只解决一个明确问题：

> 当用户询问当前 Case 中“数据库现在实际有什么、多少、是什么状态”时，Agent 必须能够直接、确定性地查询当前数据库，而不是依赖对话历史、Memory 或 RAG 结果进行猜测。

完成后的数据访问模型：

```text
User
  ↓
Agent
  ↓
ToolRegistry
  ↓
结构化 Database Query Tool
  ↓
AgentDatabaseReadService
  ↓
现有 Repository
  ↓
SQLAlchemy ORM / SELECT
  ↓
Current Database
  ↓
受限结构化 Output
  ↓
Agent Answer
```

本轮必须建立清晰的数据源职责：

```text
Conversation History
→ 对话连续性

Memory
→ 长期已治理上下文

search_social_evidence
→ 语义相关性 / Evidence discovery

Structured Database Tools
→ 当前持久化状态、精确数量、精确列表、当前对象状态

Evidence / Verification / Finding / Human Review
→ 事实结论与可信度治理
```

---

# 2. 已核验的当前仓库事实

执行智能体不得假设与当前实现不一致的架构。

当前代码已经具备：

```text
ToolSpec.permissions
ToolSpec.output_model
ToolSpec.cache_ttl_seconds
ToolSpec.max_concurrency
ToolRegistry.invoke_with_meta(...)
AgentDefinition.allowed_tools
AgentDefinition.permissions
RuntimeContext.case_id
```

ToolRegistry 对模型驱动 Tool Call 已执行：

```text
ToolSpec.permissions - AgentDefinition.permissions
```

缺失权限时返回：

```text
tool_permission_denied
```

当前 Coordinator 已拥有：

```text
read_database
write_database
```

六类专家 Agent 的公共读权限集合已经包含：

```text
read_database
```

因此：

> **本轮不新增权限字符串，不修改 ToolRegistry 权限核心机制。**

当前已有部分数据库相关 Tool：

```text
get_collection_run
search_social_evidence
query_claims
query_evidence
query_propagation
get_artifact
```

这些 Tool 必须继续保留并复用。

当前数据库访问边界已存在：

```text
ApplicationRepository
SocialRepository
CollectionRunRepository
FindingRepository
ReportDocumentRepository
```

本轮不得绕过这些 Repository 给 Agent 新建任意 SQL 通道。

---

# 3. 根因与本轮边界

当前问题不是：

```text
Agent 完全没有 read_database 权限
```

而是：

```text
Agent 缺少能够表达“当前数据库真实状态”的结构化 Tool Surface。
```

例如：

```text
search_social_evidence
```

属于语义检索 Tool。

它适合：

```text
“哪些内容与‘黑公关’相关？”
```

不适合：

```text
“当前数据库里知乎一共有多少条？”
```

因为语义检索：

```text
不是 complete list
不是 exact count
不是数据库 snapshot
```

---

# 4. 本轮明确不做

禁止新增：

```text
execute_sql
run_sql
query_sql
query_table
query_database(table_name=...)
insert_record
update_record
delete_record
database_shell
```

禁止让 LLM 控制：

```text
SQL 字符串
table name
column name
JOIN
WHERE SQL fragment
ORDER BY SQL fragment
```

禁止给 Agent：

```text
任意 INSERT
任意 UPDATE
任意 DELETE
```

本轮新增的 Database Tool：

> **全部只读。**

数据库写操作继续使用当前 Domain Tool / Service，例如：

```text
start_social_collection
write_case_memory
Review workflow
Finding workflow
Report workflow
```

不得通过“通用数据库 Tool”绕过 Domain 状态机。

---

# 5. 六条工程不变量

## DB-INV-1 — 当前状态必须查当前数据库

当用户问题包含以下语义：

```text
当前
现在
刚刚
已经采了多少
数据库里
有哪些记录
最新记录
某平台实际采到了什么
Finding 当前状态
Review 当前状态
Report 当前状态
```

Agent 必须优先使用结构化 DB Tool。

禁止直接依据：

```text
Conversation History
Memory
旧 Artifact
旧 Assistant 回答
```

回答当前数据库状态。

---

## DB-INV-2 — DB State 不等于 Real-world Truth

数据库中的 Source Post 可以证明：

```text
系统当前持久化了该内容
```

不能自动证明：

```text
该内容陈述的事实为真
```

例如数据库存在帖子：

```text
“华为要求全网停售竹知了”
```

只能说明：

```text
系统采集到了有人发布这种说法。
```

用户进一步询问：

```text
“这个说法是真的吗？”
```

必须进入：

```text
Evidence
Verification
Finding
Human Review
```

不能仅凭 SourcePost 判断。

---

## DB-INV-3 — 全部查询 Case-scoped

任何 Case 域 Tool：

```text
case_id
```

必须由：

```text
RuntimeContext.case_id
```

强制覆盖。

模型即使传入其它 Case：

```json
{"case_id":"another-case"}
```

也必须被 Runtime 改写成当前 Case。

---

## DB-INV-4 — Exact-ID 跨 Case 不泄漏

例如：

```text
get_social_post(post_id=<another-case-post>)
```

不得返回：

```text
“该 post 属于另一个 Case”
```

应表现为：

```json
{
  "ok": true,
  "found": false
}
```

Finding / Review / Report exact ID 同理。

---

## DB-INV-5 — Tool Output 必须白名单化

禁止直接返回：

```text
ORM __dict__
raw_payload
embedding
content_hash
数据库内部状态
Cookie
Token
Secret
Sandbox Environment
```

所有输出必须通过：

```text
Pydantic Output Model
```

验证。

---

## DB-INV-6 — 实时数据库 Tool 不缓存

本轮新增 Tool：

```text
cache_ttl_seconds = 0
```

因为：

```text
CollectionRun
```

可能持续增量写入数据库。

下一次 Tool Call 必须能够立即看到新数据。

---

# 6. 最终 Tool Pack

本轮新增且只新增以下 9 个结构化 DB Tool：

| 编号 | Tool | 用途 |
|---|---|---|
| DB01 | `get_case_data_overview` | 当前 Case 数据概况与精确数量 |
| DB02 | `query_social_posts` | 精确查询当前 Source Posts |
| DB03 | `get_social_post` | 通过稳定 ID 获取单条 Post |
| DB04 | `query_social_comments` | 查询当前 Source Comments |
| DB05 | `aggregate_social_data` | 平台/日期/内容类型精确聚合 |
| DB06 | `query_findings` | 查询当前 Finding 状态 |
| DB07 | `query_review_items` | 查询 Human Review 当前状态 |
| DB08 | `query_reports` | 查询当前 ReportDocument |
| DB09 | `query_case_activity` | 查询 Case Activity |

继续复用：

```text
get_collection_run
search_social_evidence
query_claims
query_evidence
query_propagation
get_artifact
```

不得重新创建功能重复 Tool。

---

# 7. Tool 选择规则

## 精确数据库状态

用户：

```text
“当前数据库一共多少帖子？”
```

调用：

```text
get_case_data_overview
```

---

用户：

```text
“知乎现在有哪些帖子？”
```

调用：

```text
query_social_posts(platforms=["zhihu"])
```

---

用户：

```text
“这条 Post 的原始持久化内容是什么？”
```

调用：

```text
get_social_post
```

---

用户：

```text
“这条 Post 的评论有哪些？”
```

调用：

```text
query_social_comments(post_id=...)
```

---

用户：

```text
“各平台分别采到了多少？”
```

调用：

```text
aggregate_social_data(group_by="platform")
```

---

用户：

```text
“目前有哪些 verified Findings？”
```

调用：

```text
query_findings(status="verified")
```

---

用户：

```text
“这个 Finding 审核过了吗？”
```

调用：

```text
query_review_items(object_type="finding", object_id=...)
```

---

用户：

```text
“这个 Case 有哪些已发布报告？”
```

调用：

```text
query_reports(status="published")
```

---

用户：

```text
“最近这个 Case 做了哪些操作？”
```

调用：

```text
query_case_activity
```

---

## 语义检索

用户：

```text
“哪些帖子能够支持‘舆情进入长尾传播’？”
```

仍然使用：

```text
search_social_evidence
```

---

## 事实判断

用户：

```text
“这些数据能证明幕后黑手存在吗？”
```

必须使用：

```text
search_social_evidence
query_claims
query_evidence
Verification
Finding
Review
```

不能只使用 DB01–DB05。

---

# 8. 新增 AgentDatabaseReadService

新增文件：

```text
backend/app/application/agent_database_service.py
```

固定依赖：

```python
class AgentDatabaseReadService:
    def __init__(
        self,
        *,
        repository: ApplicationRepository,
        social_repository: SocialRepository,
        collection_run_repository: CollectionRunRepository,
        finding_repository: FindingRepository,
        report_repository: ReportDocumentRepository,
    ) -> None:
        self._repository = repository
        self._social = social_repository
        self._collection_runs = collection_run_repository
        self._findings = finding_repository
        self._reports = report_repository
```

不要注入：

```text
Database
AsyncSession
session_factory
```

到该 Service。

职责固定：

```text
Case validation
Input normalization
Repository orchestration
Pagination
Cross-repository aggregation
Field whitelist serialization
Output bounding
```

禁止：

```text
AgentDatabaseReadService 自己直接 select(...)
Tool handler 自己打开 session
Tool handler 访问 ORM
```

---

# 9. 为什么直接依赖 Repository，而不是移动现有 Domain Service

当前：

```text
FindingService
ReportDocumentService
```

在 `ApplicationContainer` 中创建时间晚于：

```text
build_tool_registry(...)
```

本轮不要为了只读 Tool 大规模重排现有业务 Service 生命周期。

正确实现是：

在 `build_tool_registry(...)` 之前创建轻量、无状态的只读 Repository wrapper：

```python
self.finding_read_repository = FindingRepository(self.database)
self.report_read_repository = ReportDocumentRepository(self.database)

self.agent_database = AgentDatabaseReadService(
    repository=self.repository,
    social_repository=self.social,
    collection_run_repository=self.collection_run_repository,
    finding_repository=self.finding_read_repository,
    report_repository=self.report_read_repository,
)
```

然后：

```python
self.tools = build_tool_registry(
    ...,
    agent_database=self.agent_database,
)
```

后续现有：

```text
FindingService
ReportDocumentService
```

继续按当前顺序创建。

允许存在：

```text
FindingService 内自己的 FindingRepository
```

和：

```text
AgentDatabaseReadService 的 FindingRepository
```

两个 Repository 实例。

因为 Repository 是：

```text
无状态 Database wrapper
```

这比修改现有 FindingService 构造函数风险更低。

---

# 10. Repository 归属必须固定

执行智能体不得把所有查询都塞到 ApplicationRepository。

固定归属：

```text
Social Post / Comment / Social aggregate
→ SocialRepository

CollectionRun
→ CollectionRunRepository

Finding
→ FindingRepository

ReportDocument
→ ReportDocumentRepository

Case / Claim / Evidence / Artifact / Review / Activity
→ ApplicationRepository
```

`AgentDatabaseReadService` 只做 orchestration。

---

# 11. SocialRepository 必须扩展的只读能力

当前已有：

```text
persist_batch
list_posts_by_case
list_posts_page
list_post_time_rows
find_related_posts
```

本轮扩展现有 `list_posts_page`，不要创建功能重复方法。

建议最终签名：

```python
async def list_posts_page(
    self,
    case_id: str,
    *,
    platforms: list[str] | None = None,
    q: str | None = None,
    author: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort_order: Literal["newest", "oldest"] = "newest",
    limit: int = 50,
    offset: int = 0,
) -> Sequence[SourcePostRecord]:
    ...
```

如果需要兼容当前调用方的：

```text
platform: str | None
```

可以临时保留：

```python
platform: str | None = None
```

但 Service 必须 normalize：

```text
platform
或
platforms
```

为单一内部形式。

不要让两个过滤器产生矛盾。

---

# 12. SocialRepository 新增方法

必须新增：

```python
async def count_posts(
    self,
    case_id: str,
    *,
    platforms: list[str] | None = None,
    q: str | None = None,
    author: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> int:
    ...
```

```python
async def get_post_for_case(
    self,
    case_id: str,
    *,
    post_id: str | None = None,
    platform: str | None = None,
    native_id: str | None = None,
) -> SourcePostRecord | None:
    ...
```

```python
async def list_comments_page(
    self,
    case_id: str,
    *,
    post_id: str | None = None,
    platforms: list[str] | None = None,
    q: str | None = None,
    author: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort_order: Literal["newest", "oldest"] = "newest",
    limit: int = 50,
    offset: int = 0,
) -> Sequence[SourceCommentRecord]:
    ...
```

```python
async def count_comments(
    self,
    case_id: str,
    *,
    post_id: str | None = None,
    platforms: list[str] | None = None,
    q: str | None = None,
    author: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> int:
    ...
```

```python
async def count_posts_by_platform(
    self,
    case_id: str,
) -> list[tuple[str, int]]:
    ...
```

```python
async def count_posts_by_content_type(
    self,
    case_id: str,
    *,
    platforms: list[str] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[tuple[str, int]]:
    ...
```

---

# 13. Comment 查询的 Case Scope

`SourceCommentRecord` 没有独立 `case_id` 作为主要查询边界。

所有 comment 查询必须：

```text
SourceComment
JOIN SourcePost
ON SourceComment.post_id = SourcePost.id

WHERE SourcePost.case_id = :case_id
```

禁止：

```text
只按 comment.id / post_id 查询后直接返回
```

而不检查 Post 的 Case。

---

# 14. 日期聚合

当前已有：

```text
SocialRepository.list_post_time_rows(case_id)
```

并采用：

```text
Python 侧按天聚合
```

以避免：

```text
SQLite strftime
PostgreSQL date_trunc
```

双方言差异。

本轮 `aggregate_social_data(group_by="day")` 继续沿用这一原则。

如果 `aggregate_social_data` 支持额外过滤：

```text
platforms
query
date_from
date_to
```

则扩展轻量方法：

```python
list_post_time_rows(
    case_id,
    *,
    platforms=None,
    q=None,
    date_from=None,
    date_to=None,
)
```

而不是在 Agent Service 中重新查询 ORM。

---

# 15. ApplicationRepository 新增/扩展能力

本轮在 `ApplicationRepository` 中只补其现有 Domain 范围内的只读能力。

新增一个聚合读方法：

```python
async def get_case_database_counts(
    self,
    case_id: str,
) -> dict[str, int]:
    ...
```

只统计：

```text
claims
evidence
artifacts
review_items
review_decisions
```

不要统计：

```text
posts
comments
findings
reports
collection_runs
```

这些由各自 Repository 负责。

---

# 16. ReviewDecision Count 必须严格 Case-scoped

禁止：

```text
COUNT(review_decisions)
```

必须：

```text
ReviewDecision
JOIN ReviewItem
ON ReviewDecision.item_id = ReviewItem.id

WHERE ReviewItem.case_id = :case_id
```

否则 DB01 会泄漏其它 Case 的 Review 数据。

---

# 17. Review 查询扩展

扩展现有：

```text
ApplicationRepository.list_review_items(...)
```

使其至少支持：

```text
case_id
review_item_id
object_type
object_id
status
limit
offset
```

如果已有 exact object 方法：

```text
get_review_item_for_object
```

继续复用。

对 exact Review Item：

必须验证：

```text
review_item.case_id == runtime_case_id
```

---

# 18. Review Decision 查询

继续使用当前：

```text
ApplicationRepository.list_review_decisions(...)
```

或现有等价方法。

DB07 仅在：

```text
review_item_id
或
object_id
```

明确时返回：

```text
latest_decision
```

列表模式不要对 50 个 Review Item 分别做 N+1 decision 查询。

---

# 19. Activity 查询扩展

当前真实模型字段为：

```text
id
case_id
activity_type
summary
actor
ref_run_id
ref_tool_call_id
metadata_json
created_at
```

Tool 参数必须使用：

```text
activity_type
```

禁止使用此前错误的：

```text
event_type
```

扩展：

```text
ApplicationRepository.list_activity_log(...)
```

支持：

```text
activity_type
actor
limit
offset
```

不要创建第二个 Activity Repository。

---

# 20. FindingRepository 扩展

当前已有：

```text
get
list
list_evidence_links
list_source_links
```

扩展现有：

```python
FindingRepository.list(
    case_id,
    *,
    finding_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
)
```

`query` 只在：

```text
title
statement
```

做 lexical contains。

新增：

```python
async def count(
    case_id,
    *,
    kind=None,
    status=None,
    query=None,
) -> int:
    ...
```

禁止在 `AgentDatabaseReadService` 重新：

```python
select(FindingRecord)
```

---

# 21. ReportDocumentRepository 扩展

当前已有：

```text
get
list_for_case
list_global
latest_for_artifact
```

扩展：

```python
async def list_for_case(
    self,
    case_id: str,
    *,
    report_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[ReportDocumentRecord]:
    ...
```

新增：

```python
async def count_for_case(
    self,
    case_id: str,
    *,
    status: str | None = None,
) -> int:
    ...
```

禁止新建第二套 Report SELECT。

---

# 22. CollectionRunRepository

当前已经提供：

```text
get_for_case
list_for_case
list_active_for_case
```

全部直接复用。

只新增 Overview 真正需要的：

```python
async def count_for_case(
    self,
    case_id: str,
) -> int:
    ...
```

如果不希望新增 count 方法，可在 CollectionRunRepository 内实现：

```text
SELECT COUNT
```

但不得通过：

```text
list_for_case(limit=10000)
→ len()
```

获得数量。

Active CollectionRun：

必须直接：

```text
list_active_for_case
```

保持现有：

```text
queued / running
```

定义。

---

# 23. 新增 Tool 注册模块

新增：

```text
backend/app/harness/database_tools.py
```

该文件包含：

```text
Input Pydantic Models
Output Pydantic Models
Tool handlers
register_database_tools(...)
```

固定入口：

```python
def register_database_tools(
    registry: ToolRegistry,
    service: AgentDatabaseReadService,
) -> None:
    ...
```

不要继续把 9 个 Tool 的全部实现堆进：

```text
tool_factory.py
```

---

# 24. build_tool_registry 修改

修改：

```text
backend/app/harness/tool_factory.py
```

增加参数：

```python
def build_tool_registry(
    ...,
    agent_database: AgentDatabaseReadService | None = None,
) -> ToolRegistry:
```

在现有 Tool 注册完成过程中：

```python
if agent_database is not None:
    register_database_tools(registry, agent_database)
```

必须发生在：

```text
ApplicationContainer.skills.validate_tools(...)
```

之前。

---

# 25. ToolSpec 固定配置

DB01–DB09 全部统一：

```python
ToolSpec(
    name="...",
    version="1.0.0",
    description="...",
    input_model=...,
    handler=...,

    permissions=("read_database",),

    side_effect="none",
    idempotent=True,
    requires_approval=False,

    execution_mode="parallel",

    output_model=...,

    cache_ttl_seconds=0,
    max_concurrency=8,
    timeout_seconds=10,
    max_retries=0,

    execution_class="trusted_in_process",
    filesystem={},
    network={},
    secrets=(),
    risk_level="low",
)
```

不设置：

```text
rag_output=True
```

因为这些不是 RAG Tool。

---

# 26. DB01 — get_case_data_overview

## Input

```python
class CaseDataOverviewInput(BaseModel):
    case_id: str | None = None
```

`case_id` Runtime 注入。

---

# 27. DB01 — 精确输出

输出：

```json
{
  "ok": true,

  "case": {
    "id": "...",
    "title": "...",
    "topic": "...",
    "status": "...",
    "platforms": ["weibo", "zhihu"],
    "time_range": {
      "start": "...",
      "end": "..."
    }
  },

  "counts": {
    "posts": 87,
    "comments": 0,
    "collection_runs": 2,
    "claims": 5,
    "evidence": 16,
    "artifacts": 4,
    "findings": 3,
    "review_items": 2,
    "review_decisions": 1,
    "reports": 1
  },

  "posts_by_platform": [
    {
      "platform": "weibo",
      "count": 45
    },
    {
      "platform": "zhihu",
      "count": 42
    }
  ],

  "latest_post_published_at": "...",

  "active_collection_runs": [
    {
      "id": "...",
      "phase": "discovery",
      "status": "running",
      "posts_collected": 47,
      "comments_collected": 0,
      "updated_at": "..."
    }
  ]
}
```

如果 Case 没有数据：

```text
counts 全部为 0
posts_by_platform=[]
active_collection_runs=[]
```

这不是错误。

---

# 28. DB01 — Repository 调用固定

```text
Case
→ ApplicationRepository.get_case

posts/comments/posts_by_platform/latest post
→ SocialRepository

claims/evidence/artifacts/review items/review decisions
→ ApplicationRepository.get_case_database_counts

CollectionRun count + active
→ CollectionRunRepository

Finding count
→ FindingRepository.count

Report count
→ ReportDocumentRepository.count_for_case
```

禁止：

```text
load all rows
→ Python len()
```

做 exact count。

---

# 29. DB02 — query_social_posts

## Input

```python
class QuerySocialPostsInput(BaseModel):
    case_id: str | None = None

    platforms: list[str] | None = None

    query: str | None = Field(
        default=None,
        max_length=300,
    )

    author: str | None = Field(
        default=None,
        max_length=200,
    )

    date_from: datetime | None = None
    date_to: datetime | None = None

    sort_order: Literal["newest", "oldest"] = "newest"

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=5000)
```

`query` 是：

```text
lexical contains
```

不是 semantic search。

---

# 30. DB02 — 输出字段白名单

每条 Post：

```text
id
platform
native_id
content_type
title
content
author_id
author_name
source_url
published_at
engagement
```

禁止：

```text
raw_payload
content_hash
embedding
case_id
```

`case_id` 对 LLM 没必要重复返回。

---

# 31. DB02 — Output

```json
{
  "ok": true,
  "matched_count": 87,
  "returned_count": 20,
  "offset": 0,
  "next_offset": 20,
  "posts": [...]
}
```

如果：

```text
offset + returned_count >= matched_count
```

则：

```text
next_offset = null
```

---

# 32. DB02 — Content Bounding

列表查询：

```text
title <= 原字段
content <= 3000 chars
```

超过：

```json
{
  "content": "...",
  "content_truncated": true
}
```

不得因某一条超长文本耗尽 Agent Context。

---

# 33. DB03 — get_social_post

## Input

```python
class GetSocialPostInput(BaseModel):
    case_id: str | None = None

    post_id: str | None = None

    platform: str | None = None
    native_id: str | None = None

    include_comment_preview: bool = False

    comment_preview_limit: int = Field(
        default=5,
        ge=0,
        le=20,
    )
```

必须满足：

```text
post_id
```

或：

```text
platform + native_id
```

至少一种。

---

# 34. DB03 — Exact Scope

实现必须使用：

```text
SocialRepository.get_post_for_case
```

不能：

```text
session.get(SourcePostRecord, post_id)
→ 直接返回
```

其它 Case 的 ID：

```json
{
  "ok": true,
  "found": false,
  "post": null
}
```

---

# 35. DB03 — 输出

Post 允许比 DB02 更长：

```text
content <= 12000 chars
```

还返回：

```text
comment_count
```

只有：

```text
include_comment_preview=true
```

时返回最多 20 条 comment preview。

仍然禁止：

```text
raw_payload
```

---

# 36. DB04 — query_social_comments

## Input

```python
class QuerySocialCommentsInput(BaseModel):
    case_id: str | None = None

    post_id: str | None = None
    platforms: list[str] | None = None

    query: str | None = Field(
        default=None,
        max_length=300,
    )

    author: str | None = Field(
        default=None,
        max_length=200,
    )

    date_from: datetime | None = None
    date_to: datetime | None = None

    sort_order: Literal["newest", "oldest"] = "newest"

    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=5000)
```

---

# 37. DB04 — 输出

Comment 白名单：

```text
id
post_id
platform
native_id
parent_native_id
content
author_id
author_name
published_at
metrics
```

`content` 最大：

```text
2000 chars
```

输出：

```text
matched_count
returned_count
offset
next_offset
comments
```

---

# 38. DB05 — aggregate_social_data

第一版只支持稳定、解释清晰的 Post Count 聚合。

## Input

```python
class AggregateSocialDataInput(BaseModel):
    case_id: str | None = None

    group_by: Literal[
        "platform",
        "day",
        "content_type",
    ]

    platforms: list[str] | None = None

    query: str | None = Field(
        default=None,
        max_length=300,
    )

    date_from: datetime | None = None
    date_to: datetime | None = None

    limit: int = Field(default=50, ge=1, le=100)
```

---

# 39. DB05 — Output

```json
{
  "ok": true,
  "metric": "post_count",
  "group_by": "platform",
  "total": 87,
  "buckets": [
    {
      "key": "weibo",
      "count": 45
    },
    {
      "key": "zhihu",
      "count": 42
    }
  ]
}
```

不要在本轮增加：

```text
percentile
median engagement
复杂 JSON metric
window function
```

避免扩大双方言复杂度。

---

# 40. DB06 — query_findings

## Input

```python
class QueryFindingsInput(BaseModel):
    case_id: str | None = None

    finding_id: str | None = None

    kind: str | None = None
    status: str | None = None

    query: str | None = Field(
        default=None,
        max_length=300,
    )

    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=5000)
```

---

# 41. DB06 — Finding 输出

基础字段：

```text
id
kind
title
statement
status
confidence
attributes
source_run_id
created_at
updated_at
```

`attributes` 来自：

```text
FindingRecord.attributes_json
```

必须经过 Output Model / JSON size bounding。

---

# 42. DB06 — Exact Finding

如果：

```text
finding_id != null
```

必须：

```text
FindingRepository.get(finding_id)
→ 验证 finding.case_id == runtime_case_id
```

其它 Case：

```text
found=false
```

Exact 模式额外返回：

```text
evidence_links
source_links
```

来自：

```text
FindingRepository.list_evidence_links
FindingRepository.list_source_links
```

列表模式不要逐个查询 links，避免 N+1。

---

# 43. DB06 — Finding 状态语义

Tool description 和 Agent Prompt 必须明确：

```text
candidate
under_review
verified
rejected
superseded
```

语义不同。

禁止：

```text
candidate
→ Agent 表述为“已确认”
```

只有：

```text
verified
```

才是 Human Review 已接受的最终状态。

---

# 44. DB07 — query_review_items

## Input

```python
class QueryReviewItemsInput(BaseModel):
    case_id: str | None = None

    review_item_id: str | None = None

    object_type: str | None = None
    object_id: str | None = None

    status: str | None = None

    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=5000)
```

---

# 45. DB07 — ReviewItem 输出

当前真实字段：

```text
id
object_type
object_id
priority
status
risk_level
queue
current_version
summary
created_at
updated_at
```

这些字段可直接白名单返回。

---

# 46. DB07 — Latest Decision

只有 exact 模式：

```text
review_item_id
```

或：

```text
object_type + object_id
```

时附加：

```json
{
  "latest_decision": {
    "id": "...",
    "object_version": 2,
    "decision": "approved",
    "reason": "...",
    "actor": "...",
    "supersedes_id": null,
    "created_at": "..."
  }
}
```

来自现有：

```text
ReviewDecisionRecord
```

列表模式不要 N+1 读取 decision。

---

# 47. DB07 — 严格只读

DB07 禁止：

```text
claim
release
approve
reject
more_evidence
reopen
```

Review mutation 继续走现有 Review Service / Workbench。

---

# 48. DB08 — query_reports

## Input

```python
class QueryReportsInput(BaseModel):
    case_id: str | None = None

    report_id: str | None = None
    status: str | None = None

    include_content_preview: bool = False

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=5000)
```

---

# 49. DB08 — 输出

基础字段：

```text
id
family_id
source_artifact_id
supersedes_id
status
title
lock_version
published_at
created_at
updated_at
```

如果：

```text
include_content_preview=true
```

只返回：

```text
executive_summary
section_titles
citation_count
```

禁止默认把完整：

```text
content_json
```

全部塞给 LLM。

---

# 50. DB08 — Exact Scope

其它 Case report_id：

```text
found=false
```

禁止暴露：

```text
report_scope_mismatch
```

给模型作为跨 Case existence oracle。

---

# 51. DB09 — query_case_activity

## Input

```python
class QueryCaseActivityInput(BaseModel):
    case_id: str | None = None

    activity_type: str | None = None
    actor: str | None = Field(
        default=None,
        max_length=100,
    )

    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=5000)
```

---

# 52. DB09 — 输出字段

严格使用当前真实模型字段：

```text
id
activity_type
summary
actor
ref_run_id
ref_tool_call_id
created_at
```

禁止使用此前文档中错误的：

```text
event_type
object_type
object_id
```

因为：

```text
CaseActivityLogRecord
```

没有这些字段。

默认不返回：

```text
metadata_json
```

避免内部信息和不必要 Token 暴露。

---

# 53. Runtime Case Scope 注入

修改：

```text
backend/app/harness/runtime.py
```

不要继续维护越来越长的局部 inline set。

抽取模块级：

```python
_CASE_SCOPED_TOOLS = frozenset(
    {
        # existing
        "collect_social_posts",
        "start_social_collection",
        "get_collection_run",
        "search_social_evidence",
        "write_case_memory",
        "dispatch_expert",
        "get_artifact",
        "reconstruct_propagation",
        "verify_claims",
        "query_claims",
        "query_evidence",
        "query_propagation",

        # new DB tools
        "get_case_data_overview",
        "query_social_posts",
        "get_social_post",
        "query_social_comments",
        "aggregate_social_data",
        "query_findings",
        "query_review_items",
        "query_reports",
        "query_case_activity",
    }
)
```

执行：

```python
if call.name in _CASE_SCOPED_TOOLS:
    arguments["case_id"] = context.case_id
```

保持其它：

```text
run_id
turn_id
tool_call_id
approval_id
dispatch_key
```

现有注入逻辑不变。

---

# 54. Permission 规则

DB01–DB09：

```text
permissions=("read_database",)
```

不得：

```text
permissions=()
```

不得：

```text
permissions=("write_database",)
```

ToolRegistry 当前 permission 差集检查继续作为唯一权限执行点。

---

# 55. Agent Allowlist 最终矩阵

不要把全部 DB Tool 无差别给所有专家。

## Coordinator

增加全部 DB01–DB09：

```text
get_case_data_overview
query_social_posts
get_social_post
query_social_comments
aggregate_social_data
query_findings
query_review_items
query_reports
query_case_activity
```

Coordinator 是用户数据库状态问题的主要响应者。

---

## Opinion Agent

增加：

```text
get_case_data_overview
query_social_posts
get_social_post
query_social_comments
aggregate_social_data
```

用途：

```text
平台分布
时间趋势
当前真实 Post / Comment
```

---

## Propagation Agent

增加：

```text
query_social_posts
get_social_post
query_social_comments
```

不要增加：

```text
query_reports
query_review_items
query_case_activity
```

---

## Verification Agent

增加：

```text
query_social_posts
get_social_post
query_social_comments
```

继续使用已有：

```text
search_social_evidence
query_claims
query_evidence
```

---

## Evidence Critic

增加：

```text
get_social_post
query_findings
```

继续：

```text
query_claims
query_evidence
get_artifact
```

---

## Report Agent

增加：

```text
get_case_data_overview
query_findings
```

报告生成需要：

```text
覆盖概况
verified Findings
```

不需要：

```text
query_reports
query_case_activity
```

---

## Citation Validator

增加：

```text
get_social_post
query_findings
```

继续：

```text
query_claims
query_evidence
query_propagation
get_artifact
```

---

# 56. 不修改 Agent Permissions

当前专家已经有：

```text
read_database
```

因此：

```text
只修改 allowed_tools
```

不要给专家新增：

```text
write_database
```

Coordinator 现有权限保持，不扩大。

---

# 57. Coordinator 数据源优先级规则

修改：

```text
COORDINATOR_INSTRUCTIONS
```

加入以下规则：

```text
【当前持久化状态】

当用户询问当前 Case 已经持久化的数据、精确数量、精确记录列表、
最新记录、平台分布、Finding/Review/Report 当前状态时，
必须优先调用结构化数据库查询工具。

Conversation History、Memory、旧 Artifact、先前 Assistant 回答
不能替代当前数据库查询。

search_social_evidence 用于语义相关性与 Evidence discovery，
不得作为数据库 exact count 或 complete list 的权威来源。

若数据库返回 0 条，必须以当前数据库结果为准；
不得因为历史对话中曾经出现这些数据就声称它们当前仍存在。

如果当前 DB 与历史回答冲突，以当前数据库为准。

数据库中存在某条 Social Post 只代表系统持久化了该内容，
不代表该 Post 陈述的事实已经被证明。
```

---

# 58. Expert 通用规则

对获得 DB Tool 的 Expert Instructions 加入简短统一规则：

```text
Exact count / exact list / current state
→ structured DB tools

Semantic relevance
→ search_social_evidence

Truth / verification
→ Evidence / Verification / Finding / Review
```

不要新增复杂 Skill 作为本轮必要依赖。

---

# 59. 本轮不新增 database-query Skill

此前方案建议：

```text
backend/skills/database-query/SKILL.md
```

本 V2 明确：

> **本轮不要求新增该 Skill。**

原因：

```text
不同 Expert 的 DB Tool allowlist 不同。
```

把全部 DB Tool 放进一个 Skill dependency 容易造成：

```text
Skill dependency
vs
Agent allowed_tools
```

语义混乱。

本轮直接使用：

```text
Agent Instructions
+
精确 Tool descriptions
```

完成路由即可。

未来若工具体系进一步扩大再单独抽 Skill。

---

# 60. Tool Description 必须具有路由意义

## get_case_data_overview

Description 必须明确：

> Use for exact current persisted counts, platform totals, and active collection status of the current case. This is the authoritative tool for “how many records are in the database now”. Do not infer exact counts from semantic search or conversation history.

---

## query_social_posts

> Query persisted source posts using deterministic database filters. Use for exact lists/latest posts/platform-specific records. `query` is lexical substring matching, not semantic retrieval.

---

## search_social_evidence

保留现有 Tool，但 Description 必须补一句：

> Do not use this tool as the authoritative source for exact database counts or complete record lists.

---

# 61. Tool Handler 规范

所有 DB Tool handler 结构统一：

```python
async def handler(arguments: BaseModel) -> dict[str, Any]:
    request = XxxInput.model_validate(arguments)

    if service is None or not request.case_id:
        return {
            "ok": False,
            "error": {
                "code": "database_query_unavailable",
                "message": "...",
            },
        }

    return await service.some_method(...)
```

禁止：

```text
handler 内直接 session
handler 内直接 ORM
handler 内拼 SQL
```

---

# 62. Error Contract

统一 Tool Error：

```json
{
  "ok": false,
  "error": {
    "code": "...",
    "message": "..."
  }
}
```

建议：

```text
database_query_invalid
database_query_unavailable
database_record_not_found
database_query_limit_exceeded
```

空列表：

```json
{
  "ok": true,
  "matched_count": 0,
  "items": []
}
```

不是 Error。

---

# 63. Exact Record 不存在

例如：

```text
post_id
finding_id
review_item_id
report_id
```

找不到或属于其它 Case：

```json
{
  "ok": true,
  "found": false
}
```

避免 Cross-case existence leak。

---

# 64. 数据输出大小限制

全局：

```text
默认 limit 20–30
最大 limit 100
最大 offset 5000
```

禁止：

```text
SELECT 全部几千条
→ 一次返回给模型
```

---

# 65. Raw Payload 禁止输出

DB02–DB04：

```text
raw_payload
```

永远不返回。

原因：

```text
体积大
第三方结构不稳定
Prompt Injection Surface 更大
可能携带不必要字段
```

如果以后确实需要：

```text
单独设计高权限 Raw Tool
```

不属于本轮。

---

# 66. SQL 安全

所有新增 Repository 查询必须使用：

```text
SQLAlchemy expression
bind parameter
typed filter
Literal sort
```

禁止：

```python
text(f"...{request.query}...")
```

禁止动态用户输入成为：

```text
column/table/order expression
```

---

# 67. Tool Output Security

DB Tool 虽然是：

```text
trusted_in_process
```

但 Post / Comment 内容仍属于外部不可信文本。

所有 Tool 调用必须继续经过：

```text
ToolRegistry.invoke_with_meta
```

从而保留现有：

```text
Tool Output Content Security Guardrail
```

禁止 Runtime 绕过 ToolRegistry 直接：

```text
service.query(...)
→ 塞入 LLM
```

---

# 68. Cache

DB01–DB09：

```text
cache_ttl_seconds=0
```

测试必须证明：

```text
Query 1 → 10 posts
DB 新增 1 post
Query 2 → 11 posts
```

不返回旧结果。

---

# 69. No Migration 原则

本轮正常情况下：

```text
不修改 models.py
不新增表
不新增列
不新增 migration
```

因为当前问题是：

```text
缺少 Agent read surface
```

不是 Schema 缺失。

只有真实 profiling 证明特定 Query 缺少必要 Index 且明显不可接受时，才单独增加 index migration。

不得预防性给大量表加索引。

---

# 70. Bootstrap 最终修改

在：

```text
backend/app/bootstrap.py
```

现有：

```text
collection_run_repository / collection_run_service
```

创建后、`build_tool_registry(...)` 之前加入：

```python
self.finding_read_repository = FindingRepository(self.database)
self.report_read_repository = ReportDocumentRepository(self.database)

self.agent_database = AgentDatabaseReadService(
    repository=self.repository,
    social_repository=self.social,
    collection_run_repository=self.collection_run_repository,
    finding_repository=self.finding_read_repository,
    report_repository=self.report_read_repository,
)
```

然后：

```python
self.tools = build_tool_registry(
    self.crawler,
    self.skills,
    self.knowledge,
    self.embeddings,
    self.social,
    self.repository,
    self.sentiment,
    self.llm,
    security=self.content_security,
    governance=self.memory_governance,
    collection_service=self.collection_service,
    collection_run_service=self.collection_run_service,
    agent_database=self.agent_database,
)
```

现有后续：

```text
FindingService
ReportDocumentService
```

创建顺序不要求改变。

---

# 71. Imports

Bootstrap 新增：

```python
from app.application.agent_database_service import AgentDatabaseReadService
from app.infrastructure.database.finding_repository import FindingRepository
from app.infrastructure.database.report_repository import ReportDocumentRepository
```

Tool Factory 新增：

```python
from app.application.agent_database_service import AgentDatabaseReadService
from app.harness.database_tools import register_database_tools
```

---

# 72. 预计修改文件

必须修改/新增：

```text
backend/app/application/agent_database_service.py        NEW

backend/app/harness/database_tools.py                    NEW

backend/app/harness/tool_factory.py
backend/app/harness/runtime.py
backend/app/harness/agents.py

backend/app/bootstrap.py

backend/app/infrastructure/database/social_repository.py
backend/app/infrastructure/database/finding_repository.py
backend/app/infrastructure/database/report_repository.py

backend/app/application/repositories.py
```

测试：

```text
backend/tests/test_agent_database_service.py             NEW
backend/tests/test_agent_database_tools.py               NEW

backend/tests/test_social_repository.py
backend/tests/test_tool_system.py
backend/tests/test_agent_runtime.py
backend/tests/test_expert_agents.py
```

通常无需修改：

```text
models.py
engine.py
migrations/
Finding state machine
Review mutation
Report publish gate
CollectionRun state machine
ToolRegistry permission core
```

---

# 73. 实施阶段 DBT0 — Baseline

修改代码前记录：

```text
git rev-parse HEAD
git status
```

记录当前：

```text
Coordinator allowed_tools
6 类 Expert allowed_tools
permissions
当前 ToolRegistry names
```

构造一个确定性复现：

```text
Conversation History：
Assistant 曾回答“知乎有 10 条”

当前 DB：
知乎有 25 条

User：
“现在数据库里知乎有多少条？”
```

确认旧代码存在：

```text
不查当前 DB / 回答旧数字
```

或至少缺少结构化查询路径。

把结果写入：

```text
docs/agent-database-tools-delivery.md
```

Baseline 部分。

---

# 74. DBT1 — 先冻结 Tool Contract

实现 SQL 前先写：

```text
DB01–DB09 Input Models
DB01–DB09 Output Models
字段白名单
limit
offset
exact/list 模式
```

并先完成：

```text
test_agent_database_tools.py
```

中的 Schema Contract Test。

禁止：

```text
先写 Repository
后根据实现随意改变 Tool Schema
```

---

# 75. DBT2 — Repository 扩展

按固定顺序：

```text
SocialRepository
→ Post / Comment / Aggregate

ApplicationRepository
→ Case aggregate counts / Review / Activity

FindingRepository
→ list query/offset/count

ReportDocumentRepository
→ status/offset/count

CollectionRunRepository
→ count_for_case
```

所有 Repository Test 先通过。

---

# 76. DBT3 — AgentDatabaseReadService

实现以下方法：

```text
get_case_data_overview
query_social_posts
get_social_post
query_social_comments
aggregate_social_data
query_findings
query_review_items
query_reports
query_case_activity
```

Service Test 不涉及 LLM。

---

# 77. DBT4 — database_tools.py

实现：

```text
Input Models
Output Models
Tool handlers
register_database_tools
```

确认全部 ToolSpec：

```text
read_database
side_effect none
cache 0
no approval
output model
```

---

# 78. DBT5 — Bootstrap / Tool Factory

完成：

```text
AgentDatabaseReadService wiring
Finding read Repository
Report read Repository
build_tool_registry parameter
register_database_tools
```

启动应用。

验证：

```text
skills.validate_tools
```

仍通过。

---

# 79. DBT6 — Runtime Case Scope

抽取：

```text
_CASE_SCOPED_TOOLS
```

加入所有新 Tool。

测试模型伪造：

```text
another case_id
```

被覆盖。

---

# 80. DBT7 — Agent Allowlists

严格按第 55 节矩阵更新。

禁止：

```text
所有 Expert 一次性加入全部 9 个 DB Tool。
```

完成：

```text
test_expert_agents.py
```

---

# 81. DBT8 — Agent Tool-Usage Guidance

本阶段不仅修改 Agent Instructions，还必须同步完成：

```text
Agent system instructions
ToolSpec.description
Pydantic Field.description
Runtime-injected parameter guidance
Tool-routing regression tests
```

目标是让模型明确知道：

```text
什么时候调用哪个数据库 Tool
什么时候不应该调用数据库 Tool
每个 Tool 需要什么参数
哪些参数由 Runtime 注入
数据库查询与 RAG / History / Verification 的优先级
```

禁止仅仅：

```text
把 Tool 名加入 allowed_tools
```

就认为 Agent 已经能够正确使用它们。

---

## 81.1 Coordinator Instructions

修改：

```text
COORDINATOR_INSTRUCTIONS
```

必须加入“当前持久化状态路由规则”。

至少表达：

```text
当用户询问当前 Case 中已经持久化的数据、精确数量、记录列表、
最新记录、平台分布、Finding / Review / Report 当前状态时，
必须优先使用结构化数据库查询工具。

Conversation History、Memory、旧 Artifact 和先前 Assistant 回答
不能代替当前数据库查询。

search_social_evidence 用于语义相关性与 Evidence discovery，
不得作为数据库 exact count 或 complete list 的权威来源。

如果当前数据库结果与历史对话冲突，以当前数据库为准。

数据库中的 Social Post 只证明系统持久化了该内容，
不代表该 Post 中的事实主张已经得到验证。

对于事实真假、可信度和证据充分性问题，
必须进入 Evidence / Verification / Finding / Human Review 流程。
```

---

## 81.2 Expert Instructions

只给拥有对应 DB Tool 的 Expert 增加最小必要规则。

### Opinion Agent

增加：

```text
精确帖子数量、平台分布、当前帖子列表、时间范围内当前持久化数据
→ structured DB tools

语义相关观点 / Evidence
→ search_social_evidence

统计结论仍必须来自 analyze_opinion 或数据库聚合结果，
不得从模型记忆编造。
```

### Propagation Agent

增加：

```text
需要确认当前数据库中实际存在的 Post / Comment 时，
使用 query_social_posts / get_social_post / query_social_comments。

传播关系判断仍以 reconstruct_propagation / query_propagation
以及真实 Post / Comment 关系为准。
```

### Verification Agent

增加：

```text
query_social_posts / get_social_post / query_social_comments
只能证明数据库中实际存在这些内容。

它们不能直接证明内容陈述为真。

事实判断仍必须依赖：
search_social_evidence
query_claims
query_evidence
verify_claims
```

### Evidence Critic

增加：

```text
get_social_post 用于核验被引用帖子是否真实存在。
query_findings 用于确认 Finding 当前状态。

不得因为数据库中存在一条帖子就判断证据充分。
```

### Report Agent

增加：

```text
当前 Case 数据覆盖与精确数量
→ get_case_data_overview

当前 Findings
→ query_findings

正式结论仍只能建立在已治理 Evidence / Finding 上。
```

### Citation Validator

增加：

```text
get_social_post 用于精确核验 Post ID。
query_findings 用于精确核验 Finding ID / status。

引用是否真正支持结论仍需结合 Evidence / Artifact，
不能只做“ID 存在性”检查。
```

不要改变上述专家原有业务职责和输出 JSON Contract。

---

## 81.3 ToolSpec.description 必须承担 Tool 路由职责

DB01–DB09 的 `ToolSpec.description` 不能只写：

```text
“Query database”
```

必须明确：

```text
适用问题
不适用问题
与相邻 Tool 的区别
```

例如：

### get_case_data_overview

建议 description：

```text
Use this tool for authoritative current persisted counts, per-platform totals,
case-level data coverage, and active collection status. Use it when the user asks
“how many records are in the database now”, “how many posts were collected”, or
“what is the current persisted case state”. Do not infer exact counts from
conversation history, memory, or semantic search.
```

### query_social_posts

建议 description：

```text
Query the current case's persisted Source Posts using deterministic database
filters such as platform, lexical text match, author, date range, and sort order.
Use it for exact record lists, latest posts, and platform-specific data.
The query parameter is lexical substring matching, not semantic retrieval.
Use search_social_evidence instead for semantic evidence discovery.
```

### get_social_post

建议 description：

```text
Fetch one exact persisted Source Post by stable post_id or platform + native_id.
Use when the user refers to a specific post or when another tool returns a Post ID
that must be inspected precisely. This tool does not validate whether claims inside
the post are true.
```

### query_social_comments

建议 description：

```text
Query persisted comments for the current case using exact database filters.
Use for comment lists, comment text, or comments attached to a known Post.
Do not use it as a semantic evidence search engine.
```

### aggregate_social_data

建议 description：

```text
Compute exact deterministic post-count aggregations over current persisted social
data, grouped by platform, day, or content type. Use for questions such as
“how many posts are on each platform”. Do not estimate counts from sampled search
results.
```

### query_findings

建议 description：

```text
Query persisted Findings and their current workflow status. Use for questions about
candidate / under_review / verified / rejected findings. Only verified findings
represent Human-Review-accepted conclusions.
```

### query_review_items

建议 description：

```text
Query the current Human Review state for case-scoped objects such as Findings.
Use when the user asks whether an object has been reviewed, approved, rejected,
or what review version/status it is currently in. This tool is read-only.
```

### query_reports

建议 description：

```text
Query ReportDocument records and their current status for the case.
Use for exact report lists, publication status, or report identity.
Do not use this tool to regenerate or modify reports.
```

### query_case_activity

建议 description：

```text
Query the current case activity log using deterministic filters such as
activity_type and actor. Use when the user asks what operations recently occurred
in the case. This tool exposes only a bounded safe activity summary.
```

---

## 81.4 Pydantic Input Fields 必须带参数说明

对模型不直观的字段必须设置：

```python
Field(description="...")
```

尤其：

```text
platforms
query
author
date_from
date_to
sort_order
limit
offset
group_by
finding_id
review_item_id
object_type
object_id
report_id
activity_type
include_comment_preview
include_content_preview
```

例如：

```python
query: str | None = Field(
    default=None,
    max_length=300,
    description=(
        "Optional lexical substring filter over persisted post text. "
        "This is deterministic database filtering, not semantic search."
    ),
)
```

```python
platforms: list[str] | None = Field(
    default=None,
    description=(
        "Optional platform filters such as weibo, bilibili, douyin, zhihu, or tieba. "
        "Omit to query all platforms available in the current case."
    ),
)
```

```python
limit: int = Field(
    default=20,
    ge=1,
    le=100,
    description="Maximum number of records to return in this call.",
)
```

---

## 81.5 Runtime-injected 参数说明

以下字段：

```text
case_id
run_id
turn_id
tool_call_id
approval_id
```

若出现在 Input Model 中，必须在代码注释 / description 中明确：

```text
Injected by runtime; never model-controlled.
```

其中本轮 DB01–DB09 核心只需要：

```text
case_id
```

模型不得通过 Prompt 被要求“自己找到 case_id”。

---

## 81.6 Tool 使用参数示例

测试与开发文档中至少保留下列标准调用语义。

### 当前总体数量

```json
{
  "tool": "get_case_data_overview",
  "arguments": {}
}
```

`case_id` Runtime 注入。

### 知乎最新帖子

```json
{
  "tool": "query_social_posts",
  "arguments": {
    "platforms": ["zhihu"],
    "sort_order": "newest",
    "limit": 10
  }
}
```

### 字符串精确过滤

```json
{
  "tool": "query_social_posts",
  "arguments": {
    "query": "竹知了",
    "limit": 20
  }
}
```

注意：

```text
query="竹知了"
```

表示 lexical database filtering。

它不是：

```text
semantic similarity
```

### 各平台数量

```json
{
  "tool": "aggregate_social_data",
  "arguments": {
    "group_by": "platform"
  }
}
```

### 当前 verified Findings

```json
{
  "tool": "query_findings",
  "arguments": {
    "status": "verified"
  }
}
```

### 某 Finding Review

```json
{
  "tool": "query_review_items",
  "arguments": {
    "object_type": "finding",
    "object_id": "<finding-id>"
  }
}
```

---

## 81.7 Tool 路由优先级固定

Agent 必须遵循：

```text
问题类型 1：
当前数量 / 当前列表 / 最新记录 / 当前状态
→ Structured DB Tool

问题类型 2：
“哪些内容与 X 相关？”
→ search_social_evidence

问题类型 3：
“X 是否是真的？”
→ Evidence / Verification / Finding / Review

问题类型 4：
“之前我们讨论过什么？”
→ Conversation History / Memory
```

禁止将四种来源混成一个统一 fallback。

---

## 81.8 Tool 路由失败原则

如果用户明确问：

```text
“当前数据库……”
```

但 Agent 没有调用结构化 DB Tool：

```text
即使最终数字碰巧正确，也视为行为失败。
```

因为本轮目标不仅是答案正确，还要保证：

```text
答案来源正确、可追踪、可重复。
```

---

# 82. DBT9 — Tool Permission / Security Test

验证：

```text
read_database required
no approval
no write_database
no raw SQL
runtime case scope
output model
no cache
output security pipeline
```

---

# 83. DBT10 — Agent Routing Regression

必须实现 deterministic Agent Runtime Tests：

```text
History vs DB
DB empty
Collection incremental freshness
Exact count vs RAG
Semantic evidence vs DB
```

---

# 84. DBT11 — Adjacent Regression

运行当前相关测试：

```text
Tool System
Agent Runtime
Expert Agents
Social Repository
Knowledge / RAG
CollectionRun
Claims
Evidence
Propagation
Findings
Review Reads
Report Reads
```

文件名按当前仓库实际测试文件为准。

---

# 85. DBT12 — 真实 Case 验证

使用当前真实 Investigation。

推荐：

```text
华为竹知了 Case
```

逐个提问：

```text
Q1 现在数据库总共有多少帖子？

Q2 各平台分别多少？

Q3 知乎现在有哪些帖子？

Q4 最新十条是什么？

Q5 某条具体 Post 的内容是什么？

Q6 当前有哪些 Findings？

Q7 哪些 Findings 已 verified？

Q8 某 Finding 当前 Review 状态是什么？

Q9 当前有哪些 Reports？

Q10 最近 Case Activity 有什么？
```

逐个检查：

```text
ToolCallRecord.tool_name
```

确认 Agent 使用正确 DB Tool。

---

# 86. Repository Tests

必须覆盖：

```text
R01 count_posts current case only

R02 posts multi-platform filter

R03 posts lexical query

R04 posts author filter

R05 posts date range

R06 posts sort newest / oldest

R07 posts exact lookup case scoped

R08 comments query joins SourcePost case scope

R09 comments foreign post cannot leak

R10 post pagination deterministic

R11 comment pagination deterministic

R12 post count by platform

R13 post aggregate by day

R14 content_type aggregate

R15 empty result

R16 get_case_database_counts

R17 review_decisions count joins ReviewItem case scope

R18 FindingRepository query/offset/count

R19 ReportDocumentRepository status/offset/count

R20 CollectionRunRepository count_for_case
```

---

# 87. Tool Contract Tests

必须覆盖：

```text
T01 DB01–DB09 registered

T02 permissions == ("read_database",)

T03 side_effect == "none"

T04 idempotent == True

T05 requires_approval == False

T06 cache_ttl_seconds == 0

T07 output_model exists

T08 execution_class == trusted_in_process

T09 limit > 100 rejected

T10 offset > 5000 rejected

T11 query > max length rejected

T12 output does not contain raw_payload

T13 output does not contain embedding

T14 output does not contain content_hash

T15 every DB Tool has non-empty routing-oriented description

T16 query_social_posts description distinguishes lexical DB filtering from semantic search

T17 get_case_data_overview description explicitly identifies exact current counts

T18 non-obvious Input fields have Field.description

T19 runtime-injected case_id is documented as runtime-controlled
```

---

# 88. Permission Tests

必须覆盖：

```text
P01 Coordinator can call new DB Tool

P02 allowed Expert with read_database can call

P03 Agent missing read_database
→ tool_permission_denied

P04 Tool absent from allowed_tools
→ tool_not_allowed

P05 no new DB Tool requires write_database

P06 no DB Tool bypasses ToolRegistry.invoke_with_meta
```

---

# 89. Runtime Scope Tests

必须覆盖：

```text
S01 model passes foreign case_id
→ runtime overwrites

S02 get_social_post foreign ID
→ found=false

S03 query_social_comments foreign post
→ no result

S04 query_findings foreign finding_id
→ found=false

S05 query_review_items foreign review_item_id
→ found=false

S06 query_reports foreign report_id
→ found=false

S07 DB01 comment count excludes other Case

S08 DB01 review decision count excludes other Case
```

---

# 90. Freshness Test

P0 Test：

```text
1. DB contains 10 posts
2. get_case_data_overview → 10
3. persist one new SourcePost
4. immediately call get_case_data_overview
5. result → 11
```

证明：

```text
cache=0
```

且 Service 查询的是当前 DB。

---

# 91. History-vs-DB P0 Regression

构造：

```text
History:
Assistant:
“知乎目前有 10 条。”

Current DB:
25 条

User:
“现在数据库里知乎有多少条？”
```

Agent 必须产生：

```text
get_case_data_overview
或
aggregate_social_data
```

最终回答：

```text
25
```

如果 Agent：

```text
不调用 DB Tool
```

即使恰好猜对：

```text
测试仍失败。
```

---

# 92. DB-empty P0 Regression

History：

```text
曾讨论 20 条微博
```

当前数据库：

```text
0 条
```

用户：

```text
“当前数据库有微博数据吗？”
```

Agent：

```text
DB Tool
→ 0
```

必须回答：

```text
当前数据库没有匹配记录。
```

不得 History fallback。

---

# 93. CollectionRun 增量 Freshness

CollectionRun：

```text
running
```

第一次：

```text
posts=20
```

用户问：

```text
现在采到多少？
```

Agent：

```text
DB01
→ 20
```

平台完成后 DB：

```text
47
```

下一次询问：

```text
DB01
→ 47
```

不能仍返回：

```text
20
```

---

# 94. Search-vs-DB Routing Test

用户：

```text
“数据库当前有多少条包含‘竹知了’的帖子？”
```

预期：

```text
query_social_posts + matched_count
或
aggregate_social_data（如果聚合接口支持同等过滤）
```

禁止只调用：

```text
search_social_evidence
```

---

用户：

```text
“知乎最新十条是什么？”
```

预期：

```text
query_social_posts(
    platforms=["zhihu"],
    sort_order="newest",
    limit=10
)
```

禁止：

```text
search_social_evidence(query="知乎 最新")
```

---

用户：

```text
“哪些内容支持‘事件进入长尾传播’？”
```

预期：

```text
search_social_evidence
```

而不是认为所有 lexical DB rows 都是 Evidence。

---

用户：

```text
“数据库里有人说华为要求停售竹知了，这是真的吗？”
```

预期调用阶段：

```text
第一步：
query_social_posts
→ 确认系统是否实际持久化了该说法

第二步：
search_social_evidence / query_claims / query_evidence / verify_claims
→ 判断说法真实性
```

禁止：

```text
只因为 query_social_posts 找到一条 Post
→ 回答“是真的”
```

---

# 95. Finding 状态测试

DB 中：

```text
F1 candidate
F2 under_review
F3 verified
F4 rejected
```

用户：

```text
“目前有哪些已验证结论？”
```

Agent 必须：

```text
query_findings(status="verified")
```

只回答：

```text
F3
```

---

# 96. Review 状态测试

Finding：

```text
under_review
```

ReviewItem：

```text
in_review
current_version=3
```

用户：

```text
“这个 Finding 审核到哪一步了？”
```

必须：

```text
query_review_items(object_type="finding", object_id=...)
```

回答基于：

```text
ReviewItem
```

不是 History。

---

# 97. No-Arbitrary-SQL Architecture Test

增加测试：

```python
forbidden = {
    "execute_sql",
    "run_sql",
    "query_sql",
    "query_table",
    "update_record",
    "delete_record",
}

assert not (forbidden & registry.names())
```

并人工 code search：

```text
Database Tool Input Model
```

中不得出现：

```text
table_name
column_name
sql
where_clause
order_clause
```

---

# 98. SQLite / PostgreSQL

SQLite：

```text
Mandatory
```

必须覆盖全部新 Repository Query。

PostgreSQL：

如果：

```text
TEST_POSTGRES_URL
```

可用：

运行核心集成：

```text
posts
comments
overview counts
finding
review
report
activity
```

如果不可用：

```text
明确 skip
```

不得写：

```text
PostgreSQL PASS
```

---

# 99. 性能要求

本轮不是性能项目，但查询不能明显低效。

本地正常 Case 观察目标：

```text
get_case_data_overview < 500ms
query_social_posts(limit=20) < 500ms
aggregate_social_data(group_by=platform) < 500ms
```

不作为跨环境 CI 硬断言。

若明显超过：

```text
先 profiling / EXPLAIN
```

只有确认索引问题后再新增 migration。

---

# 100. Telemetry

建议增加：

```text
agent_db.query_count
agent_db.query_latency_ms
agent_db.rows_returned
agent_db.empty_result
```

Labels：

```text
tool
agent
```

禁止把：

```text
query text
post content
author name
```

作为 Metrics Label。

---

# 101. Tool 审计

所有新 Tool 通过现有 Runtime：

```text
tool_execution_start
ToolCallRecord
tool_execution_end
```

进行审计。

真实 Case 验收时必须查看：

```text
ToolCallRecord.tool_name
```

以证明 Agent 确实查了数据库。

---

# 102. 默认不新增前端功能

本轮核心是：

```text
Agent Tool Surface
```

不要求新增数据库浏览器页面。

现有：

```text
Run / Tool Trace
```

能够显示 Tool Call 即可。

如果 Tool Result 在 Trace 中过长：

沿用当前 Tool summary / truncation 机制，不额外开发前端。

---

# 103. 默认不运行 Full Backend Regression

如果最终 diff 仅限：

```text
AgentDatabaseReadService
database_tools
只读 Repository 方法
Tool Factory wiring
Runtime Case Tool set
Agent allowlists / instructions
```

采用：

```text
Targeted + Adjacent Regression
```

即可。

---

# 104. Full Regression 升级条件

以下任一发生必须升级：

```text
修改 engine.py
修改 session_factory
修改 ToolRegistry permission 核心语义
修改 ToolRegistry invoke 核心流程
修改数据库 transaction 全局语义
修改 Finding mutation / 状态机
修改 Review mutation
修改 Report publish gate
修改 CollectionRun 状态机
新增数据库通用写 Tool
Repository 变更影响现有写方法
出现跨域不明失败
```

---

# 105. 本轮禁止实现方式

执行智能体不得：

```text
给 Agent 任意 SQL

给 Agent table_name / column_name

一次暴露 81 张表

新增 generic DB write Tool

让 DB Tool 直接打开 AsyncSession

在 Tool handler 重写 Repository SQL

返回 raw_payload

返回 ORM __dict__

返回 embedding

给所有专家加入所有 DB Tool

给 Expert 增加 write_database

给实时 DB Tool 设置 TTL cache

让 DB 空结果 fallback 到 History

把数据库中的用户帖子直接当成已验证事实

复制已有 query_claims/query_evidence/query_propagation/get_artifact/get_collection_run
```

---

# 106. 推荐 Commit 划分

## Commit 1

```text
feat: add case-scoped agent database read service
```

包括：

```text
Repository read extensions
AgentDatabaseReadService
Service tests
```

---

## Commit 2

```text
feat: add structured database query tools
```

包括：

```text
database_tools.py
ToolSpec
Tool Factory
Runtime Case scope
Permission tests
```

---

## Commit 3

```text
feat: route agents to current persisted database state
```

包括：

```text
Coordinator allowlist
Expert allowlists
Agent Instructions
History-vs-DB routing tests
```

---

## Commit 4

```text
docs: finalize agent database tool delivery
```

---

# 107. 最终实施顺序

严格执行：

```text
DBT0 Baseline / reproduction

DBT1 Freeze DB01–DB09 contracts

DBT2 Extend existing repositories

DBT3 Implement AgentDatabaseReadService

DBT4 Implement database_tools.py

DBT5 Bootstrap + Tool Factory wiring

DBT6 Runtime Case Scope

DBT7 Agent allowlists

DBT8 Agent Tool-Usage Guidance / Prompt / Tool descriptions / parameter guidance

DBT9 Repository + Service tests

DBT10 Tool / Permission / Scope tests

DBT11 Agent routing P0 regression

DBT12 Adjacent regression

DBT13 Real Case verification

DBT14 Delivery documentation
```

不得跳过：

```text
DBT11
```

直接以“Tool 能调用”为完成。

---

# 108. Delivery 文档

新增：

```text
docs/agent-database-tools-delivery.md
```

必须包含：

```text
Baseline HEAD
Final HEAD

New Tools

Tool → Permission Matrix

Agent → Tool Allowlist Matrix

Agent Prompt / Tool Routing Rules

DB01–DB09 Tool Description Matrix

Runtime-injected vs Model-controlled Parameter Matrix

Repository methods added

Files changed

Targeted test results

History-vs-DB P0 result

Collection incremental freshness result

Cross-case isolation result

Real Case questions / tool traces / answers

Known limitations
```

---

# 109. Final Definition of Done — Tool Surface

必须存在：

```text
[ ] get_case_data_overview
[ ] query_social_posts
[ ] get_social_post
[ ] query_social_comments
[ ] aggregate_social_data
[ ] query_findings
[ ] query_review_items
[ ] query_reports
[ ] query_case_activity
```

---

# 110. Final DoD — Architecture

```text
[ ] AgentDatabaseReadService exists

[ ] AgentDatabaseReadService depends on:
    ApplicationRepository
    SocialRepository
    CollectionRunRepository
    FindingRepository
    ReportDocumentRepository

[ ] database_tools.py exists

[ ] Tool handlers do not open DB sessions

[ ] Social query logic remains in SocialRepository

[ ] Finding query logic remains in FindingRepository

[ ] Report query logic remains in ReportDocumentRepository

[ ] CollectionRun query logic remains in CollectionRunRepository

[ ] Review / Activity query logic remains in ApplicationRepository

[ ] no generic SQL Tool exists

[ ] no generic DB write Tool exists
```

---

# 111. Final DoD — Bootstrap

```text
[ ] FindingRepository read instance is created before build_tool_registry

[ ] ReportDocumentRepository read instance is created before build_tool_registry

[ ] AgentDatabaseReadService is created before build_tool_registry

[ ] Existing FindingService lifecycle is not broken

[ ] Existing ReportDocumentService lifecycle is not broken

[ ] skills.validate_tools still passes
```

---

# 112. Final DoD — Permissions

```text
[ ] all new tools require read_database

[ ] no new tool requires write_database

[ ] missing read_database → tool_permission_denied

[ ] absent allowed_tools → tool_not_allowed

[ ] Runtime overrides model case_id

[ ] exact foreign-case IDs return found=false / empty

[ ] no cross-case count leakage
```

---

# 113. Final DoD — Data Correctness

```text
[ ] DB01 returns exact current counts

[ ] Comment counts are scoped through SourcePost.case_id

[ ] ReviewDecision counts are scoped through ReviewItem.case_id

[ ] active CollectionRun uses existing queued/running semantics

[ ] Post list is deterministic and paginated

[ ] query field is lexical, not semantic

[ ] DB Tool output excludes raw_payload / embedding / content_hash

[ ] current database state overrides history
```

---

# 114. Final DoD — Freshness

```text
[ ] cache_ttl_seconds=0

[ ] new SourcePost is visible on next Tool Call

[ ] CollectionRun partial persistence is immediately visible

[ ] History cannot override newer DB value

[ ] DB empty result cannot fall back to old History
```

---

# 115. Final DoD — Agent Routing / Prompt / Tool Guidance

```text
[ ] exact count → structured DB Tool

[ ] exact list/latest → structured DB Tool

[ ] current Finding/Review/Report status → structured DB Tool

[ ] semantic evidence search → search_social_evidence

[ ] truth judgment → Evidence / Verification / Finding / Review

[ ] Coordinator instructions explicitly encode this hierarchy

[ ] Expert instructions encode role-specific DB Tool usage

[ ] Experts only receive role-relevant DB Tools

[ ] DB01–DB09 ToolSpec.description clearly states when to use each Tool

[ ] Tool descriptions distinguish neighboring Tools

[ ] query_social_posts explicitly states query is lexical, not semantic

[ ] get_case_data_overview explicitly states it is authoritative for exact current counts

[ ] non-obvious Pydantic Input fields include Field.description

[ ] runtime-injected case_id is documented as never model-controlled

[ ] standard Tool-call examples are covered by deterministic tests

[ ] current-state questions without a DB Tool Call fail routing tests
```

---

# 116. Final DoD — P0 Regression

以下两个测试必须同时通过。

## P0-A History vs DB

```text
History = 10
Current DB = 25

User:
“现在知乎多少条？”

Agent:
→ DB Tool
→ 25
```

不调用 DB Tool：

```text
FAIL
```

---

## P0-B Collection Incremental Freshness

```text
Collection running

DB = 20
→ Agent query = 20

new platform persisted
DB = 47
→ next Agent query = 47
```

如果第二次仍回答：

```text
20
```

本轮：

```text
FAIL
```

---

# 117. Prompt / Tool Guidance P0 Gate

以下行为必须通过 deterministic Agent Test。

## G01 — Exact Count

```text
User:
“现在数据库一共有多少帖子？”
```

Agent 第一个相关 Tool 必须为：

```text
get_case_data_overview
```

或能够返回同等 exact count 的结构化 DB Tool。

---

## G02 — Latest Posts

```text
User:
“知乎最新 10 条是什么？”
```

必须调用：

```text
query_social_posts
```

参数：

```text
platforms=["zhihu"]
sort_order="newest"
limit=10
```

---

## G03 — Semantic Evidence

```text
User:
“哪些内容支持事件进入长尾传播？”
```

优先：

```text
search_social_evidence
```

不得把：

```text
query_social_posts(query="长尾传播")
```

当成完整 Evidence reasoning 的替代。

---

## G04 — Truth Verification

```text
User:
“数据库里有人说华为要求停售竹知了，这是真的吗？”
```

必须区分：

```text
DB Tool
→ 该说法是否被系统持久化

Verification / Evidence Tool
→ 该说法是否真实
```

禁止只调用数据库 Tool 后直接下事实结论。

---

## G05 — Runtime Parameter

Fake LLM Tool Call 不提供：

```text
case_id
```

调用仍必须成功，因为：

```text
case_id 由 Runtime 注入。
```

若 Prompt 让模型必须自己生成 `case_id`：

```text
本轮实现失败。
```

---

# 118. 最终真实用户行为

优化完成后：

```text
用户：
“刚才知乎到底采到了多少？”

Agent：
→ 不从旧 Assistant 回答中找数字
→ 不从 Memory 猜
→ 不用 RAG top-k 推算
→ 调 get_case_data_overview / aggregate_social_data
→ 查询当前数据库
→ 返回当前真实数量
```

用户：

```text
“把知乎现在采到的帖子给我看看。”
```

Agent：

```text
query_social_posts(platforms=["zhihu"])
```

用户：

```text
“这些帖子能不能证明华为真的要求停售竹知了？”
```

Agent：

```text
DB Post 只能证明“采集到该说法”
↓
search_social_evidence
query_claims
query_evidence
Verification
Finding / Review
```

最终 Nothing-in-the-dark 的 Agent 数据访问规则必须稳定变成：

> **当前系统状态查数据库；相关信息查 RAG；事实结论服从 Evidence 与 Human Review。**

这就是本轮优化的最终交付目标。
