# Nothing-in-the-dark Review Decision Concurrency Hardening 执行计划

> 文档性质：Optimization V2 正式 CLOSED 后的独立工程正确性修复计划  
> 目标问题：Review 决策并发竞争、`current_version` 失效、前端未提交版本导致的 stale/ABA 决策风险  
> 评审仓库：`Ethan-Martinez-creater/Nothing-in-the-dark`  
> 评审基线 HEAD：`2a2d1e6e523fbb2d26824c4bdd4b463553dcad11`  
> 面向对象：负责直接修改仓库、运行测试并提交实现的执行智能体  
>
> 本文不是 Optimization V2 返工，也不是多人协作/RBAC 方案。执行智能体只实现本文确定的 Review 并发控制方案，不重新设计 Investigation、Finding、Review、Agent Harness 或权限体系。

---

# 1. 评审结论

Optimization V2 已正式 CLOSED。

在最终横向代码检查中发现一个独立于 V2 Closure 的 Review 并发控制问题。

当前 `ReviewItemRecord` 已有：

```text
current_version: int = 1
```

并且 Review decision API 已经存在：

```text
expected_version
```

但当前实现存在四个事实：

1. `current_version` 基本只被读取/比较，没有在 ReviewItem 状态成功变化后单调递增；
2. `ApplicationRepository.decide_review_item()` 当前采用：
   ```text
   SELECT current item
   → Python 检查 status/version
   → 修改状态
   → commit
   ```
   检查和写入不是数据库级 CAS；
3. `ReviewService.list_queue()` 当前队列 DTO 没有返回 `current_version`；
4. 前端 `ReviewWorkbenchView.decide()` 当前虽然调用的 API client 支持 `expected_version`，但没有传入该字段。

因此两个审核请求如果真正并发读取同一个：

```text
ReviewItem.status = in_review
ReviewItem.current_version = 1
```

理论上都可能在提交前认为自己仍然有权裁决。

典型风险：

```text
Reviewer / Tab A                   Reviewer / Tab B
      │                                  │
      ├── read in_review v1              ├── read in_review v1
      │                                  │
      ├── approved                       ├── rejected
      │                                  │
      ▼                                  ▼
 ReviewDecision A                  ReviewDecision B
 accepted / verified              rejected / rejected
```

如果两个 transaction 发生竞争，可能产生：

- 两条相互冲突的 ReviewDecision；
- ReviewItem/Finding 最终状态由最后提交者覆盖；
- 用户 A 和用户 B 都收到“成功”；
- `expected_version` 无法发挥 optimistic concurrency 的作用。

此外，即使没有真正同时提交，当前 `current_version` 不递增还存在 ABA 风险：

```text
用户旧页面：
in_review v1
        │
另一个操作：
accepted
→ reopen
→ in_review

因为 current_version 仍是 1

旧页面提交 decision
→ 无法识别这是新的审核轮次
```

---

# 2. 本轮修复目标

本轮只解决 Review lifecycle concurrency。

最终必须达到：

```text
ReviewItem.current_version
```

成为真实的、单调递增的 **Review lifecycle revision**。

任何成功的 ReviewItem 状态变化：

```text
claim
release
decision
reopen
Finding re-review activation
其它生产路径中的 status transition
```

都必须：

```text
current_version = current_version + 1
```

并且 Review decision 必须通过数据库原子 CAS 获得唯一状态转换权：

```sql
UPDATE review_items
SET
    status = :target_status,
    current_version = current_version + 1
WHERE
    id = :item_id
    AND status = :expected_status
    AND current_version = :expected_version
```

只有：

```text
affected_rows == 1
```

的 transaction 可以继续写 Finding 和 ReviewDecision。

另一个并发 transaction 必须：

```text
review_version_conflict
```

且：

```text
0 ReviewDecision
0 partial Finding change
```

---

# 3. 本轮明确不做什么

不得把这个任务扩大成：

- 多用户账号；
- Organization / Tenant；
- RBAC；
- Reviewer role；
- 多人 assignment redesign；
- Review cycle 新表；
- Review round 新表；
- WebSocket 协作；
- 数据库 schema migration；
- Finding 状态机重构；
- Review object type 重构；
- Agent Harness 重构；
- 新页面；
- Optimization V3。

