# Nothing-in-the-dark 异步渐进式采集优化最终执行方案

> 文档性质：本轮采集链路优化的最终实施规格  
> 目标仓库：`Ethan-Martinez-creater/Nothing-in-the-dark`  
> 执行基线 HEAD：`b68025553b038eace0ddda4e07dc07e834502670`  
> 目标：解决真实 MediaCrawler 采集耗时过长导致的对话阻塞问题，并建立后台异步、渐进式、可恢复的 Collection 运行模型  
> 面向对象：负责直接修改仓库、补测试、运行真实采集验证并提交结果的执行智能体  
>
> 本文是最终执行规范。执行智能体不得自行替换核心架构方案，不得把本轮任务扩大成 Celery/Temporal 引入、MediaCrawler 全面重写、Harness 全面重构或 Optimization V3。

---

# 1. 当前问题

当前真实采集链路为：

```text
用户发送消息
    ↓
Agent 调用 collect_social_posts
    ↓
Tool Policy / Approval
    ↓
同步等待整轮采集
    ↓
平台 A / 关键词 1
平台 A / 关键词 2
平台 B / 关键词 1
...
    ↓
所有平台采集结束
    ↓
统一后处理 + 入库
    ↓
Tool 返回
    ↓
Agent 才继续回答
```

现有实现已经加入：

- 平台/关键词进度事件；
- headless；
- 串行稳定执行；
- 失败隔离；
- 每平台关键词数量限制；

但根本问题仍然存在：

> **采集仍然是一个同步长 Tool Call，Conversation 生命周期被 Crawler 生命周期绑定。**

真实测试中，单个平台单关键词即可能阻塞较长时间；多个平台、多个关键词、评论抓取和失败重试会使等待时间进一步累加。

---

# 2. 本轮优化的最终目标

完成后，用户体验必须变为：

```text
用户：
“请采集并调查华为竹知了事件。”

Agent：
解析当前 Investigation / Collection Definition
        ↓
start_social_collection
        ↓
Approval
        ↓
创建持久化 CollectionRun
        ↓
Tool 立即返回 collection_run_id
        ↓
Agent 当前 Turn 正常结束

系统：
“后台采集已经启动。
第一批数据到达后即可查看 Live Data，
完整采集会继续进行。”

此时：
AgentRun = completed
CollectionRun = queued / running
ChatInput = enabled

后台：
CollectionRunWorker
        ↓
受控平台并发
        ↓
每个平台一个 MediaCrawler process
        ↓
同一进程处理多个关键词
        ↓
某平台完成
        ↓
立即过滤、去重、持久化
        ↓
Live Data 立即出现 partial data
        ↓
其它平台继续后台采集
```

用户在完整 CollectionRun 结束前必须能够：

```text
继续聊天
查看 Live Data
查看已有 Timeline
基于已有数据发起阶段性分析
取消剩余采集
```

---

# 3. 四条不可违反的工程不变量

## INV-1 — Approval Snapshot 一致性

```text
用户批准的 Collection Definition + Scope
==
CollectionRun 中冻结的 request_json
==
Worker 最终执行的 Snapshot
```

禁止：

```text
Approval 完成后重新读取当前 Active Collection Definition
并执行新的版本。
```

## INV-2 — Lease Ownership

任意 CollectionRun：

```text
只有当前有效 lease_owner
可以继续更新 progress、result、terminal status
以及产生新的采集副作用。
```

Worker 一旦失去 lease：

```text
停止平台任务
停止新的数据库写入
触发 cancel_event
终止 MediaCrawler / Chrome / Playwright 子进程树
退出当前 CollectionRun
```

## INV-3 — AgentRun 与 CollectionRun 生命周期解耦

系统必须允许并稳定支持：

```text
AgentRun.status = completed

同时

CollectionRun.status = running
```

此时：

```text
ChatInput 必须可用。
```

这是本轮优化是否真正完成的首要判定条件。

## INV-4 — Partial Data 单调可用

一旦某个平台数据成功持久化：

```text
后续 Worker crash
retry
cancel
lease loss
其它平台失败
```

都不得：

```text
删除已成功数据
重复累计 posts_collected
使已完成平台重新被无条件采集
```

---

# 4. 本轮明确不做

禁止引入或实施：

```text
Celery
Temporal
Redis Queue
Kafka
新的分布式任务平台
新的 Collection SSE 通道
自动后台生成新的 Assistant Message
全面重写 MediaCrawler
全面重构 MonitorScheduler
全面重构 Agent Harness
自动 Discovery → Deep
多机分布式 Worker
RBAC / Organization / Optimization V3
```

继续复用当前：

```text
FastAPI
ApplicationContainer
asyncio Worker
数据库 Durable Job 模式
ToolRegistry
SandboxedToolExecutor
MediaCrawlerAdapter
现有 Approval / Harness Runtime
```

---

# 5. 最终总体架构

