# Nothing-in-the-dark Optimization V2 最终评审结果与 Post-Closure Correctness Patch 执行计划

> 文档性质：Optimization V2 Final Closure 最终评审结果 + 最后一个阻塞问题的确定性修复规格  
> 评审仓库：`Ethan-Martinez-creater/Nothing-in-the-dark`  
> 评审基线 HEAD：`e4bd0796464b24e65fb2d9c3bf48b4e11152a051`（`docs: finalize optimization v2 final closure`）  
> 关联文档：
> - `docs/Nothing-in-the-dark_Optimization_Execution_Plan_V2.md`
> - `docs/optimization-v2-review-and-closure-plan.md`
> - `docs/optimization-v2-final-closure-execution-plan-corrected.md`
> - `docs/optimization-v2-delivery.md`
>
> 面向对象：负责继续直接修改仓库、运行测试和提交实现的执行智能体。  
> 本文不是新一轮产品优化计划。除本文明确列出的最后一个正确性问题外，Optimization V2 已有架构和功能均视为稳定基线，不允许重新设计。

---

# 1. 最终评审结论

本次 Final Closure 修复已经解决上一轮确认的主要问题：

- Propagation Edge 已从二义性 Boolean 改为 `unreviewed / confirmed / rejected` 显式三态；
- Alembic `20260830_0049` migration 已实现保守回填和 downgrade；
- 手动 Finding 创建已实现 Finding + SourceLink + EvidenceLinks 单事务写入；
- Unassigned Evidence 已恢复为可浏览、可选中、可进入 Copilot Context 的调查数据；
- Generic Finding citation 已实现 Report ↔ Finding 双向 Provenance；
- 无效的 `assert ... or True` 已删除；
- Browser E2E A–F 已从 API smoke 升级为真实 UI interaction；
- Frontend typecheck / lint / test / build 已有完整通过记录；
- Backend 全量测试已按文件集合 1:1 覆盖执行，并最终全部 green。

因此，本轮不再存在需要重新设计 Investigation IA、Evidence Workspace、Propagation Network、Signals、Report、Collection、Copilot 等产品架构的问题。

但是，最终评审发现了 **1 个新的事务一致性 Blocker**：

> **Finding 状态进入 `under_review` 与对应 ReviewItem 的创建/重新激活当前不是同一数据库事务。**

当前路径是：

```text
FindingService.update_status(...)
    ↓
FindingRepository.update_status(...)
    ↓ COMMIT
Finding.status = under_review

然后

ReviewService.submit_item(...)
    ↓ 另一个 session / transaction
创建或读取 ReviewItem
```

如果第二步失败，会产生：

```text
Finding.status = under_review
ReviewItem = 不存在
```

这是产品状态不变量破坏。

此外，当前 Browser Scenario B 在 UI 提交审核后又显式调用：

```http
POST /cases/{case_id}/reviews/items
```

人工补建 ReviewItem，因此会掩盖上述问题。

---

# 2. 当前最终评级

| 项目 | 评审结果 |
|---|---|
| FC1 Propagation Review Tri-state | ✅ 通过 |
| FC1 Migration 0049 | ✅ 通过 |
| FC2 Finding Manual Creation Atomicity | ✅ 通过 |
| FC3 Unassigned Evidence | ✅ 通过 |
| FC4 Generic Provenance | ✅ 通过 |
| FC5 Evidence/Copilot E2E | ✅ 通过 |
| FC5 Report Publish E2E | ✅ 通过 |
| FC5 Propagation E2E | ✅ 通过 |
| FC5 Live Data E2E | ✅ 通过 |
| FC5 Signals E2E | ✅ 通过 |
| Finding Review 正常功能 | 🟢 可用 |
| Finding → Review 提交事务一致性 | 🔴 最后一个 Closure Blocker |
| Backend regression | 🟢 有完整执行记录 |
| Frontend gates | ✅ 通过 |
| Browser gate | 🟡 Scenario B 目前存在 masking |
| Optimization V2 正式 CLOSED | ⏸ 等待本文补丁完成 |

---

# 3. 本次补丁范围

本次只允许完成以下工作：

```text
PC0  记录 Post-Closure 修复基线
PC1  Finding → Review 原子提交事务
PC2  Finding Review 状态重入/历史不一致恢复
PC2B Review Workbench → Finding 原子重开事务
PC3  修改 Browser Scenario B，移除人工补建 ReviewItem，并验证 Workbench 重开
PC4  专项测试与最终回归
PC5  修正 optimization-v2-delivery.md 测试记录并正式 CLOSED
```

不允许扩展：

- 新 Agent
- 新 Investigation 页面
- 新 Review 数据模型
- 新 Alembic migration
- RBAC
- 多人协作
- Public Sharing
- 新 Propagation 算法
- 新 Evidence 类型
- 新 Report 状态
- 新 E2E 框架