当前：

```text
current_version
```

字段已经存在，因此本轮 **不新增 Alembic migration**。

---

# 4. 本轮工作包

必须按顺序执行：

```text
RH0  建立并发硬化基线
RH1  固定 current_version 生命周期语义
RH2  Review decision 改为数据库 CAS
RH3  所有 ReviewItem 状态变化统一递增版本
RH4  Queue/API 暴露真实 current_version
RH5  Review Workbench 提交 expected_version 并处理冲突
RH6  并发/ABA/版本专项测试
RH7  Full regression + Browser regression + delivery 记录
```

---

# 5. 必须保持的现有系统不变量

## 5.1 Finding Review submit

当前已经完成：

```text
Finding + unique ReviewItem
```

单事务。

继续保留：

```text
ApplicationRepository.submit_finding_for_review()
```

作为唯一 Finding Review submit path。

## 5.2 Finding Review decision

继续保持：

```text
ReviewItem + ReviewDecision + Finding
```

单事务。

只是将 ReviewItem 的“获得状态转换权”改为 CAS。

## 5.3 Finding Review reopen

继续保持：

```text
ReviewItem + Finding
```

单事务，并继续执行：

- same Case；
- target exists；
- Review/Finding 状态配对；
- superseded 不可复活。

## 5.4 Generic Review API / Harness

此前已经完成的：

```text
Generic Review API finding
Harness submit_review_item finding
```

统一走原子 Finding submit。

不得退回旧路径。

---

# 6. 当前必须先读取的代码

执行智能体开始修改前必须读取当前 HEAD：

```text
backend/app/infrastructure/database/models.py
backend/app/application/repositories.py
backend/app/application/review_service.py
backend/app/api/routes/reviews.py
backend/app/services/review.py
backend/tests/test_review.py
backend/tests/test_findings.py

frontend/src/services/api.ts
frontend/src/types/api.ts
frontend/src/views/ReviewWorkbenchView.vue
frontend/src/views/ReviewWorkbenchView.test.ts   # 若当前存在
frontend/e2e-interact.cjs

docs/optimization-v2-delivery.md
```

并全仓搜索：

```text
current_version
ReviewItemRecord.status
update_review_item_status
claim_review_item
release_review_item
reopen_review_item_atomic
submit_finding_for_review
decide_review_item
```

目的：

> 确认所有真正修改 ReviewItem.status 的生产路径。

禁止只修改本文列出的几个函数而不做全仓 status writer audit。

---

# 7. RH0 — 建立并发硬化记录

在：

```text
docs/optimization-v2-delivery.md
```

末尾另起独立章节。

不要再把它称为 Optimization V2 Closure。

写：

```markdown
# Post-V2 Review Decision Concurrency Hardening

Status: IN PROGRESS
Baseline HEAD: 2a2d1e6e523fbb2d26824c4bdd4b463553dcad11

Optimization V2 remains CLOSED.

This independent hardening patch addresses ReviewItem optimistic
concurrency: current_version was not advancing with lifecycle transitions,
queue UI did not receive a reliable version, and decision persistence used
a read/check/write sequence instead of a database CAS.
```

明确：

```text
Optimization V2 不重新打开。
```

---

# 8. RH1 — 固定 current_version 的唯一语义

## 8.1 定义

从本补丁开始：

```text
ReviewItem.current_version
```

定义为：

> ReviewItem lifecycle revision。

初始：

```text
new ReviewItem → current_version = 1
```

每一次成功改变：

```text
ReviewItem.status
```

必须：

```text
current_version += 1
```

---

# 9. 必须递增/不得递增版本的操作

成功状态变化必须递增一次：

```text
unreviewed → in_review                  claim
in_review → unreviewed                  release
unreviewed/in_review → terminal/status  decision
accepted/rejected/needs_more_evidence → in_review  reopen
terminal/needs_more_evidence → in_review  Finding re-review submit
其它合法 ReviewItem status transition
```

不得递增：

```text
新增 comment
Activity Log
读取 queue
读取 decision history
完全幂等 submit（ReviewItem.status 没变化）
失败/rollback/conflict
```

---

# 10. ReviewDecision.object_version 语义

保持现有字段，固定为：

> decision 开始时所依据的 ReviewItem 旧版本。