```text
                    User Conversation
                           │
                           ▼
                 Coordinator Agent
                           │
                 start_social_collection
                           │
                           ▼
                       Approval
                           │
                           ▼
                 CollectionRunService
                           │
                  create queued run
                           │
                    immediate return
                           │
                           ▼
                Agent current Turn ends
                           │
                           ▼
                 Conversation usable


                   CollectionRunWorker
                           │
                     lease + heartbeat
                           │
                 bounded concurrency
                           │
           ┌───────────────┴───────────────┐
           ▼                               ▼
        Platform A                      Platform B
   one MediaCrawler process       one MediaCrawler process
   multiple keywords              multiple keywords
           │                               │
           └───────────────┬───────────────┘
                           ▼
                  single-writer ingest
                           │
              filter / dedup / persist
                           │
             update CollectionRun progress
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
          Live Data     Timeline      Platform View
```

---

# 6. CollectionRun 持久化模型

新增：

```text
CollectionRunRecord
```

建议字段：

```text
id
case_id

collection_definition_id
collection_definition_version

trigger_run_id
trigger_turn_id
trigger_tool_call_id
approval_id

phase
status

request_fingerprint
idempotency_key

request_json
progress_json
result_json

posts_collected
comments_collected

attempts
max_attempts

lease_owner
lease_expires_at
heartbeat_at

cancel_requested_at

error_code
error_message

version

started_at
completed_at
created_at
updated_at
```

---

# 7. CollectionRun 状态机

统一使用：

```text
queued
running
completed
completed_with_errors
failed
cancelled
```

取消请求不作为正式 status：

```text
cancel_requested_at != null
```

运行中的 partial 数据通过：

```text
status == running
AND
posts_collected > 0
```

派生为前端文案：

```text
已有部分数据，继续采集中
```

最终部分平台失败使用：

```text
completed_with_errors
```

而不是 `partial`，避免终态语义模糊。

---

# 8. 平台级 progress 状态

`progress_json.platforms` 内每个平台固定状态：

```text
queued
running
completed
failed
cancelled
```

结构建议：

```json
{
  "platforms": {
    "weibo": {
      "status": "completed",
      "attempts": 1,
      "posts_collected": 47,
      "comments_collected": 0,
      "started_at": "...",
      "completed_at": "...",
      "error_code": null,
      "error_message": null
    }
  },
  "completed_platforms": 1,
  "total_platforms": 5
}
```

---

# 9. Immutable Collection Snapshot

CollectionRun 创建时必须冻结完整执行 Snapshot。

`request_json` 至少：

```json
{
  "case_id": "...",

  "definition": {
    "id": "...",
    "version": 2
  },

  "phase": "discovery",

  "topic": "...",

  "platforms": [
    "weibo",
    "bilibili"
  ],

  "time_range": {
    "start": "...",
    "end": "..."
  },

  "keywords": {
    "weibo": [
      "竹知了",
      "华为 竹知了"
    ],
    "bilibili": [
      "竹知了",
      "华为 竹知了"
    ]
  },

  "exclusions": [],

  "filters": {},

  "budget": {
    "limit_per_platform": 110,
    "per_day_limit": 30,
    "upstream_limit_per_platform": 110,
    "include_comments": false,
    "comment_limit": 0
  }
}
```

Worker 只能读取：

```text
CollectionRun.request_json
```

禁止执行期间重新读取 Active Collection Definition 改变任务。

---

# 10. 数据库 Migration

新增 `collection_runs` 表。

Migration 编号必须基于执行时最新 HEAD 的 migration 链确定，禁止制造 revision 冲突。

至少建立：

```text
FK case_id → cases.id ON DELETE CASCADE
UNIQUE(case_id, idempotency_key)
INDEX(status, created_at)
INDEX(case_id, created_at)
INDEX(lease_expires_at)
INDEX(request_fingerprint)
```

如果 `collection_definition_id` 建立 FK，也不能依赖该 Definition 的未来状态决定运行语义，运行语义以 `request_json` Snapshot 为准。

---

# 11. Approval 必须绑定 exact Definition

错误流程：

```text
Tool Call
→ Approval
→ Approval 通过
→ get_active_definition()
→ 创建 CollectionRun
```

禁止。

正确流程：

```text
Tool Call
→ 解析 exact Collection Definition
→ 固定 definition_id + definition_version
→ 固定 platforms / time range / phase / budget
→ Approval
→ Approval 通过
→ 根据 exact id/version 创建 CollectionRun
```

如果等待 Approval 时：

```text
Active Definition v2 → v3
```

用户批准的任务仍必须执行：

```text
v2 Snapshot
```

---

# 12. Crawl Approval Scope

现有 `collect_social_posts` 的 Crawl-specific Approval 保护必须扩展到：

```text
start_social_collection
```

Approval Scope 至少包含：

```text
collection_definition_id
collection_definition_version

platforms

time_range.start
time_range.end

phase

limit_per_platform
per_day_limit
upstream_limit_per_platform

include_comments
comment_limit
```

