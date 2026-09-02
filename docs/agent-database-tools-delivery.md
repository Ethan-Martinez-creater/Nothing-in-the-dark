# Agent Database Tools Delivery（V2）

> 对应实施规格：`docs/nothing-in-the-dark-agent-database-tools-final-v2.md`  
> 目标：Agent 回答"当前 Case 数据库实际状态"问题时，必须确定性查询当前数据库，而不是依赖 Conversation History / Memory / RAG top-k 猜测。

## 1. Baseline / Final

| 项 | 值 |
|---|---|
| Baseline HEAD | `769b352e4e5f672604246857044b59a425918a03` |
| Final HEAD | （见提交记录，按文档 §106 的 4 段式提交） |

## 2. New Tools（DB01–DB09）

| 编号 | Tool | 用途 |
|---|---|---|
| DB01 | `get_case_data_overview` | 当前 Case 数据概况与精确数量（权威 exact count） |
| DB02 | `query_social_posts` | 精确查询当前 Source Posts（lexical 过滤） |
| DB03 | `get_social_post` | 通过稳定 ID 获取单条 Post（exact case scope） |
| DB04 | `query_social_comments` | 查询当前 Source Comments（经 SourcePost JOIN case scope） |
| DB05 | `aggregate_social_data` | 平台 / 日期 / 内容类型精确聚合 |
| DB06 | `query_findings` | 查询当前 Finding 状态（exact 附带 evidence/source links） |
| DB07 | `query_review_items` | 查询 Human Review 当前状态（exact 附带 latest_decision） |
| DB08 | `query_reports` | 查询当前 ReportDocument（可选 bounded content preview） |
| DB09 | `query_case_activity` | 查询 Case Activity 日志 |

继续复用：`get_collection_run` / `search_social_evidence` / `query_claims` / `query_evidence` / `query_propagation` / `get_artifact`。

## 3. Tool → Permission Matrix

全部 DB Tool 统一：

```text
permissions = ("read_database",)
side_effect = "none"
idempotent = True
requires_approval = False
execution_mode = "parallel"
cache_ttl_seconds = 0        # 实时 DB Tool 不缓存
max_concurrency = 8
timeout_seconds = 10
max_retries = 0
execution_class = "trusted_in_process"
rag_output = False
```

未新增任何权限字符串；未修改 ToolRegistry 权限核心机制。

## 4. Agent → Tool Allowlist Matrix

| Agent | 新增 DB Tool |
|---|---|
| Coordinator | 全部 DB01–DB09 |
| Opinion | DB01, DB02, DB03, DB04, DB05 |
| Propagation | DB02, DB03, DB04 |
| Verification | DB02, DB03, DB04 |
| Evidence Critic | DB03, DB06 |
| Report | DB01, DB06 |
| Citation Validator | DB03, DB06 |

各 Expert 未新增 `write_database` 权限。

## 5. Agent Prompt / Tool Routing Rules

Coordinator Instructions 新增"当前持久化状态"路由规则：

```text
当用户询问当前 Case 已经持久化的数据、精确数量、精确记录列表、最新记录、
平台分布、Finding/Review/Report 当前状态时，必须优先调用结构化数据库查询工具。
Conversation History、Memory、旧 Artifact、先前 Assistant 回答不能替代
当前数据库查询。
search_social_evidence 用于语义相关性与 Evidence discovery，
不得作为数据库 exact count 或 complete list 的权威来源。
若数据库返回 0 条，必须以当前数据库结果为准。
如果当前 DB 与历史回答冲突，以当前数据库为准。
数据库中存在某条 Social Post 只代表系统持久化了该内容，
不代表该 Post 陈述的事实已经被证明。
```

各 Expert Instructions 增加角色相关的最小 DB Tool 规则（Opinion 精确统计 /
Propagation 核验真实 Post / Verification 区分"存在"与"为真" / Critic 核验引用 /
Report 覆盖概况与 Findings / Validator 精确核验 ID）。

## 6. DB01–DB09 Tool Description 要点

每个 `ToolSpec.description` 都承担路由职责（明确适用问题 / 不适用问题 / 与相邻 Tool 区别）：