示例：

```text
ReviewItem in_review v4
approved
→ ReviewDecision.object_version = 4
→ ReviewItem accepted v5
```

禁止把 decision 记录写成新版本 5。

---

# 11. 历史数据

不需要 migration。

历史 ReviewItem 即使：

```text
status=accepted
current_version=1
```

也允许。

从补丁部署后的下一次成功状态变化开始：

```text
1 → 2 → 3 ...
```

无需回推历史轮次。

---

# 12. RH2 — Review Decision 改为数据库原子 CAS【核心】

修改：

```text
backend/app/application/repositories.py
backend/app/application/review_service.py
backend/tests/test_review.py
backend/tests/test_findings.py
```

核心方法：

```text
ApplicationRepository.decide_review_item()
```

---

# 13. 不要只添加 SELECT FOR UPDATE

Finding submit 当前已经有跨对象事务：

```text
lock Finding
→ read/reuse ReviewItem
```

如果 decision 改为：

```text
lock ReviewItem
→ lock Finding
```

会形成相反锁顺序，PostgreSQL 下有潜在死锁。

因此最终 winner 判定使用：

```text
conditional UPDATE / CAS
```

Finding transaction 保持与当前 submit 兼容的对象访问顺序。

---

# 14. Repository decision 目标结构

最终结构：

```text
BEGIN

1. 获取 ReviewItem snapshot
2. 验证基本对象存在
3. 若 object_type=finding：
      require/lock same-case Finding
      require Finding.status == under_review
4. 解析 target Finding status
5. CAS ReviewItem：
      WHERE id
        AND status == expected_status
        AND current_version == expected_version
      SET status = target_status
          current_version = current_version + 1
6. CAS affected_rows != 1：
      ROLLBACK
      return None
7. 若 Finding：
      更新 Finding status
8. INSERT ReviewDecision
      object_version = expected_version
9. COMMIT ONCE
10. reload ReviewItem
11. return ReviewItem + Decision
```

---

# 15. CAS 推荐 SQLAlchemy 写法

必须由数据库 WHERE 完成最终竞争判定：

```python
from sqlalchemy import update as sa_update

result = await session.execute(
    sa_update(ReviewItemRecord)
    .where(
        ReviewItemRecord.id == item_id,
        ReviewItemRecord.status == expected_status,
        ReviewItemRecord.current_version == expected_version,
    )
    .values(
        status=target_status,
        current_version=ReviewItemRecord.current_version + 1,
        updated_at=utc_now(),
    )
)

if result.rowcount != 1:
    await session.rollback()
    return None
```

不要再使用：

```python
if item.current_version == expected_version:
    item.status = ...
    await commit()
```

作为最终并发判定。

优先使用 `rowcount`，避免本轮依赖特定 SQLite `RETURNING` 行为。

由于同一 transaction 中可能已经加载过 `ReviewItemRecord` ORM 实例，执行 Core UPDATE 时应避免让 SQLAlchemy 依赖旧 identity-map 状态。推荐：

```python
stmt = (
    sa_update(ReviewItemRecord)
    .where(...)
    .values(...)
    .execution_options(synchronize_session=False)
)
```

CAS 成功后不要继续信任此前加载的 `item.current_version`；在 commit/flush 后重新 `session.get()` / `refresh()`，以数据库值作为返回结果。

---

# 16. Finding target 与事务

`object_type == "finding"` 时继续复用现有：

```text
_require_finding_review_target()
Finding.status == under_review
Review/Finding mapping
```

不复制状态机。

若 Finding update 或 ReviewDecision insert 失败：

```text
ReviewItem CAS 同样 rollback
```

必须仍是一个 transaction。

---

# 17. 并发结果不变量

给定：

```text
ReviewItem in_review v4
```

A 与 B 同时：

```text
A expected v4 → approved
B expected v4 → rejected
```

必须：

```text
exactly one success
exactly one review_version_conflict
ReviewDecision count += 1
ReviewItem.current_version = 5
```

若为 Finding：

```text
Finding.status 与 winner 一致
```

禁止两个 decision 都成功。

---

# 18. ReviewService.decide 的版本策略

保留当前函数兼容签名：

```python
expected_version: int | None
```

计算：