以下任一扩大必须重新 Approval：

```text
新增平台
扩大时间范围
增加上游抓取预算
增加保留数量
开启评论
Discovery → Deep
```

---

# 13. Agent-facing Tool

新增：

```text
start_social_collection
```

作为 Agent 唯一直接启动真实 Social Collection 的 Tool。

输入建议：

```text
phase        discovery | deep
platforms    optional
time_range   optional

case_id
run_id
turn_id
tool_call_id
```

其中 Case/Run/Turn/Tool Call 等应尽可能 runtime 注入，避免 LLM 自由构造。

LLM 不允许直接传：

```text
keywords
exclusions
filters
limit_per_platform
per_day_limit
upstream_limit
comment_limit
```

这些必须由系统 Policy 和 exact Collection Definition 决定。

---

# 14. start_social_collection 行为

固定流程：

```text
1. Case scope validation
2. resolve exact Collection Definition
3. resolve allowed platforms
4. resolve keywords
5. compute Discovery / Deep budget
6. build Approval Scope
7. Approval
8. build immutable snapshot
9. build request fingerprint
10. idempotently create CollectionRun
11. return immediately
```

返回：

```json
{
  "ok": true,
  "collection_run_id": "...",
  "status": "queued",
  "phase": "discovery",
  "platforms": [
    "weibo",
    "bilibili"
  ]
}
```

禁止：

```text
await Worker
直接运行 MediaCrawler
等待第一批数据
循环 poll CollectionRun
sleep 等待完成
```

---

# 15. get_collection_run Tool

新增：

```text
get_collection_run
```

只读。

仅在：

```text
用户明确询问采集进度
```

时使用。

Coordinator 不得在启动成功后的同一 Turn 自动循环：

```text
get_collection_run
get_collection_run
get_collection_run
```

等待任务完成。

---

# 16. Coordinator Tool Allowlist

调整：

```diff
- collect_social_posts
+ start_social_collection
+ get_collection_run
```

但底层：

```text
collect_social_posts
```

继续保留在 ToolRegistry 中。

最终关系：

```text
LLM-facing:
start_social_collection
get_collection_run

Internal sandbox crawler primitive:
collect_social_posts
```

禁止误删底层 Sandbox Tool。

---

# 17. Social Crawl Skill

修改：

```text
skills/social-crawl/SKILL.md
```

固定说明：

```text
1. 使用 start_social_collection。
2. CollectionRun 创建成功即代表采集已经启动。
3. 当前 Turn 不等待采集结束。
4. 不主动轮询 get_collection_run。
5. 告知用户后台任务正在执行。
6. 告知用户第一批数据会出现在 Live Data。
7. 用户可以继续对话。
8. 对 partial data 分析时必须明确“当前覆盖仍不完整”。
```

---

# 18. CrawlRequest 最终扩展

保留现有默认语义。

新增：

```python
@dataclass(slots=True)
class CrawlRequest:
    topic: str
    platforms: list[str]
    time_range: dict[str, str | None]

    limit_per_platform: int = 150
    per_day_limit: int = 150

    upstream_limit_per_platform: int | None = None

    comment_limit: int = 10
    include_comments: bool | None = None

    keywords: dict[str, list[str]] | None = None

    cancel_event: asyncio.Event | None = None
```

---

# 19. Legacy Consumer 兼容

不得为了 Discovery 修改 CrawlRequest 默认值。

特别禁止：

```text
per_day_limit 默认 150 → 10
comment_limit 默认 10 → 0
include_comments 默认直接 false
```

原因：

```text
MonitorScheduler
其它 crawler consumer
```

可能依赖现有默认语义。

CollectionRunService 必须显式传 Discovery/Deep 参数。

---

# 20. Discovery Budget

Discovery 默认：

```text
include_comments = false
comment_limit = 0
per_day_limit = 30
```

上游平台 Aggregate Budget：

```python
days = inclusive_days(start, end)

upstream_limit = min(
    max(days * 10, 60),
    150,
)
```

示例：

```text
1 天  → 60
11 天 → 110
30 天 → 150
```

Discovery：

```text
limit_per_platform = upstream_limit
per_day_limit = 30
upstream_limit_per_platform = upstream_limit
include_comments = false
comment_limit = 0
```

---

# 21. Deep Collection

Deep 必须是显式用户动作：

```text
phase = deep
```

禁止 Discovery 完成后自动 Deep。

Deep 必须重新 Approval。

建议：

```text
include_comments = true
comment_limit <= 当前产品既有安全上限
platform concurrency = 1
```

Deep Budget 可高于 Discovery，但必须进入 Approval Scope。

---

# 22. 平台选择兼容语义

允许平台：

```text
requested platforms
∩
Case platforms
```

不要再额外与：

```text
Collection Definition.platforms
```

取交集导致 requested platform 被静默删除。