---

# 4. 必须保持不变的系统不变量

本次修复不得破坏以下已经完成的设计。

## 4.1 Finding 终审权限

必须继续保持：

```text
verified / rejected
```

只能来自：

```text
Review decision transaction
```

普通 Finding API 不得直接设置终审态。

---

## 4.2 Review Decision 原子事务

现有：

```text
ApplicationRepository.decide_review_item()
```

已经能够在一个事务内：

```text
ReviewItem.status
+ ReviewDecision
+ Finding.status
```

同步更新。

**不得重写或拆分这条路径。**

---

## 4.3 ReviewItem 唯一约束

数据库已有：

```text
UNIQUE(case_id, object_type, object_id)
```

因此同一个 Finding 永远只允许一个 ReviewItem。

本次复审必须**重用并重新激活既有 ReviewItem**，不得创建第二个 ReviewItem。

---

## 4.4 Harness 保护区

不得修改：

- LangGraph
- Durable Run
- SSE
- Approval
- Sandbox
- Tool Registry
- crawler
- Agent Expert
- Context Builder

本次修复与 Harness 无关。

---

# 5. 问题根因

当前 `FindingService.update_status()` 的关键逻辑是：

```python
updated = await self._findings.update_status(finding_id, status)

if status == "under_review":
    await self._reviews.submit_item(...)
```

其中：

```text
FindingRepository.update_status()
```

已经单独 commit。

`ReviewService.submit_item()` 随后通过 `ApplicationRepository.create_review_item()` 使用另一个 session 再 commit。

因此无法保证：

```text
Finding under_review
⇔
存在有效 ReviewItem
```

同时，`ReviewService.submit_item()` 当前的幂等策略是：

```text
若 ReviewItem 已存在
→ 原样返回
```

这会引出第二个问题：

当 Finding 已经：

```text
verified
```

且 ReviewItem 已经：

```text
accepted
```

用户重新发起复审：

```text
verified Finding → under_review
```

现有逻辑会：

```text
Finding = under_review
ReviewItem = accepted
```

因为唯一约束阻止新建，而 `submit_item()` 又不会重新激活 existing ReviewItem。

因此本次补丁必须同时解决：

1. 首次提交审核的事务原子性；
2. 重复提交的幂等；
3. 历史 `under_review + no ReviewItem` 的恢复；
4. verified/rejected Finding 的重新复审。

---

# 6. PC0 — 建立 Post-Closure 修复记录

## 修改文件

```text
docs/optimization-v2-delivery.md
```

## 执行

在文件末尾新增：

```markdown
# Optimization V2 Post-Closure Correctness Patch

Status: IN PROGRESS
Baseline HEAD: e4bd0796464b24e65fb2d9c3bf48b4e11152a051
Reason: Finding under_review 与 ReviewItem 创建/重新激活不是同一事务。
```

不要删除之前的 Final Closure 记录。

之前的 `CLOSED` 作为当时交付记录保留，但新增说明：

```text
Final reviewer found one post-closure transaction consistency blocker.
The final CLOSED status is superseded until PC1–PC5 pass.
```

## 建议提交

不要单独提交 PC0。

与 PC1 一起提交即可。

---

# 7. PC1 — 新增 Finding → Review 原子提交事务【唯一核心修复】

## 7.1 修改文件

主要修改：

```text
backend/app/application/repositories.py
backend/app/application/finding_service.py
backend/tests/test_findings.py
```

原则上不需要修改：

```text
backend/app/infrastructure/database/models.py
backend/migrations/*
backend/app/services/review.py
```

如果执行智能体发现必须修改以上三个文件，应重新核对是否偏离本文方案。

---

# 8. PC1.1 — 在 ApplicationRepository 新增唯一事务入口

新增方法，名称固定建议：

```python
async def submit_finding_for_review(
    self,
    *,
    case_id: str,
    finding_id: str,
    summary: str,
    actor: str = "finding_submit_review",
) -> tuple[FindingRecord, ReviewItemRecord]:
    ...
```

不要把该方法放入 `FindingRepository`。

原因：

- `FindingRepository` 当前只负责 Finding 三张新表；
- ReviewItem 位于既有 ApplicationRepository Review 域；
- `ApplicationRepository` 已经持有 `FindingRecord`、`ReviewItemRecord` 并承担 `decide_review_item()` 的跨对象原子同步；
- 因此这里是现有代码中最合理的事务边界。

---

# 9. PC1.2 — 单事务必须完成的操作顺序

必须使用：

```python
async with self._database.session_factory() as session:
```

一个 session。

推荐逻辑如下。

## Step 1：锁定 Finding

PostgreSQL 路径建议：

```python
finding = await session.scalar(
    select(FindingRecord)
    .where(FindingRecord.id == finding_id)
    .with_for_update()
)
```