```python
effective_expected_version = (
    expected_version
    if expected_version is not None
    else item.current_version
)
```

Repository 永远收到确定的 expected version。

这保证旧内部调用者即使未传版本，同时提交也仍由 CAS 只允许一个成功。

但只有显式传 `expected_version` 的客户端能检测跨审核轮次的 stale/ABA，因此第一方 Workbench 必须传版本。

---

# 19. DecideRequest API 策略

当前：

```python
expected_version: int | None = Field(default=None, ge=1)
```

本轮保持 optional，不做 breaking API change。

代码注释明确：

```text
optional for backward compatibility;
first-party Review Workbench always sends expected_version.
```

---

# 20. RH3 — 所有 ReviewItem 状态变化统一递增版本【核心】

执行智能体必须全仓审计 ReviewItem status writers。

## claim_review_item

成功：

```text
unreviewed → in_review
```

同一原子 UPDATE：

```text
current_version + 1
```

失败：

```text
version 不变
```

## release_review_item

成功：

```text
in_review → unreviewed
```

版本 +1。

## reopen_review_item_atomic

成功：

```text
terminal / needs_more_evidence → in_review
```

ReviewItem version +1，Finding 继续同事务进入 `under_review`。

## submit_finding_for_review

- 新 ReviewItem：version=1；
- existing item status 不变：版本不变；
- existing item 被重新激活到 `in_review`：版本 +1。

## update_review_item_status

如果仍有生产调用并改变 status：

```text
status changed → version +1
```

---

# 21. 全仓 status writer audit

完成后必须搜索：

```text
ReviewItemRecord.status =
.values(status=
update(ReviewItemRecord)
update_review_item_status(
```

所有生产写路径必须归类：

```text
状态变化 → +1
状态不变 → 不增
只读 → 无关
```

delivery 文档列出审计过的方法。

---

# 22. RH4 — Queue/API 暴露真实 current_version

当前 `_item_summary()` 已返回 `current_version`，但 `ReviewService.list_queue()` 当前 queue item 没有该字段。

修改：

```text
backend/app/application/review_service.py
```

queue DTO 加：

```python
"current_version": record.current_version,
```

测试：

```text
new item queue version == 1
claim 后 queue version == 2
```

---

# 23. Frontend type contract

检查：

```text
frontend/src/types/api.ts
```

`ReviewQueueItem`。

从本补丁开始必须：

```ts
current_version: number
```

为 required。

如果当前已经是 required，保持。

---

# 24. RH5 — Workbench 提交 expected_version

修改：

```text
frontend/src/views/ReviewWorkbenchView.vue
frontend/src/services/api.ts
frontend tests
```

当前 decision：

```ts
api.reviewDecide(caseId, item.id, {
  decision,
  reason,
})
```

改为：

```ts
api.reviewDecide(caseId, item.id, {
  decision,
  reason,
  expected_version: item.current_version,
})
```

API client 已支持 `expected_version`，不要新增第二套方法。

---

# 25. Version conflict UX

后端：

```text
review_version_conflict
```

时必须：

1. 显示：
   ```text
   该审核项已被其他操作更新，请基于最新状态重新审核。
   ```
2. 自动 reload queue；
3. 不自动重试；
4. 保留用户输入的 reason。

禁止：

```text
reload → 自动带新 version 重放旧 decision
```

Human Review 必须重新确认。

---

# 26. claim/release/reopen 与版本

本轮不强制改变这三个 HTTP endpoint 的 request body。

但它们成功后必须因为 RH3 使 `current_version` 增长。

Workbench 当前 action 成功后会 reload queue，因此后续 decision 会拿到最新版本。

真正多人 Reviewer/RBAC 阶段可再决定是否让所有 mutation endpoint 都强制提交 expected version；本轮不扩大 API。

---

# 27. ABA 保护

示例：

```text
用户 A：
in_review v2

用户 B：
approve → accepted v3
reopen  → in_review v4

用户 A：
decision expected_version=2
```

结果必须：

```text
review_version_conflict
```

即使 status 再次回到 `in_review`。

---

# 28. RH6 — Backend 专项测试

建议新增：

```text
backend/tests/test_review_concurrency.py
```

避免继续扩大 `test_review.py`。

必须覆盖：

### C1 Queue version

```text
new → queue current_version=1
```