关键词必须继续复用当前：

```text
CollectionDefinitionService.keywords_for()
```

语义：

```text
Definition 有该平台 query
→ 使用 Definition keywords

Definition 没有该平台 query
→ fallback topic
```

本轮性能优化不得偷偷改变 Collection 产品行为。

---

# 23. Request Fingerprint

CollectionRun Active 去重不能只使用：

```text
case + definition + phase
```

必须构造 canonical request payload：

```text
case_id
definition_id
definition_version
phase
sorted platforms
time range
canonical keywords
exclusions
filters
budgets
```

然后：

```text
request_fingerprint = SHA256(canonical_json)
```

Active Equivalent：

```text
same case_id
+
same request_fingerprint
+
status in queued/running
```

才允许返回已有 Run。

---

# 24. Tool Retry Idempotency

Harness Tool retry 使用：

```text
trigger_tool_call_id
```

生成：

```text
tool-call:<tool_call_id>
```

作为 `idempotency_key`。

同一个 Tool Call retry：

```text
只能产生一个 CollectionRun。
```

---

# 25. CollectionRunRepository

建议新增：

```text
backend/app/infrastructure/database/collection_run_repository.py
```

至少提供：

```text
create
get
get_for_case
list_for_case
list_active_for_case
find_active_by_fingerprint
claim_next
heartbeat
request_cancel
update_progress_if_owner
update_result_if_owner
mark_completed_if_owner
mark_completed_with_errors_if_owner
mark_failed_if_owner
mark_cancelled_if_owner
recover_expired
```

所有运行中写方法必须进行 lease fencing：

```text
WHERE
id = :run_id
AND lease_owner = :worker_id
```

---

# 26. Claim / Lease

参考现有 Durable Job 实现。

Claim：

```text
queued
→ running
```

成功后：

```text
lease_owner = worker_id
lease_expires_at = now + lease_seconds
heartbeat_at = now
attempts += 1
```

PostgreSQL 优先沿用：

```text
FOR UPDATE SKIP LOCKED
```

SQLite 使用项目现有兼容策略。

---

# 27. Worker Heartbeat

CollectionRunWorker 执行期间必须始终存在独立 heartbeat task。

例如：

```text
COLLECTION_WORKER_LEASE_SECONDS = 60
heartbeat interval ≈ 20s
```

Heartbeat 每轮：

```text
1. 验证 lease_owner
2. 延长 lease
3. 更新 heartbeat_at
4. 检查 cancel_requested_at
```

如果 heartbeat 返回“已不再拥有 lease”：

```text
lease_lost.set()
cancel_event.set()
停止平台任务
禁止后续数据库副作用
```

禁止只在“平台完成后”刷新租约。

---

# 28. CollectionRunWorker

新增：

```text
backend/app/application/collection_run_worker.py
```

生命周期：

```text
start
stop
loop
tick
execute
```

执行结构：

```text
claim
↓
heartbeat task
↓
读取 immutable snapshot
↓
恢复平台 checkpoint
↓
bounded concurrent platform execution
↓
single-writer ingest coordinator
↓
deferred retry
↓
terminal status
```

---

# 29. Worker Recovery

对过期 lease 的运行：

```text
允许重新 claim
```

平台恢复规则：

```text
completed
→ skip

running
→ stale owner，恢复 queued

failed
→ attempts < platform retry limit 时 queued
→ 否则保持 failed

queued
→ execute
```

禁止：

```text
Worker 重启
→ 所有平台无条件重新采集
```

---

# 30. 平台并发

Discovery：

```text
run 内 platform concurrency = 2
```

Deep：

```text
run 内 platform concurrency = 1
```

并发单位：

```text
平台
```

不是：

```text
关键词
```

---

# 31. 全局 Crawl Capacity

推荐新增：

```text
CrawlCapacityLimiter
```

由 ApplicationContainer 创建单例。

配置：

```text
MEDIACRAWLER_GLOBAL_CONCURRENCY=2
```

至少接入：

```text
CollectionPlatformExecutor
MonitorScheduler
```

从系统级保证：

```text
同时活跃的 MediaCrawler browser process
<= global concurrency
```

---

# 32. MediaCrawler：一个平台一个 Process

修改 MediaCrawler Adapter。

禁止：

```text
platform A
keyword 1 → process 1
keyword 2 → process 2
```

必须：

```text
platform A
→ one process
→ --keywords "kw1,kw2"
```

---

# 33. Aggregate Budget 验证

必须确认 MediaCrawler：

```text
crawler_max_notes_count
```

是 aggregate 还是 per-keyword 语义。

系统最终必须保证：

```text
upstream_limit_per_platform
```

是平台 Aggregate Limit。

如果 MediaCrawler 内部按 keyword 使用 limit：

```text
按 keyword 数量拆分 process budget
```

或：

```text
Adapter 后严格 aggregate cap
```

禁止 2 个 keyword 让平台预算无意翻倍。