随后验证：

```text
Finding exists
AND finding.case_id == case_id
```

跨 Case 与不存在继续使用当前 Finding API 的统一 not-found/scope 语义，不泄漏其他 Case。

SQLite 对 `FOR UPDATE` 的处理由 SQLAlchemy/dialect 决定，不要为了 SQLite 建立另一套业务路径。

---

## Step 2：读取唯一 ReviewItem

```python
review_item = await session.scalar(
    select(ReviewItemRecord).where(
        ReviewItemRecord.case_id == case_id,
        ReviewItemRecord.object_type == "finding",
        ReviewItemRecord.object_id == finding_id,
    )
)
```

不得：

```text
list 1000 items
→ Python for loop
```

原子事务必须直接查询唯一对象键。

---

# 10. PC1.3 — 明确状态行为表

执行智能体不得自行决定不同状态如何处理。

必须按下表实现。

| Finding 当前状态 | ReviewItem 当前状态 | 提交审核结果 |
|---|---|---|
| `candidate` | 不存在 | 创建 `unreviewed` ReviewItem；Finding → `under_review` |
| `candidate` | `unreviewed` | 复用；Finding → `under_review` |
| `candidate` | `in_review` | 复用；Finding → `under_review` |
| `candidate` | `needs_more_evidence` | 将 ReviewItem → `in_review`；Finding → `under_review` |
| `candidate` | `accepted/rejected/superseded` | 将 ReviewItem → `in_review`；Finding → `under_review` |
| `under_review` | 不存在 | 作为历史修复：创建 `unreviewed` ReviewItem；Finding 保持 |
| `under_review` | `unreviewed/in_review/needs_more_evidence` | 幂等返回，不重复创建 |
| `under_review` | `accepted/rejected/superseded` | 历史不一致恢复：ReviewItem → `in_review` |
| `verified` | `accepted` | ReviewItem → `in_review`；Finding → `under_review` |
| `verified` | 其他合法既有状态 | 统一重新激活到 `in_review`；Finding → `under_review` |
| `verified` | 不存在 | 历史修复：创建 `in_review` ReviewItem；Finding → `under_review` |
| `rejected` | `rejected` | ReviewItem → `in_review`；Finding → `under_review` |
| `rejected` | 其他合法既有状态 | 统一重新激活到 `in_review`；Finding → `under_review` |
| `rejected` | 不存在 | 历史修复：创建 `in_review` ReviewItem；Finding → `under_review` |
| `superseded` | 任意 | 拒绝，不允许重新提交审核 |

注意：

- 首次 `candidate` 提交创建 `unreviewed`，表示进入审核队列、尚未领取。
- 已裁决 Finding 重新复审时 ReviewItem 进入 `in_review`，与现有 `ReviewService.reopen()` 的领域语义保持一致。
- 不允许为同一 Finding 创建第二个 ReviewItem。

---

# 11. PC1.4 — Review 状态迁移必须复用现有 domain validator

当前：

```text
backend/app/services/review.py
```

已有：

```python
validate_transition(current, target)
```

以及：

```text
accepted → in_review
rejected → in_review
needs_more_evidence → in_review
superseded → in_review
```

当 existing ReviewItem 需要重新激活时，必须调用当前 domain validator。

可以在 repository 方法内部局部 import：

```python
from app.services import review as review_domain
```

然后：

```python
review_domain.validate_transition(review_item.status, "in_review")
```

禁止：

- 复制 `_VALID_TRANSITIONS`
- 新写另一套 Review 状态机
- 不验证直接赋值

对于：

```text
unreviewed / in_review
```

无需无意义转换。

---

# 12. PC1.5 — 创建 ReviewItem 的字段

首次创建时固定：

```python
ReviewItemRecord(
    case_id=case_id,
    object_type="finding",
    object_id=finding_id,
    summary=summary,
    priority=0,
    risk_level="low",
    queue="default",
    status="unreviewed",   # candidate/under_review 首次修复
)
```

若来源 Finding 已是：

```text
verified / rejected
```

但 ReviewItem 历史缺失，则创建：

```text
status="in_review"
```

不要新增 Review cycle 表。

---

# 13. PC1.6 — Finding 状态更新

完成 ReviewItem 创建/重新激活后：

```python
finding.status = "under_review"
```

然后：

```python
await session.commit()
```

必须只有一次 commit。

结构应类似：

```text
SELECT/LOCK Finding
SELECT ReviewItem
VALIDATE
INSERT/UPDATE ReviewItem
UPDATE Finding
OPTIONAL Activity Log
COMMIT ONCE
REFRESH
RETURN
```

不得：

```text
commit Finding
→ commit ReviewItem
```

---

# 14. PC1.7 — Activity Log