### C2 Claim

```text
unreviewed v1 → in_review v2
```

### C3 Release

```text
in_review v2 → unreviewed v3
```

### C4 Decision

```text
in_review v2 → accepted v3
ReviewDecision.object_version=2
```

### C5 Reopen

```text
accepted v3 → in_review v4
```

Finding 同步：

```text
verified → under_review
```

### C6 Finding re-review

```text
accepted vN → in_review vN+1
same ReviewItem
```

### C7 Idempotent submit

ReviewItem status 不变化：

```text
version 不变化
```

### C8 Stale explicit version

```text
current v4
request expected v3
→ review_version_conflict
→ 0 decision
→ 0 Finding change
```

### C9 ABA regression

```text
snapshot in_review v2
approve → v3
reopen → v4
old expected v2
→ conflict
```

### C10 Sequential CAS duplicate

同一：

```text
expected_status
expected_version
```

第一 decision 成功，第二返回 None/conflict。

ReviewDecision 只增加 1。

### C11 Concurrent opposite decisions

两个独立 coroutine/transaction：

```text
approved
rejected
```

同 expected version。

必须：

```text
success count = 1
conflict count = 1
ReviewDecision count = 1
version +1
```

Finding 状态匹配 winner。

---

# 29. SQLite 与 PostgreSQL 并发测试规则

项目常用 SQLite。

可以尝试 SQLite `asyncio.gather`，但禁止把：

```text
database is locked
```

当成 `review_version_conflict`。

若 SQLite true-concurrency test 不稳定：

- C10 确定性 CAS regression 必须保留；
- C11 增加 PostgreSQL integration test。

建议环境变量：

```text
TEST_POSTGRES_URL
```

只允许专用测试数据库/Schema。

禁止使用生产数据库，也不要要求当前账号必须有 `CREATEDB`。

如果没有 `TEST_POSTGRES_URL`：

```text
skip with explicit reason
```

delivery 如实记录，不伪称已执行 PostgreSQL race test。

若有 PostgreSQL：

```text
two sessions + barrier + opposite decisions
```

exactly one winner。

---

# 30. 并发测试规则

测试不得断言：

```text
approved 一定赢
```

只能断言：

```text
one winner / one loser
```

并根据 winner 验证最终 Finding/ReviewItem 状态。

Loser 必须：

```text
0 ReviewDecision
0 Finding overwrite
```

---

# 31. 非 Finding Review 回归

至少保留一个：

```text
claim / evidence
```

ReviewItem 的 decision 回归，证明 CAS/version 不是 Finding 专属。

---

# 32. RH6 — Frontend tests

至少新增/修改：

### F1

queue mock：

```text
current_version=7
```

页面显示：

```text
版本 v7
```

### F2

点击 approve：

```json
{
  "decision": "approved",
  "expected_version": 7
}
```

### F3

mock `review_version_conflict`：

- 显示指定冲突文案；
- reload queue；
- 不自动第二次 decision。

---

# 33. Browser E2E

原 A–F 全部重新运行。

建议增加：

```text
Scenario G — Review stale decision protection
```

不要求用两个真实浏览器窗口。

稳定方式：

```text
1. UI 打开 Review Workbench，记录 version=N
2. 通过合法另一流程使 item：
      decision → terminal
      reopen → in_review
   version > N
3. 使用旧 expected_version=N 提交
4. 后端 review_version_conflict
5. UI reload
6. 显示最新 version
7. 旧 decision 未写入
```

真正 simultaneous race 由 backend test 负责。

---

# 34. Error semantics

继续统一使用：

```text
review_version_conflict
```

用于：

```text
expected status 不匹配
expected version 不匹配
CAS rowcount == 0
```

Finding target missing/state mismatch 继续使用已存在的具体错误码，不混成 version conflict。

---

# 35. Mutation API 返回值

任何成功状态 mutation 返回的 ReviewItem 必须带**新版本**：

```text
claim:
v1 → response v2

decision:
request expected v2
→ response v3

reopen:
v3 → response v4
```

---

# 36. Activity / Comment / Assignment

Activity Log：

- 不参与 version；
- 失败不回滚已成功 Review transaction。

Comments：

- 不递增 ReviewItem version。

Assignment：