- `get_case_data_overview`：authoritative current persisted counts；Do not infer exact counts from conversation history, memory, or semantic search.
- `query_social_posts`：deterministic database filters；query 是 lexical，不是 semantic；semantic 用 search_social_evidence。
- `get_social_post`：exact stable post_id 或 platform+native_id；不验证帖子内事实。
- `query_social_comments`：exact database filters；不是语义证据搜索。
- `aggregate_social_data`：exact deterministic post-count aggregation by platform/day/content_type。
- `query_findings`：workflow status；Only verified findings represent Human-Review-accepted conclusions.
- `query_review_items`：Human Review 状态；read-only。
- `query_reports`：ReportDocument 状态；不用于生成/修改报告。
- `query_case_activity`：activity log 有界摘要。

`search_social_evidence` description 补充：Do not use this tool as the authoritative source for exact database counts or complete record lists.

## 7. Runtime-injected vs Model-controlled Parameters

| 参数 | 控制方 |
|---|---|
| `case_id`（全部 DB Tool） | **Runtime**（`_CASE_SCOPED_TOOLS` 强制覆盖，模型伪造其它 case 被改写） |
| `platforms / query / author / date_from / date_to / sort_order / limit / offset / group_by / status / ...` | 模型（受 Pydantic bounds 约束） |

所有非直观 Input 字段带 `Field(description=...)`；runtime-injected `case_id` 标注 "Injected by runtime; never model-controlled."。

## 8. Repository Methods Added / Extended

| Repository | 方法 | 类型 |
|---|---|---|
| `SocialRepository` | `count_posts` / `get_post_for_case` / `list_comments_page` / `count_comments` / `count_posts_by_platform` / `count_posts_by_content_type` | 新增 |
| `SocialRepository` | `list_posts_page`（+platforms/author/sort_order）、`list_post_time_rows`（+platforms/q/date range） | 扩展（向后兼容） |
| `ApplicationRepository` | `get_case_database_counts` | 新增 |
| `ApplicationRepository` | `list_review_items`（+review_item_id/object_id/offset）、`list_activity_log`（+activity_type/actor/offset） | 扩展 |
| `FindingRepository` | `list`（+finding_id/query/offset）、`count` | 扩展/新增 |
| `ReportDocumentRepository` | `list_for_case`（+report_id/status/offset）、`count_for_case` | 扩展/新增 |
| `CollectionRunRepository` | `count_for_case` | 新增 |

关键约束实现：
- Comment 查询一律 `SourceComment JOIN SourcePost ON post_id`，以 `SourcePost.case_id` 限定 Case scope（DB-INV-3）。
- `ReviewDecision` count 一律 `JOIN ReviewItem`，以 `ReviewItem.case_id` 限定（防止跨 Case 泄漏）。
- day 聚合沿用 Python 侧按天聚合（双方言安全）；SQLite 读取的 naive datetime 直接取日历日期，避免本地时区假定造成跨日偏移。

## 9. Files Changed

```text
backend/app/application/agent_database_service.py        NEW
backend/app/harness/database_tools.py                    NEW
backend/app/harness/tool_factory.py                      MODIFIED
backend/app/harness/runtime.py                           MODIFIED
backend/app/harness/agents.py                            MODIFIED
backend/app/bootstrap.py                                 MODIFIED
backend/app/infrastructure/database/social_repository.py MODIFIED
backend/app/infrastructure/database/finding_repository.py MODIFIED
backend/app/infrastructure/database/report_repository.py MODIFIED
backend/app/infrastructure/database/collection_run_repository.py MODIFIED
backend/app/application/repositories.py                  MODIFIED

backend/tests/test_agent_database_service.py             NEW
backend/tests/test_agent_database_tools.py               NEW
backend/tests/memory_db.py                               NEW
```

未修改：`models.py` / `engine.py` / `migrations/` / Finding 状态机 / Review mutation / Report publish gate / CollectionRun 状态机 / ToolRegistry 权限核心（文档 §69 No Migration 原则）。

## 10. Targeted Test Results