建议在同一 transaction 中新增：

```python
CaseActivityLogRecord(
    case_id=case_id,
    activity_type="review_item_submitted",
    summary=f"提交审核项：finding:{finding_id}",
    actor=actor,
    metadata_json={
        "object_type": "finding",
        "object_id": finding_id,
    },
)
```

但只在本次调用**实际改变了 Finding 或 ReviewItem 状态**时新增。

对于完全幂等调用：

```text
Finding=under_review
ReviewItem=unreviewed/in_review
```

不重复写 activity。

如果执行智能体认为将 Activity 写进 transaction 会与当前记录约定冲突，可不在本方法中新增 Activity，但必须确保：

> Activity logging failure 不得导致已经成功的 Finding/Review transaction 向用户返回失败。

不得恢复原来“主状态先 commit，日志/Review 再失败”的模式。

---

# 15. PC1.8 — 并发与唯一约束

数据库已有：

```text
uq_review_item_object
(case_id, object_type, object_id)
```

该约束必须保留作为最终防线。

同时使用 Finding row lock，避免 PostgreSQL 下同一 Finding 两个并发提交同时判断 `ReviewItem is None`。

如果仍因数据库竞争产生 `IntegrityError`：

1. rollback 当前 transaction；
2. 重新读取 Finding + ReviewItem；
3. 如果最终状态已经满足：
   ```text
   Finding = under_review
   AND ReviewItem exists
   ```
   则作为幂等成功返回；
4. 否则重新抛异常。

不要简单吞掉所有 `IntegrityError`。

---

# 16. PC2 — FindingService 改为使用唯一原子入口

## 当前错误逻辑

删除：

```python
updated = await self._findings.update_status(finding_id, status)

if status == "under_review":
    await self._reviews.submit_item(...)
```

---

## 新逻辑

`FindingService.update_status()`：

### 目标为 `under_review`

**必须在普通 `ALLOWED_TRANSITIONS` 校验之前分支处理。**

原因：

- 重复提交 `under_review → under_review` 必须幂等；
- 历史 `under_review + no ReviewItem` 必须能被修复；
- 如果先执行当前 `ALLOWED_TRANSITIONS` 检查，这两类请求会在进入原子方法前就被拒绝。

推荐控制流：

```python
if status in REVIEW_ONLY_STATUSES:
    raise finding_review_required

record = await self.get_for_case(case_id, finding_id)

if status == "under_review":
    finding, review_item = await self._repository.submit_finding_for_review(
        case_id=case_id,
        finding_id=finding_id,
        summary=record.statement,
    )
    return finding

transition = (record.status, status)
if transition not in ALLOWED_TRANSITIONS:
    raise finding_invalid_transition

return await self._findings.update_status(finding_id, status)
```

不得在 `status == "under_review"` 之前执行普通 transition table 拒绝。

### 其他普通状态

继续复用现有：

```python
self._findings.update_status(...)
```

### `verified/rejected`

继续在最开始被：

```text
finding_review_required
```

阻止。

---

# 17. PC2.1 — 不再由 FindingService 调用 ReviewService.submit_item

本路径应删除：

```python
self._reviews = ReviewService(repository)
```

如果 `FindingService` 中没有其他地方使用 `_reviews`，则删除该依赖和 import。

不要为了兼容留下死代码。

`ReviewService.submit_item()` 继续保留，因为其他 review object 仍可能使用它。

---

# 18. PC2.2 — `under_review → candidate` 现有行为

本补丁不扩展 Finding 产品状态机。

现有：

```text
under_review → candidate
```

若仍存在普通 API 使用，应保持。

但是必须增加一个明确测试，记录当前行为对 ReviewItem 的影响。

建议本次同时做一个最小一致性处理：

当：

```text
Finding under_review → candidate
```

时，如果存在 ReviewItem：

- `unreviewed`：保持 item，但它不应继续出现在“当前待审核 Finding”队列；
- 若当前 Review Workbench 查询没有依据 Finding 状态过滤，则建议同步将 ReviewItem 置为 `superseded` 会破坏后续复审，因此**本轮不要自行改变 ReviewItem**。

因此本补丁只解决“提交审核”的原子性，不继续扩展撤回审核语义。

如果产品当前没有暴露 under_review→candidate UI，这不是本轮 Closure Blocker。

---


# 18A. PC2B — Review Workbench 的“重开”也必须原子同步 Finding

## 当前遗漏

当前：

```text
ReviewService.reopen()
```

执行：

```text
review_domain.validate_transition(item.status, "in_review")
→ ApplicationRepository.update_review_item_status(item_id, "in_review")
```

只更新 `ReviewItem.status`。

当该 ReviewItem 的：

```text
object_type == "finding"
```

并且对应 Finding 当前为：

```text
verified / rejected
```