---

# 34. Discovery 必须真正关闭评论抓取

```text
include_comments=false
```

必须使 MediaCrawler 根本不进入评论采集逻辑。

禁止：

```text
先抓评论
→ 最后 comment_limit=0 丢弃
```

---

# 35. Sandbox 保留

CollectionRunWorker 不得裸跑：

```text
subprocess MediaCrawler
```

必须继续经过：

```text
ToolRegistry
→ SandboxedToolExecutor
→ internal collect_social_posts capability
→ MediaCrawlerAdapter
```

继续保留：

```text
restricted process
egress policy
cancel propagation
process-tree termination
audit
timeout policy
```

---

# 36. Cancel 统一语义

以下事件统一触发：

```text
cancel_event
```

来源：

```text
用户 cancel
lease lost
Application shutdown
```

最终必须传播到：

```text
CollectionRunWorker
→ CollectionPlatformExecutor
→ ToolRegistry
→ SandboxedToolExecutor
→ MediaCrawler process
→ Chrome / Playwright children
```

---

# 37. Incremental Persistence

平台完成后立即：

```text
1. time-range filter
2. high-confidence noise filter
3. native-id dedup
4. Collection exclusions
5. coverage sampling
6. SocialRepository.persist_batch
7. 更新平台 checkpoint
8. 重算 CollectionRun totals
```

禁止：

```text
所有平台结束
→ 再统一 persist
```

---

# 38. 非关键 Enrichment 不得阻塞首批数据

以下不得放在 `persist_batch` 之前：

```text
embedding
sentiment
platform summary
coverage memory
非关键 profile enrichment
```

允许：

```text
persist
→ partial data 可见
→ best-effort enrichment
```

---

# 39. Single Writer Coordinator

浏览器 I/O 可以并发。

数据库 ingest / progress 更新必须单 Writer。

推荐：

```text
platform task A ─┐
platform task B ─┤
                 ▼
             asyncio.Queue
                 │
                 ▼
       CollectionRun coordinator
            ├─ filter
            ├─ persist
            ├─ checkpoint
            └─ progress
```

平台任务不得直接并发覆盖 `progress_json`。

---

# 40. posts_collected 禁止累加式更新

禁止：

```python
run.posts_collected += platform_result_count
```

必须根据平台 checkpoint 重算：

```python
run.posts_collected = sum(
    p["posts_collected"]
    for p in progress["platforms"].values()
    if p["status"] == "completed"
)
```

`comments_collected` 同理。

---

# 41. SocialRepository 幂等

继续依赖当前：

```text
case_id + platform + native_id
```

语义进行 upsert。

重复采集已有 Post：

```text
update
```

不得让总 collected count 翻倍。

---

# 42. Retry 策略

每平台最大：

```text
2 attempts
```

固定顺序：

```text
Main Pass：
A fail
→ 先执行 B / C / D

Retry Pass：
所有平台首轮结束后
→ 再重试 A
```

禁止 immediate retry 阻塞 First Usable Data。

---

# 43. Partial Failure

例如：

```text
Weibo completed
Bilibili completed
Douyin failed
```

最终：

```text
CollectionRun.status = completed_with_errors
```

成功数据保留。

如果所有平台失败：

```text
failed
```

---

# 44. Cancel Partial Data

例如：

```text
Weibo completed 47
Bilibili completed 30
Douyin running
用户 cancel
```

结果：

```text
CollectionRun = cancelled
77 posts remain
```

禁止删除已完成数据。

---

# 45. CollectionRunService

新增：

```text
backend/app/application/collection_run_service.py
```

职责：

```text
resolve approved exact snapshot
build Discovery/Deep budget
build request fingerprint
idempotent create
read/list
cancel
```

不负责实际 Crawl。

---

# 46. ApplicationContainer 集成

新增：

```text
collection_run_repository
collection_run_service
collection_platform_executor
collection_run_worker
crawl_capacity_limiter
```

`start()`：

```text
await collection_run_worker.start()
```

`stop()`：

```text
停止 claim 新 Run
→ shutdown cancel
→ 停止 active platform tasks
→ 终止 subprocess tree
→ 等待 cleanup
→ stop worker
```

---

# 47. CollectionRun API

新增：

```http
GET /api/v1/cases/{case_id}/collection-runs

GET /api/v1/cases/{case_id}/collection-runs/{run_id}

POST /api/v1/cases/{case_id}/collection-runs/{run_id}:cancel
```

List 支持：

```text
active=true
status
phase
limit
```

所有 API 必须执行 Case Scope。

---

# 48. API 返回安全

允许返回：

```text
status
phase
posts_collected
comments_collected
platform progress
started_at
completed_at
errors
```

禁止返回：

```text
Cookies
Sandbox secrets
raw environment
认证 token
```

---

# 49. CollectionRunCard

新增：

```text
frontend/src/components/collection/CollectionRunCard.vue
```

至少显示：