- 不重构 assignment 模型；
- 但 claim/release 引起 ReviewItem.status 变化，因此 ReviewItem version 必须递增。

---

# 37. 禁止实现

不得使用：

```text
asyncio.Lock
threading.Lock
全局内存锁
```

作为并发权威。

不得只使用：

```text
SELECT FOR UPDATE
```

而不建立 CAS/version 协议。

不得：

```text
Python check
→ normal attribute assignment
→ commit
```

继续作为最终 winner 判定。

不得只让 decision 增版本，而 reopen/claim/release 不增。

不得 GET 时增版本。

不得失败/rollback 时增版本。

不得冲突后自动重试用户 decision。

---

# 38. 推荐 Commit

推荐：

```text
fix: harden review decision concurrency
```

包含 backend CAS/version/tests。

然后：

```text
fix: send review versions from workbench
```

包含 queue contract、frontend conflict UX、frontend/E2E、delivery。

也可以单 commit：

```text
fix: enforce optimistic concurrency for review lifecycle
```

但不得混入其它产品功能。

---

# 39. Backend targeted gate

至少运行：

```text
test_review.py
test_findings.py
test_review_concurrency.py（若新增）
test_claim_review.py
test_legacy_compatibility.py
```

以及现有 Review API Consistency tests。

---

# 40. 本轮默认不要求 Backend Full Regression

本次修复允许**不运行整个 backend/tests 全量回归**。

原因：

- Optimization V2 在前序版本提交前已经多次完成完整全量回归；
- 当前补丁没有 schema / migration；
- 不修改 Database abstraction、session factory、Harness runtime、crawler、Evidence、Report、Network 等大范围基础能力；
- 修改集中在 Review lifecycle concurrency 与第一方 Review Workbench；
- 新增的 CAS/version 行为可以通过一组高密度专项与邻接回归直接证明。

因此本轮默认采用：

```text
Targeted regression
+
Review-adjacent regression
+
Frontend gates
+
Review browser regression
```

而不是再次机械运行全部 800+ backend tests。

---

# 40A. 必跑的 Backend Targeted Regression

至少运行：

```text
backend/tests/test_review.py
backend/tests/test_findings.py
backend/tests/test_review_concurrency.py      # 若新增
backend/tests/test_claim_review.py
backend/tests/test_legacy_compatibility.py
```

以及当前仓库中实际覆盖以下生产路径的测试文件：

```text
Harness submit_review_item / review tool
Review API Consistency
Finding Review submit/reopen/decision
```

如果对应测试在：

```text
test_tool_registry.py
test_tool_system.py
```

则一并运行。

建议最终固定为：

```text
test_review.py
test_review_concurrency.py
test_findings.py
test_claim_review.py
test_tool_registry.py
test_tool_system.py
test_legacy_compatibility.py
```

文件不存在时按当前仓库实际名称替换，但不得直接删除对应能力的回归覆盖。

要求：

```text
0 failed
0 unexpected skipped
```

---

# 40B. 邻接回归的覆盖目标

这组测试必须能证明：

```text
Review generic object workflow 不回归
Finding submit/re-review 不回归
Finding decision/reopen 不回归
Harness review tool 不回归
legacy Review API compatibility 不回归
```

不要求重新运行与本次变更无依赖关系的：

```text
crawler
media
monitor
signals
timeline
network algorithms
report rendering
RAG
sandbox
unrelated agent experts
```

测试。

---

# 40C. 什么时候必须升级为 Full Regression

以下任一情况出现，执行智能体才必须运行全部 backend tests：

### Trigger 1 — 修改范围越界

最终 diff 触及：

```text
backend/app/infrastructure/database/models.py
backend/app/infrastructure/database/database.py
backend/app/bootstrap.py
backend/app/main.py
backend/migrations/*
核心 Harness runtime / graph / tool execution
```

或其它明显的共享基础设施文件。

### Trigger 2 — 引入 schema/migration

如果执行过程中发现必须修改数据库 schema：

```text
立即恢复 full regression requirement
```

并说明为什么原计划“不需要 migration”的假设失效。

### Trigger 3 — CAS 为了兼容而修改共享 Repository/Session 行为

例如修改：

```text
session factory
SQLite pragma
transaction isolation
global retry
database timeout
```

必须全量回归。