时，从 Review Workbench 点击“重开”会形成：

```text
ReviewItem = in_review
Finding = verified / rejected
```

这与本文要建立的核心不变量冲突。

因此必须同时修复 **Finding 页面发起复审** 和 **Review Workbench 发起重开** 两条入口。

---

## 18A.1 新增 Repository 原子重开方法

在：

```text
backend/app/application/repositories.py
```

新增建议方法：

```python
async def reopen_review_item_atomic(
    self,
    *,
    item_id: str,
    case_id: str | None = None,
) -> ReviewItemRecord:
    ...
```

该方法使用一个 session / transaction：

```text
1. SELECT/LOCK ReviewItem
2. case_id 存在时校验 scope
3. 调 review_domain.validate_transition(current, "in_review")
4. 若 item.object_type == "finding":
      SELECT/LOCK Finding
      校验 finding.case_id == item.case_id
      Finding.status = under_review
5. ReviewItem.status = in_review
6. COMMIT ONCE
7. refresh + return
```

不得：

```text
先 update ReviewItem commit
→ 再 update Finding
```

---

## 18A.2 非 Finding ReviewItem 行为保持原样

对于：

```text
evidence
claim
propagation_edge
alignment_candidate
risk_assessment
hypothesis
report_conclusion
```

该方法只需要：

```text
ReviewItem → in_review
```

不引入其它对象状态同步。

不要为了本次修复扩展所有 Review object 的状态模型。

---

## 18A.3 ReviewService.reopen 改接原子方法

修改：

```text
backend/app/application/review_service.py
```

当前：

```python
review_domain.validate_transition(item.status, "in_review")
updated = await self._repository.update_review_item_status(item_id, "in_review")
```

改为：

```python
updated = await self._repository.reopen_review_item_atomic(
    item_id=item_id,
    case_id=case_id,
)
```

状态机校验只保留一个权威实现。

推荐由 repository 原子方法内部调用：

```python
review_domain.validate_transition(...)
```

因此 `ReviewService.reopen()` 不再在事务外先校验后写入。

Activity log 继续在主事务成功后按现有 `_log()` 方式记录即可；日志失败不得回滚已经成功的主状态事务。

---

## 18A.4 Review assignment 不在本轮重构

当前 Review Workbench：

- `unreviewed` 提供“领取”
- `in_review` 可以直接进入决定区
- `accepted/rejected/needs_more_evidence` 提供“重开”

本轮不要新增 review cycle / assignment reset 模型。

如果既有历史 assignment 仍存在，保持当前行为。

核心要求仅为：

```text
Finding ReviewItem 被重开
⇒ ReviewItem=in_review AND Finding=under_review
```

---

## 18A.5 Backend 必须新增测试

### Test A：accepted Finding 从 Workbench 重开

构造真实流程：

```text
candidate Finding
→ submit review
→ approved
→ Finding verified
→ ReviewItem accepted
```

调用：

```python
ReviewService.reopen(...)
```

断言：

```text
ReviewItem = in_review
Finding = under_review
同一个 ReviewItem.id
```

### Test B：rejected Finding 从 Workbench 重开

同理：

```text
ReviewItem rejected
Finding rejected
→ reopen
→ ReviewItem in_review
→ Finding under_review
```

### Test C：重开事务失败 0 partial write

模拟 transaction 在 commit 前失败。

重新读取：

```text
ReviewItem 仍 accepted/rejected
Finding 仍 verified/rejected
```

不能出现只更新一边。

### Test D：非 Finding item 回归

例如 Claim ReviewItem：

```text
accepted → reopen → in_review
```

保持原行为，不访问 Finding 表。

---

## 18A.6 Browser Scenario B 必须覆盖 Workbench 重开

Scenario B 在首次 approve 并验证 Finding `verified` 后，再增加：

```text
B9  回到 Review Workbench
B10 展开同一 accepted Finding ReviewItem
B11 点击“重开”
B12 ReviewItem 显示 in_review
B13 返回 Findings
B14 Finding 显示 under_review
B15 回 Review Workbench，对同一 item 再次接受
B16 返回 Findings，再次显示 verified
```

不得通过 API 修改 ReviewItem/Finding 来完成这些步骤。

这证明两个生产入口都满足同一个一致性不变量：

```text
Finding UI submit
AND
Review Workbench reopen
```

---

## 18A.7 可选但建议的小型 UI 修正

当前 Review Workbench 的 `OBJECT_LABELS` 若仍没有：

```ts
finding: '调查结论'
```

建议在同一补丁中补上。

这不属于 Closure blocker，但避免 Finding Review 卡片直接显示英文 `finding`。

不得因此重构 Review Workbench UI。

---


# 19. PC3 — 修改 Browser Scenario B，去掉 masking

## 修改文件

```text
frontend/e2e-interact.cjs
```