```text
phase
overall status
posts collected
comments collected
platform rows
platform status
platform posts
error
elapsed time
cancel
```

---

# 50. 前端状态文案

统一：

```text
queued
→ 等待采集

running + posts=0
→ 正在采集

running + posts>0
→ 已有部分数据，继续采集中

completed
→ 采集完成

completed_with_errors
→ 采集完成，部分平台失败

failed
→ 采集失败

cancelled
→ 已取消
```

---

# 51. CollectionRunCard 持久恢复

Tool result 的 `collection_run_id` 只用于即时关联。

页面刷新后的事实来源：

```http
GET /cases/{case_id}/collection-runs?active=true
```

通过：

```text
trigger_run_id
```

重新映射到对应 RunBubble。

---

# 52. Polling

本轮不新增 Collection SSE。

非 terminal：

```text
2–3 秒 poll
```

页面 hidden：

```text
5 秒或暂停
```

terminal 后停止。

AgentRun 已 completed、CollectionRun 仍 running 时，Card 必须继续 polling。

---

# 53. Live Data 渐进刷新

当：

```text
posts_collected
```

增长时刷新 Live Data。

只有计数变化时 reload，避免无意义请求。

---

# 54. ChatInput 解耦

ChatInput 禁用只能与当前 Agent 交互状态有关。

不得因为：

```text
CollectionRun.status == running
```

而禁用。

---

# 55. 阶段性分析

运行中且已有数据：

```text
running
AND posts_collected > 0
```

显示：

```text
分析已有数据
```

点击必须生成正常 User Turn，而不是后台隐式执行。

---

# 56. 完整数据分析

`completed` 或 `completed_with_errors` 后显示：

```text
基于当前采集结果继续分析
```

如果是 `completed_with_errors`，Prompt 必须提示覆盖限制。

---

# 57. Observability

新增：

```text
collection.enqueue_latency_ms
collection.queue_wait_ms
collection.platform_duration_ms
collection.first_persist_ms
collection.total_duration_ms

collection.posts_persisted
collection.comments_persisted
collection.retry_count
collection.platform_failures
collection.cancelled
collection.lease_lost
```

标签：

```text
collection_run_id
phase
platform
attempt
```

---

# 58. Backend 测试 — CollectionRun

至少覆盖：

```text
CR01 create queued run
CR02 immutable snapshot
CR03 exact definition version survives Active Definition changes
CR04 same tool call is idempotent
CR05 different platform scope → different fingerprint
CR06 different time range → different fingerprint
CR07 same active fingerprint → existing run
CR08 claim queued run
CR09 heartbeat extends lease
CR10 stale worker loses ownership
CR11 stale worker cannot update progress/result
CR12 lease loss triggers cancel
CR13 user cancel
CR14 completed platform survives recovery
CR15 stale running platform resets/retries correctly
CR16 failed platform retries within limit
CR17 retry does not double posts_collected
CR18 completed_with_errors preserves successful data
CR19 all platforms fail → failed
CR20 all platforms succeed → completed
```

---

# 59. Approval 测试

```text
AP01 start_social_collection requires approval
AP02 platform expansion requires reapproval
AP03 time-range expansion requires reapproval
AP04 budget expansion requires reapproval
AP05 include_comments false → true requires reapproval
AP06 discovery approval cannot authorize deep
AP07 approval binds exact definition id/version
AP08 Active Definition changes while approval pending does not change execution
```

---

# 60. Crawler / Adapter 测试

```text
MC01 one platform + multiple keywords → one subprocess
MC02 two platforms obey global capacity
MC03 discovery include_comments=false → comment crawler not entered
MC04 aggregate upstream budget respected
MC05 cancel_event terminates subprocess
MC06 lease loss terminates subprocess
MC07 CrawlRequest legacy defaults unchanged
MC08 MonitorScheduler crawler behavior unchanged
MC09 concurrent platform output paths do not collide
```

---

# 61. Worker 并发测试

```text
CW01 discovery platform concurrency <= 2
CW02 deep platform concurrency <= 1
CW03 concurrent platform completion does not lose progress
CW04 global MediaCrawler concurrency <= configured capacity
CW05 first finished platform persists before remaining platforms finish
CW06 AnalysisJob worker is not blocked by long CollectionRun
```

---

# 62. Harness 测试

```text
H01 Coordinator has start_social_collection
H02 Coordinator no longer directly uses collect_social_posts
H03 social-crawl Skill references start_social_collection
H04 start tool returns quickly after enqueue
H05 Coordinator does not poll get_collection_run automatically
H06 AgentRun reaches completed while CollectionRun remains running
H07 Chat remains usable during running CollectionRun
```

`H06 + H07` 是本轮 P0 Gate。

---

# 63. Frontend 测试