### Trigger 4 — Targeted/adjacent regression 出现无法局部解释的失败

如果失败扩散到 Review 之外，不能只修专项测试后结束。

### Trigger 5 — 执行智能体实际修改文件超过本计划范围

例如为了修并发而改动：

```text
Evidence / Report / Network / Collection / Agent Harness
```

必须升级全量。

---

# 40D. 不跑 Full Regression 时 delivery 的正确表述

不能写：

```text
Full backend regression passed
```

而应写：

```markdown
Backend regression strategy:
Risk-based targeted regression was used for this narrow post-V2 concurrency patch.

The previous Optimization V2 baseline had already passed the complete backend
regression matrix. This patch did not modify schema, migrations, database
bootstrap/session infrastructure, Harness runtime, or unrelated Investigation
domains.

Executed:
- Review targeted regression: ...
- Finding / Review adjacent regression: ...
- Harness Review tool regression: ...
- Legacy Review compatibility: ...

Result:
N/N targeted and adjacent tests green, 0 failed, 0 unexpected skipped.

Full backend regression was intentionally not re-run for this narrow patch.
None of the escalation triggers defined in the execution plan were met.
```

这样能准确区分：

```text
“此前完整基线已通过”
```

和：

```text
“本次窄补丁使用风险分层回归”
```

禁止把此前的 872/872 直接当成本次代码的全量测试结果。

---

# 41. Frontend gate

运行：

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

全部通过。

---

# 42. Browser gate — 本轮可缩小到 Review 相关闭环

不强制再次执行完整 A–F。

本轮最低必须执行：

```text
通用 smoke（确保应用可启动/路由正常）
Scenario B — Finding Review 主闭环
Scenario G — Review stale/ABA protection（本轮新增）
```

Scenario B 必须继续证明：

```text
Finding submit
→ Review Workbench
→ claim
→ decide
→ reopen
→ 再次 decide
```

Scenario G 必须证明：

```text
旧 expected_version
→ review_version_conflict
→ queue reload
→ 无自动重试
```

如果现有 `e2e-interact.cjs` 无法按场景选择、运行 A–F 的额外成本很低，可以继续整套执行；但这不是本轮 mandatory gate。

只有当本次修改触碰全局 Router、Shell、shared HTTP client 基础行为或其它 Investigation 页面时，才要求重新跑完整 A–F。

要求：

```text
Review-related browser scenarios = green
0 unexpected console/pageerror
```

---

# 43. GitHub CI 说明

当前评审 HEAD 未发现 GitHub status/workflow runs。

delivery 准确写：

```text
Regression evidence is based on repository-local executed tests.
No GitHub commit status checks were available for this HEAD.
```

如果执行后已有 CI，则附实际结果。

---

# 44. delivery 结果模板

在 `optimization-v2-delivery.md` 追加独立章节：

```markdown
# Post-V2 Review Decision Concurrency Hardening Result

Status: COMPLETED

Baseline:
2a2d1e6e523fbb2d26824c4bdd4b463553dcad11

Optimization V2 remains CLOSED.

## Version semantics
- ReviewItem.current_version is now a monotonic lifecycle revision.
- Every successful ReviewItem status transition increments it exactly once.
- Idempotent/non-state operations do not increment it.
- ReviewDecision.object_version stores the pre-transition version.

## Decision CAS
- Review decision uses atomic conditional UPDATE on
  id + expected_status + expected_version.
- Exactly one competing decision can win.
- CAS loser returns review_version_conflict.
- Finding + ReviewDecision stay in the same transaction as the winner.

## UI
- Review queue returns current_version.
- Review Workbench sends expected_version.
- Version conflict reloads the queue and requires explicit user confirmation.

## Tests
- Targeted backend: ...
- ABA regression: passed.
- Concurrent opposite decision: ...
- PostgreSQL integration: passed / not executed (TEST_POSTGRES_URL unavailable).
- Backend regression strategy: targeted + adjacent (full regression intentionally skipped unless escalation triggers fired).
- Targeted/adjacent backend: N/N green.
- Full backend regression: not re-run / executed because trigger <reason>.
- Frontend gates: passed.
- Browser Review Scenario B: passed.
- Scenario G stale review: passed.
- Unexpected console/pageerror: 0.
```