## 必须删除

Scenario B 中人工创建 Review item 的：

```javascript
await api.post(
  BASE_API + '/cases/' + cid + '/reviews/items',
  ...
)
```

这个请求不能再存在。

---

# 20. PC3.1 — 新 Scenario B 固定流程

必须改为：

```text
1. 打开 Investigation → Findings
2. 找到 candidate Finding
3. 点击 Finding
4. 点击“提交审核”
5. UI 等待 Finding 显示“审核中”
6. 不调用任何 Review 创建 API
7. 直接导航 /admin/reviews
8. 选择当前 Investigation
9. 等待 Review card 出现
10. 断言 card object/finding summary 对应本 Finding
11. 领取（若需要）
12. 点击接受
13. 等待 Review 显示已接受
14. 返回 Investigation → Findings
15. 断言 Finding = verified
```

---

# 21. PC3.2 — E2E 允许的 API 使用边界

Scenario B 中 API 只允许：

### A. 负路径

```http
POST /findings/{id}/status
{"status":"verified"}
```

断言 422。

### B. Read-only 验证

允许 GET Review queue，用于断言：

```text
exactly one ReviewItem
```

但不能通过 API 创建、修复或改变 ReviewItem。

### C. 所有 Review 状态变化

必须由 UI：

```text
Submit
→ Claim
→ Approve
```

完成。

---

# 22. PC3.3 — Scenario B 新增断言

至少增加：

```text
B1 candidate Finding visible
B2 submit review UI
B3 Finding under_review
B4 direct verified API blocked
B5 Review Workbench automatically contains Finding item
B6 exactly one ReviewItem for Finding
B7 claim + approve via UI
B8 Finding verified after return
```

其中 B5 是本次补丁最重要的 E2E 证明。

---

# 23. PC4 — Backend 专项测试

## 修改文件

```text
backend/tests/test_findings.py
```

必要时可以新增：

```text
backend/tests/test_finding_review_submission.py
```

但为了保持当前测试布局，优先扩展 `test_findings.py`。

---

# 24. PC4.1 — 首次提交原子成功

测试：

```text
candidate Finding
ReviewItem 不存在
```

执行：

```python
service.update_status(..., "under_review")
```

断言：

```text
Finding.status == under_review
ReviewItem count for object == 1
ReviewItem.status == unreviewed
```

---

# 25. PC4.2 — 重复提交幂等

测试 repository 原子方法直接调用两次：

```python
submit_finding_for_review(...)
submit_finding_for_review(...)
```

断言：

```text
Finding = under_review
ReviewItem count == 1
```

不能因为重复调用产生 IntegrityError。

---

# 26. PC4.3 — ReviewItem 写入失败整体回滚

必须有测试证明：

```text
Finding 状态修改
+
ReviewItem 创建
```

处于同一 transaction。

建议使用 monkeypatch 模拟当前 transaction 在 commit/flush 时失败。

例如：

```python
monkeypatch AsyncSession.commit
```

在目标 transaction 抛：

```text
SQLAlchemyError
```

或使用项目当前测试惯例构造数据库错误。

恢复 monkeypatch 后重新查询：

```text
Finding.status == candidate
ReviewItem 不存在
```

这条测试是本次补丁最核心的 backend regression。

不要只测试：

```text
raise exception
```

必须查询数据库证明 0 partial write。

---

# 27. PC4.4 — 历史不一致自动修复

构造：

```text
Finding.status = under_review
ReviewItem 不存在
```

直接调用 repository 原子方法。

断言：

```text
Finding 仍 under_review
ReviewItem 被创建
ReviewItem.status = unreviewed
```

用于兼容在当前补丁之前可能存在的脏状态。

---

# 28. PC4.5 — verified Finding 重新复审

构造真实流程：

```text
candidate
→ submit
→ Review accepted
→ Finding verified
→ ReviewItem accepted
```

然后：

```python
service.update_status(..., "under_review")
```

断言：

```text
Finding = under_review
同一个 ReviewItem.id
ReviewItem = in_review
ReviewItem count = 1
```

禁止创建第二 ReviewItem。

---

# 29. PC4.6 — rejected Finding 重新复审

同上：

```text
Finding rejected
ReviewItem rejected
```

再次提交：

```text
Finding under_review
ReviewItem in_review
同一个 ReviewItem
```

---

# 30. PC4.7 — superseded Finding 拒绝

执行：

```text
Finding = superseded
submit_finding_for_review()
```

必须失败：

```text
finding_invalid_transition
```

不得重新创建/激活 ReviewItem。

---

# 31. PC4.8 — 并发提交

若当前 SQLite 测试环境稳定支持：

```python
asyncio.gather(
    submit(...),
    submit(...),
)
```

至少断言最终：

```text
ReviewItem count = 1
Finding = under_review
```