```text
FE01 CollectionRunCard render
FE02 Agent completed + Collection running → ChatInput enabled
FE03 page reload restores active CollectionRun
FE04 posts_collected increase refreshes Live Data
FE05 terminal run stops polling
FE06 cancel action updates state
FE07 completed_with_errors shows successful + failed platforms
FE08 AgentRun terminal does not hide active CollectionRun
FE09 分析已有数据 produces a normal User Turn
```

---

# 64. Migration 测试

至少：

```text
upgrade
downgrade
upgrade
```

SQLite。

如 PostgreSQL 测试环境可用，再验证 PG migration。

---

# 65. Mandatory Adjacent Backend Regression

至少覆盖：

```text
Collection Definitions
Collection Tool Integration
MediaCrawler Adapter
Crawl Cancel
Crawl Coverage
Tool Sandbox
Approval / HITL
Agent Runtime
Durable Runtime
Tool System
Expert Agents
Analysis Jobs
Social Repository
Posts
Case Deletion
Production Entry
Monitor Scheduler / Monitor Execution
```

文件名按当前仓库实际名称执行。

---

# 66. Frontend Gate

运行：

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

全部通过。

---

# 67. Browser E2E — Async Collection UX

必须覆盖：

## E2E-A Non-blocking

```text
User 请求采集
→ Approval
→ approve
→ CollectionRun 创建
→ Agent 回复后台已启动
→ AgentRun terminal
→ CollectionRun still running
→ ChatInput enabled
```

## E2E-B Partial Data

```text
Platform A 完成
Platform B 仍 running
→ posts_collected > 0
→ Live Data 已出现 A 的帖子
```

## E2E-C Continue Chat

```text
CollectionRun running
→ User 发送第二条消息
→ 新 AgentRun 正常运行
```

## E2E-D Cancel

```text
partial posts exist
→ cancel
→ cancelled
→ partial posts remain
```

## E2E-E Partial Failure

```text
2 success
1 fail
→ completed_with_errors
→ successful data usable
```

## E2E-F Page Refresh

```text
CollectionRun running
→ F5
→ active CollectionRunCard restored
→ polling continues
```

---

# 68. 性能验收

至少记录：

```text
Approval timestamp
Tool enqueue completed timestamp
Agent Turn completed timestamp
First platform completed timestamp
First SourcePost visible timestamp
CollectionRun terminal timestamp
Platform durations
Posts per platform
Retries
Max active browser processes
```

核心指标：

```text
enqueue latency
Agent release latency
first usable data latency
full collection latency
```

---

# 69. 硬性性能 / UX DoD

确定性测试：

```text
start_social_collection enqueue < 1s
```

真实运行：

```text
Agent Turn 不再等待完整 Crawl
```

必须实际观察：

```text
AgentRun = completed
CollectionRun = running
ChatInput = enabled
```

第一平台完成后：

```text
数据立即出现在 Live Data
```

而不是等待整轮。

---

# 70. 华为“竹知了”真实案例验收

专项测试全部通过后，重新执行：

```text
主题：华为竹知了事件
时间：2026-08-10 ～ 2026-08-20
```

本轮只重跑采集链路，不要求重新完成 Evidence → Finding → Report 全案例。

建议选择：

```text
2–3 个当前登录最稳定的平台
```

验证：

```text
Collection Definition
→ Agent start_social_collection
→ Approval
→ Agent quickly completes
→ CollectionRun background running
→ partial Live Data
→ User continues conversation
→ CollectionRun terminal
```

---

# 71. 真实案例必须记录

```text
CASE_ID
COLLECTION_RUN_ID

T0 user request
T1 approval shown
T2 approved
T3 start tool returned
T4 AgentRun completed
T5 first platform completed
T6 first SourcePost visible
T7 CollectionRun terminal

posts/platform
failed platforms
retry count
max browser processes
```

---

# 72. 默认不跑 800+ Backend Full Regression

本轮采用：

```text
广覆盖 Targeted + Adjacent Regression
```

无需机械执行全部后端测试。

以下任一出现才升级到 Full Regression：

```text
修改 DB engine / session factory
修改全局 transaction policy
修改 ToolRegistry 通用执行语义
修改 Harness terminal semantics
修改 SocialRepository 全局 upsert contract
改动扩散到 Evidence / Finding / Review / Report 核心域
Targeted tests 出现无法局部解释的跨领域失败
```

新增 `collection_runs` 表本身不自动触发 Full Regression，但 migration / case delete / production entry 必须专项验证。

---

# 73. 禁止实现方式

不得通过以下方式宣称任务完成：

```text
只增加更多 tool_progress
只把关键词 2 → 1
只扩大 timeout
恢复 5 平台无界并发
Worker 裸跑 MediaCrawler
Worker 重新读取最新 Active Definition
后台自动写 Assistant Message
取消时删除已采集数据
Discovery 自动 Deep
只在平台结束时 heartbeat
posts_collected 使用简单 +=
多个平台任务直接并发覆盖 progress_json
```

---

# 74. 推荐 Commit 顺序

```text
feat: add durable collection run lifecycle
```