| 测试文件 | 结果 |
|---|---|
| `tests/test_agent_database_service.py`（33 个） | PASS（Repository R01–R20 + Service DB01–DB09 + Freshness） |
| `tests/test_agent_database_tools.py`（19 个） | PASS（T01–T19 契约 + P01–P06 权限 + S01–S08 scope + G01/G02/G04/G05 routing + No-Arbitrary-SQL） |
| `tests/test_agent_runtime.py` | PASS（Runtime case scope 抽取无回归） |
| `tests/test_social_repository.py` | PASS（list_posts_page 扩展兼容） |
| `tests/test_expert_agents.py` | PASS（allowlist 更新无回归） |

> 注：本仓库多数既有测试依赖文件 SQLite 的 `create_schema()`（Windows + aiosqlite 上每测试约 60s），完整相邻回归成本极高。新增测试使用内存库 + StaticPool（`tests/memory_db.py`）规避该环境瓶颈。

### History-vs-DB P0 结果

```text
History = "知乎有 10 条"（对话历史）
当前 DB  = 知乎 25 条

User: "现在数据库里知乎有多少条？"
Agent 路由 → get_case_data_overview
Tool 返回 → "posts": 25（当前数据库真值）
```

`test_g01_exact_count_uses_db_tool_and_returns_current_db` 断言 `"posts": 25`。

### Collection Incremental Freshness 结果

```text
get_case_data_overview → posts=1
persist 新 post → 立即再次调用 → posts=2
（cache_ttl_seconds=0，下一 Tool Call 可见）
```

`test_freshness_new_post_visible_immediately` / `test_freshness_collection_run_partial_persist_visible` 覆盖。

### Cross-Case Isolation 结果

- `get_social_post(foreign post_id)` → `{"ok": true, "found": false}`（DB-INV-4）
- `query_findings(foreign finding_id)` → `found=false`
- DB01 counts（posts/comments/findings/collection_runs/review_items/review_decisions）均不包含其它 Case（S07/S08）

## 11. Real Case Verification（DBT13）

服务重启（bettafish 环境，`python -m app.main`）后对真实 Investigation 逐个提问，检查 `ToolCallRecord.tool_name`（文档 §85/§101）。

### Case A：华为竹知了节奏事件（`93d17b16-...`，数据库中无帖子）

| 问题 | Tool 轨迹（节选） | 结果 |
|---|---|---|
| 当前数据库总共有多少帖子？ | `aggregate_social_data(group_by=day)` → 0；`aggregate_social_data(group_by=platform)` → 0；`get_case_data_overview` → counts.posts=0；随后 `query_case_activity` / `query_reports` / `query_findings` / `query_social_posts` 均返回空 | 9 个 Tool Call 全部 `completed`，Agent 如实回答当前数据库为空，未从历史/记忆编造"25 条"（DB-empty P0 达成） |

### Case B：杭州电梯女子诬告事件（`93c5c2b8-...`，真实有数据）

| 问题 | Tool 轨迹（节选） | 结果 |
|---|---|---|
| 当前数据库总共有多少帖子？ | `aggregate_social_data(group_by=platform)` → total=111；`get_case_data_overview` → counts：posts=111 / comments=14 / collection_runs=11 / claims=4 / findings=4 / artifacts=1 | Agent 返回当前数据库真实精确数量（G01 Exact Count 达成） |
| 知乎平台现在有哪些帖子？ | `query_social_posts`（runtime 注入 case_id）→ matched_count=25，返回 25 条真实帖子 | 平台精确列表（G02 达成；正是此前"23 条"误答场景的真实数据） |

两层验证覆盖了本轮核心目标：

```text
空 case：Agent 查当前 DB → 0（不 History fallback）
有数据 case：Agent 查当前 DB → 111 / 25（真实 exact count，来源可追踪）
```

## 12. Known Limitations

- `query_case_activity` 不返回 `matched_count`（现有 `list_activity_log` 无 count 方法；列表返回 `returned_count` + `next_offset`）。
- `aggregate_social_data` 第一版只支持 `post_count` 单一 metric（文档 §39 明确不在本轮增加 percentile / median 等复杂 metric）。
- 数据库中的 Social Post 只代表"系统持久化了该内容"，不自动证明内容为真；事实判断必须进入 Evidence / Verification / Finding / Review（DB-INV-2）。
- day 聚合按 UTC 日历日期归组（沿用现有 posts stats 逻辑）；SQLite 下 naive datetime 直接取日历日期。