如果 SQLite 锁导致测试本身不稳定，可以将并发测试作为 PostgreSQL integration test，而 unit test 保留唯一约束 + 幂等重复调用。

不得为了通过测试提高全局 retry 次数。

---

# 32. PC4.9 — Review Decision 回归

必须继续运行现有：

- approved → Finding verified
- rejected → Finding rejected
- optimistic version conflict → Finding 不改变
- verified → under_review → 再次 verified 仍必须经过 Review

这些现有测试不得删除。

---

# 33. PC5 — Frontend / Browser Gate

本次不需要新增 Vue 页面。

只需确保 E2E 修改不影响前端。

运行：

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

必须全部通过。

然后真实启动：

```text
E2E disposable SQLite backend
VITE_E2E=true frontend
```

运行：

```bash
npm run e2e:smoke
npm run e2e:interact
```

或仓库对应的：

```bash
node e2e-smoke.cjs
node e2e-interact.cjs
```

重点记录：

```text
Closure A–F
0 failed
0 skipped
0 unexpected console/pageerror
```

Scenario B 结果必须明确说明：

> ReviewItem solely created/reopened by the Finding submit production path; E2E does not call POST /reviews/items.

---

# 34. Backend 回归要求

专项测试完成后，至少运行：

```text
test_findings.py
test_claim_review.py
test_provenance.py
test_report_documents.py
test_legacy_compatibility.py
```

然后按当前已经验证过的全量分片方法重新运行全部：

```text
backend/tests/*.py
```

要求：

```text
所有 unique tests 最终 green
0 unresolved failures
0 unexpected skipped
```

如果仍出现已知 SQLite `database is locked`：

- 必须记录首次失败；
- 允许串行复跑对应文件确认是否为并发基础设施 flake；
- 不得把真实业务失败标记成 flaky。

---

# 35. 修正 delivery 文档中的回归计数表述

当前文档写：

```text
844 + 1 re-run = 845 passed executions
```

这不够准确。

若测试集合实际包含 845 个 unique tests，应改为：

```text
Full regression contains 845 unique tests.

First pass:
844 passed / 1 failed / 0 skipped.

The single failing test was caused by an OS-level SQLite lock under xdist.
Its containing file (11 tests) was re-run serially:
11 passed / 0 failed.

Final unique-test status:
845 / 845 green.

If raw executions including the re-run are counted:
856 total executions = 855 passed executions + 1 first-pass failed execution.
```

以最终本次补丁重跑后的实际数字为准。

不要继续使用：

```text
844 + 1 re-run = 845 executions
```

这种把“测试数”和“执行次数”混为一谈的表述。

---

# 36. PostgreSQL Migration 记录

本次不新增 migration。

`20260830_0049` 保持不动。

`docs/optimization-v2-delivery.md` 中可以继续保留：

- SQLite 0049 revision logic upgrade/downgrade 已通过；
- PostgreSQL offline DDL 双向验证已通过；
- 本地 PG 用户缺少 CREATEDB，因此未运行 disposable full-chain verifier。

这是已知环境限制，不是本文 blocker。

不要为了本次 Finding Review 修复再次修改 0049 migration。

---

# 37. 推荐代码形态

最终结构应该变为：

```text
FindingService.update_status()
│
├─ verified/rejected
│    └─ finding_review_required
│
├─ target == under_review
│    └─ ApplicationRepository.submit_finding_for_review()
│          └─ ONE DATABASE TRANSACTION
│              ├─ lock Finding
│              ├─ get/reuse/create ReviewItem
│              ├─ reactivate ReviewItem if needed
│              ├─ set Finding under_review
│              ├─ optional Activity
│              └─ commit once
│
└─ other normal Finding transitions
     └─ FindingRepository.update_status()
```

Review Decision 保持：

```text
ReviewService.decide()
    ↓
ApplicationRepository.decide_review_item()
    ↓ ONE DATABASE TRANSACTION
ReviewItem + ReviewDecision + Finding
```

这样形成完整闭环：

```text
提交审核：
Finding + ReviewItem 原子

审核工作台重开：
ReviewItem + Finding 原子

最终裁决：
ReviewItem + ReviewDecision + Finding 原子
```

---

# 38. 禁止实现方式

执行智能体不得采用以下替代方案。

## 禁止 1

```text
先 Finding commit
try create Review
失败时再把 Finding 改回 candidate
```

这是 compensation，不是原子事务。

---

## 禁止 2

让前端：

```text
POST finding under_review
POST reviews/items
```

承担一致性。

前端不是事务协调器。

---

## 禁止 3

E2E 中继续人工：

```http
POST /reviews/items
```

然后声称 Finding Submit 闭环通过。

---

## 禁止 4

为复审创建第二 ReviewItem。

数据库已有唯一约束。