包括：

```text
model / migration / repository / service / lease / heartbeat / recovery / API
```

然后：

```text
perf: optimize mediacrawler discovery execution
```

包括：

```text
one process/platform
multi keywords
aggregate budget
comments off
global capacity
incremental ingest
```

然后：

```text
feat: decouple social collection from agent turns
```

包括：

```text
start tool
approval scope
Coordinator / Skill
CollectionRunCard
Live Data refresh
ChatInput decoupling
E2E
```

最后：

```text
docs: finalize async progressive collection optimization
```

---

# 75. 最终实施顺序

执行智能体严格按以下顺序推进：

```text
AC0
冻结 baseline + 记录真实旧行为

AC1
定义 CollectionRun domain contract
状态机 / snapshot / fingerprint / progress schema

AC2
Migration + Repository
lease / heartbeat / fencing / recovery

AC3
CollectionRunService
exact snapshot / budget / fingerprint / idempotency

AC4
Approval scope
让 start_social_collection 继承完整 Crawl 安全边界

AC5
扩展 CrawlRequest
upstream_limit / include_comments
保持 legacy defaults

AC6
MediaCrawler Adapter
one process/platform
multi-keyword
aggregate budget
true comment disable

AC7
Sandbox CollectionPlatformExecutor

AC8
Global Crawl Capacity Limiter

AC9
CollectionRunWorker
heartbeat
bounded concurrency
single-writer ingest
recovery
retry
cancel
shutdown

AC10
Agent Tool Migration
Coordinator allowlist
Skill / Prompt

AC11
CollectionRun API

AC12
Frontend persistent CollectionRun integration
Card / RunBubble / LiveData / ChatInput

AC13
Backend targeted tests

AC14
Frontend + Browser E2E

AC15
华为竹知了真实采集验收

AC16
Delivery / architecture documentation
```

---

# 76. 最终 Definition of Done

只有以下全部成立，本轮优化才允许结束。

## Architecture

```text
[ ] CollectionRun persistent model
[ ] dedicated CollectionRunWorker
[ ] immutable approved snapshot
[ ] request fingerprint
[ ] Tool retry idempotency
[ ] lease + heartbeat + fencing
[ ] recovery checkpoint
[ ] Agent-facing start_social_collection
[ ] direct collect_social_posts removed from Coordinator allowlist
[ ] internal collect_social_posts retained as sandbox primitive
```

## Crawler

```text
[ ] one platform = one MediaCrawler process
[ ] multiple keywords = same process
[ ] platform aggregate upstream budget enforced
[ ] Discovery comments truly disabled
[ ] Discovery platform concurrency <= 2
[ ] Deep platform concurrency <= 1
[ ] system-wide MediaCrawler concurrency limited
[ ] deferred retry
```

## Progressive Data

```text
[ ] each completed platform persists immediately
[ ] partial data visible before full run ends
[ ] successful data survives other platform failures
[ ] retry/recovery does not double counts
[ ] cancel preserves partial data
```

## Conversation UX

```text
[ ] Approval remains
[ ] enqueue returns quickly
[ ] Agent Turn does not await crawler
[ ] AgentRun can be completed while CollectionRun running
[ ] ChatInput enabled while CollectionRun running
[ ] active CollectionRun survives page refresh
[ ] user can view Live Data during collection
[ ] user can explicitly analyze partial data
[ ] Coordinator does not auto-poll
[ ] no hidden automatic Assistant continuation
```

## Security / Correctness

```text
[ ] approved snapshot == executed snapshot
[ ] scope expansion requires reapproval
[ ] lease lost worker cannot continue writes
[ ] lease loss cancels external process
[ ] sandbox retained
[ ] egress retained
[ ] Case scope enforced
[ ] MonitorScheduler legacy crawler behavior retained
```

## Tests / Validation

```text
[ ] CollectionRun backend tests green
[ ] Approval tests green
[ ] MediaCrawler adapter tests green
[ ] Worker concurrency/recovery tests green
[ ] Harness decoupling tests green
[ ] Migration tests green
[ ] Adjacent backend regression green
[ ] frontend typecheck green
[ ] frontend lint green
[ ] frontend tests green
[ ] frontend build green
[ ] Async Collection browser E2E green
[ ] 华为竹知了真实采集验证完成并记录指标
```

---

# 77. 最终用户体验目标

优化后的系统不要求“完整采集必须瞬间完成”。

真正目标是：

```text
用户发起任务
    ↓
数秒内获得“后台采集已启动”
    ↓
Conversation 可继续使用
    ↓
第一批平台完成
    ↓
Live Data 出现真实 partial data
    ↓
用户可以立即浏览或做阶段性分析
    ↓
后台继续完成剩余平台
```

即使完整采集仍需要数分钟：

> 用户也不再需要等待数十分钟才能继续使用 Nothing-in-the-dark。

这才是本轮优化最终必须实现的产品结果。