---

# 45. Final Definition of Done

## Version contract

```text
[ ] initial version = 1
[ ] claim +1
[ ] release +1
[ ] decision +1
[ ] reopen +1
[ ] Finding re-review activation +1
[ ] idempotent submit does not increment
[ ] comments/activity/read do not increment
[ ] all ReviewItem status writers audited
```

## Decision atomicity

```text
[ ] DB conditional UPDATE/CAS
[ ] predicate includes id
[ ] predicate includes expected_status
[ ] predicate includes expected_version
[ ] winner increments version exactly once
[ ] loser creates 0 ReviewDecision
[ ] loser changes 0 Finding state
[ ] winner keeps ReviewItem + Finding + Decision in one transaction
[ ] ReviewDecision.object_version = pre-transition version
```

## API/UI

```text
[ ] queue returns current_version
[ ] ReviewQueueItem.current_version required
[ ] Workbench sends expected_version
[ ] stale explicit version → review_version_conflict
[ ] conflict reloads queue
[ ] no automatic decision retry
```

## Tests

```text
[ ] version lifecycle tests
[ ] stale version test
[ ] ABA test
[ ] sequential CAS duplicate
[ ] true concurrent race where environment permits
[ ] exactly one winner
[ ] exactly one ReviewDecision
[ ] Finding final state matches winner
[ ] non-Finding regression
```

## Gates

```text
[ ] targeted backend green
[ ] Review-adjacent backend regression green
[ ] full backend regression NOT required unless an escalation trigger is met
[ ] if a full-regression trigger is met, full backend green
[ ] frontend typecheck
[ ] frontend lint
[ ] frontend tests
[ ] frontend build
[ ] browser smoke
[ ] Browser Scenario B green
[ ] Browser Scenario G green
[ ] 0 unexpected console/pageerror
[ ] delivery accurately records the chosen regression strategy
```

---

# 46. 完成后的并发模型

```text
ReviewItem in_review v8

Reviewer A                 Reviewer B
expected v8                expected v8
approve                    reject
    │                         │
    └──── DB CAS ─────────────┘

A:
WHERE status=in_review AND version=8
→ rowcount=1
→ WIN
→ accepted v9

B:
WHERE status=in_review AND version=8
→ rowcount=0
→ review_version_conflict
```

最终：

```text
ReviewDecision count += 1
```

而不是 2。

Finding 类型：

```text
Winner:
ReviewItem + Finding + ReviewDecision
COMMIT

Loser:
no write
```

---

# 47. 完成后的 ABA 模型

```text
旧页面：
in_review v8

另一操作：
approve → accepted v9
reopen  → in_review v10

旧页面：
decision expected v8
```

结果：

```text
review_version_conflict
```

即使 status 又回到 `in_review`，旧审核轮次也不能作用于新轮次。

---

# 47A. 提交前必须做一次 Diff Scope Check

在决定“不跑全量回归”前，执行智能体必须查看最终：

```text
git diff --name-only <baseline>...HEAD
```

或等价变更文件列表。

如果最终修改只落在：

```text
backend/app/application/repositories.py
backend/app/application/review_service.py
backend/app/api/routes/reviews.py           # 如确有 API contract 注释/小改
backend/tests/test_review*.py
backend/tests/test_findings.py              # 相关回归

frontend/src/views/ReviewWorkbenchView.vue
frontend/src/services/api.ts
frontend/src/types/api.ts
frontend Review tests
frontend/e2e-interact.cjs

docs/optimization-v2-delivery.md
本执行计划文档
```

则可以继续使用风险分层回归。

如果出现计划外共享基础设施文件，必须重新判断并按 Trigger 规则升级测试。

---

# 48. 最终工程目标

本补丁不改变 Review 产品功能。

它只是把已经存在的：

```text
current_version
expected_version
```

从“表面字段”变成真正的数据库并发协议。

完成后 Review 层应同时具备：

```text
状态机
Human Review Authority
Finding transaction consistency
Case scope
monotonic lifecycle version
database CAS
stale-client conflict UX
```

这将为后续真正进入：

```text
Multi-user Review
Reviewer Assignment
RBAC
Organization / Workspace
```

提供可靠的并发基础。

完成本文后，不应继续扩大本任务范围。