---

## 禁止 5

删除唯一约束：

```text
uq_review_item_object
```

来解决复审问题。

---

## 禁止 6

新增 `review_cycle` / `review_round` 等新表。

本轮只修最终 blocker，不扩大 Review 架构。

---

## 禁止 7

复制 `review.py` 的状态机。

必须复用现有 domain validator。

---

# 39. 建议 Commit

本次代码修复建议只需要一个核心 commit：

```text
fix: make finding review submission atomic
```

内容包括：

- ApplicationRepository atomic method
- FindingService integration
- backend tests
- E2E Scenario B correction

然后一个文档/验收 commit：

```text
docs: finalize optimization v2 after atomic review closure
```

不要再次拆成十几个 commit。

---

# 40. 最终验收矩阵

## Backend Domain

```text
[ ] candidate → submit → under_review + ReviewItem
[ ] exactly one ReviewItem
[ ] duplicate submit idempotent
[ ] transaction failure → Finding unchanged + no ReviewItem
[ ] historical under_review/no item repaired
[ ] verified → re-review reuses item
[ ] rejected → re-review reuses item
[ ] superseded cannot re-review
[ ] direct verified/rejected API still blocked
[ ] Review approve → verified atomic
[ ] Review reject → rejected atomic
```

## Browser Scenario B

```text
[ ] Finding candidate visible
[ ] UI submit review
[ ] Finding under_review
[ ] no POST /reviews/items in E2E
[ ] Review Workbench automatically shows item
[ ] exactly one item
[ ] UI claim
[ ] UI approve
[ ] Finding verified after return
[ ] Review Workbench reopen accepted Finding
[ ] ReviewItem becomes in_review
[ ] Finding becomes under_review in the same logical operation
[ ] UI approve the same ReviewItem again
[ ] Finding returns to verified
```

## Existing Browser A–F

```text
[ ] Scenario A Evidence/Copilot
[ ] Scenario B Finding Review
[ ] Scenario C Report Gate
[ ] Scenario D Propagation Tri-state
[ ] Scenario E Live Data Posts
[ ] Scenario F Signals
[ ] 0 Closure skipped
[ ] 0 unexpected console/pageerror
```

## Frontend

```text
[ ] typecheck
[ ] lint
[ ] test
[ ] build
```

## Backend

```text
[ ]专项 tests
[ ] full regression
[ ] 0 unresolved failures
[ ] 0 unexpected skipped
```

---

# 41. `Optimization V2 CLOSED` 最终判定条件

只有全部满足以下条件，才允许重新确认正式 CLOSED：

```text
[ ] Finding under_review 与 ReviewItem 创建在同一事务
[ ] 事务失败不存在 partial write
[ ] verified/rejected re-review 重用既有 ReviewItem
[ ] historical under_review/no ReviewItem 可恢复
[ ] Review Workbench reopen Finding 原子同步 ReviewItem + Finding
[ ] Review Workbench reopen 失败不存在 partial write
[ ] Browser Scenario B 不再手动创建 ReviewItem
[ ] Browser Review 完整 UI 闭环通过
[ ] Backend Review/Finding 回归通过
[ ] Backend full regression 最终全部 green
[ ] Frontend 4 gates 全通过
[ ] Browser A–F 全通过
[ ] delivery 文档测试数字表述已纠正
```

完成后，`docs/optimization-v2-delivery.md` 追加：

```markdown
# Optimization V2 Post-Closure Correctness Patch Result

Status: CLOSED

The final reviewer blocker was resolved:
Finding submission to review now atomically updates the Finding and
creates/reopens its unique ReviewItem in one database transaction.

Browser Scenario B no longer creates a ReviewItem through an auxiliary API.
The complete Finding → Review Workbench → approval → verified path is now
validated through the production UI flow.
```

---

# 42. 本轮结束后的正式结论

本文补丁完成后，Optimization V2 的最后一条业务一致性缺口将被封闭。

届时核心调查链为：

```text
Agent Artifact
    ↓ deterministic
Candidate Finding
    ↓ atomic submit
Finding under_review + unique ReviewItem
    ↓ Human Review
Review Decision
    ↓ atomic decision
Finding verified/rejected
    ↓ provenance
Report Draft
    ↓ citation gate
Published Report
```

这意味着：

- Agent 负责认知与生成候选结论；
- Evidence 保证事实来源；
- Finding 是产品层稳定结论；
- Review 是唯一最终裁决边界；
- Finding 与 Review 在提交和决策两个方向都具备数据库事务一致性；
- Report Published 仍由确定性 Citation Gate 控制；
- Browser E2E 真正证明完整用户路径，而不是通过测试脚本人工修复中间状态。

完成本文后，不应再继续 Optimization V2 Closure 返工。

下一步可以正式进入新的产品演进阶段。
