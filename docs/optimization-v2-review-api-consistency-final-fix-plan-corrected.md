# Nothing-in-the-dark Optimization V2 Review API Consistency 最终修复执行计划

> 文档性质：Optimization V2 Post-Closure 最终评审结果 + Generic Review API 旁路封口实施规格  
> 评审仓库：`Ethan-Martinez-creater/Nothing-in-the-dark`  
> 当前评审基线 HEAD：`e2f60b8a10138779700a7af741dddd73ea3dcc22`  
> 关键修复提交基线：`f1e03c96048789e649fd5f89b87054373bbe59b2`  
> 当前交付文档：`docs/optimization-v2-delivery.md`  
> 面向对象：负责直接修改仓库、运行测试并提交结果的执行智能体。
>
> 本文不是新一轮产品规划。Optimization V2 的 Investigation IA、Evidence、Finding、Network、Timeline、Signals、Collection、Report、Copilot、Propagation tri-state、Browser E2E 等现有成果均视为稳定基线。执行智能体不得重新设计这些模块。

---

# 1. 本轮最终评审结论

上一轮 Post-Closure Correctness Patch 已经正确完成：

- Finding 页面提交审核：`Finding + ReviewItem` 单事务；
- `candidate / under_review / verified / rejected` 的提交/复审语义；
- 唯一 ReviewItem 幂等复用；
- Review Workbench 重开 Finding：`ReviewItem + Finding` 单事务；
- Review decision：`ReviewItem + ReviewDecision + Finding` 单事务；
- Browser Scenario B 已移除人工 `POST /reviews/items` masking；
- Backend full regression：交付记录为 `858 / 858` unique tests green；
- Frontend typecheck/lint/test/build 通过；
- Browser A–F 交付记录为 `44 / 44`，0 skipped，0 unexpected console/pageerror。

因此，主 UI 生产路径已经正确。

但是最终评审继续沿所有公开 Review API 检查后，确认仍存在一个系统级旁路：

```http
POST /api/v1/cases/{case_id}/reviews/items
```

当前会直接调用：

```text
ReviewService.submit_item()
```

而 `ReviewService.submit_item()` 对 `object_type="finding"` 仍走通用 ReviewItem 创建逻辑：

```text
验证 object_type
→ list existing ReviewItems
→ create_review_item()
```

它没有：

- 验证 Finding 是否真实存在；
- 验证 Finding 是否属于当前 Case；
- 把 Finding 原子置为 `under_review`；
- 复用已经完成的 `submit_finding_for_review()` 状态行为表。

因此当前仍可以形成：

```text
ReviewItem = unreviewed
Finding = candidate
```

甚至：

```text
ReviewItem.object_type = finding
ReviewItem.object_id = nonexistent-id
```

另外，当前 `decide_review_item()` 和 `reopen_review_item_atomic()` 对 Finding target 不存在/跨 Case 时仍采用“找不到就跳过 Finding、继续修改 ReviewItem”的宽松行为。

这意味着：

> **Finding Review 的正确性现在依赖调用者是否选择了正确入口，而不是由后端统一不变量强制保证。**

本次修复目标就是封闭这一条最后的 Review API 旁路。

---

# 2. 当前最终评级

| 项目 | 当前结果 |
|---|---|
| Finding UI submit 原子性 | ✅ |
| Finding submit 幂等 / historical repair | ✅ |
| verified/rejected re-review | ✅ |
| Review Workbench reopen 原子性 | ✅ |
| Review final decision 原子性 | ✅ |
| Browser Scenario B masking | ✅ 已消除 |
| Generic `POST /reviews/items` + finding | 🔴 绕开原子入口 |
| Generic Review submit + nonexistent Finding | 🔴 可制造 dangling ReviewItem |
| Finding Review decision + dangling target | 🔴 当前 fail-open |
| Finding Review reopen + dangling target | 🔴 当前 fail-open |
| Optimization V2 系统级不变量 | 🟠 尚差最后一个旁路封口 |
| 正式 CLOSED | ⏸ 本补丁完成后重新确认 |

---

# 3. 本次修复范围

严格限制为以下工作包：

```text
RC0  开启 Review API Consistency Patch 记录
RC1  Generic Review submit 对 finding 强制路由到原子入口
RC2  Finding Review decision target fail-closed
RC3  Finding Review reopen target fail-closed
RC4  API / transaction regression tests
RC5  全量回归与 delivery 文档最终 CLOSED
```

不得新增：

- 数据库表；
- Alembic migration；
- Review cycle / Review round；
- 新 Review 状态；
- 新 Finding 状态；
- 新前端页面；
- 新 E2E 框架；
- 新 Agent；
- RBAC；
- 多人协作；
- 其它 Optimization V3 功能。

---

# 4. 本次必须建立的最终系统不变量

## 4.1 Finding ReviewItem 只能指向真实 Finding

对于任何新创建的：

```text
ReviewItem.object_type == "finding"
```

必须满足：

```text
Finding.id == ReviewItem.object_id
AND
Finding.case_id == ReviewItem.case_id
```

## 4.2 所有 Finding Review submit 只能走一个原子入口

无论入口是：

```text
Investigation Findings UI
```

还是：

```http
POST /cases/{case_id}/reviews/items
```

只要：

```text
object_type == "finding"
```

最终都必须进入：

```text
ApplicationRepository.submit_finding_for_review()
```

禁止存在第二套 ReviewItem 创建逻辑。

## 4.3 Finding Review 的终审仍只能来自 Human Review

必须继续保持：

```text
verified / rejected
```

只能由：

```text
ApplicationRepository.decide_review_item()
```

中的 Review decision transaction 产生。

普通 Finding status API 不得恢复直接终审能力。

## 4.4 Finding Review decision/reopen 必须 fail closed

如果 ReviewItem 声称：

```text
object_type == "finding"
```

但：

```text
Finding 不存在
OR
Finding.case_id != ReviewItem.case_id
```

那么：

```text
decision / reopen
```

必须整体失败。

禁止继续修改 ReviewItem 或写 ReviewDecision。

---

# 5. 当前代码真实接线点

执行前必须阅读：

```text
backend/app/application/review_service.py
backend/app/application/repositories.py
backend/app/api/routes/reviews.py
backend/app/application/finding_service.py
backend/app/services/review.py
backend/app/domain/enums.py
backend/tests/test_review.py
backend/tests/test_findings.py
frontend/e2e-interact.cjs
docs/optimization-v2-delivery.md
```

当前必须复用的现有能力：

```text
ApplicationRepository.submit_finding_for_review()
ApplicationRepository.reopen_review_item_atomic()
ApplicationRepository.decide_review_item()
ReviewService.submit_item()
ReviewService.decide()
ReviewService.reopen()
review_domain.OBJECT_TYPES
review_domain.validate_transition()
REVIEW_STATUS_TO_FINDING_STATUS
```

---

# 6. RC0 — 开启最后一次 API Consistency Patch 记录

修改：

```text
docs/optimization-v2-delivery.md
```

在文件末尾追加：

```markdown
# Optimization V2 Review API Consistency Patch

Status: IN PROGRESS
Baseline HEAD: e2f60b8a10138779700a7af741dddd73ea3dcc22

Final review found one remaining public API bypass:
generic POST /reviews/items could still create Finding ReviewItems without
using the atomic Finding review submission path, and dangling Finding
ReviewItems could still be decided/reopened fail-open.
```

不要删除之前任何 CLOSED 记录。

新增说明：

```text
The prior CLOSED declaration is superseded until RC1–RC5 pass.
```

RC0 不要求独立 commit，可与 RC1 一起提交。

---

# 7. RC1 — Generic Review Submit 对 Finding 强制路由到原子入口【核心】

## 7.1 修改文件

```text
backend/app/application/review_service.py
backend/app/application/repositories.py
backend/app/application/finding_service.py
backend/tests/test_review.py
backend/tests/test_findings.py
```

通常不需要修改：

```text
backend/app/api/routes/reviews.py
```

现有公开 endpoint 保留兼容：

```http
POST /api/v1/cases/{case_id}/reviews/items
```

---

# 8. RC1.1 — 扩展 submit_finding_for_review 参数以保持 Review API 兼容

当前方法接收 Finding 审核提交参数，但 Review generic API 还允许：

```text
priority
risk_level
queue
summary
```

本轮固定方案：

```python
async def submit_finding_for_review(
    self,
    *,
    case_id: str,
    finding_id: str,
    priority: int = 0,
    risk_level: str = "low",
    queue: str = "default",
    actor: str = "finding_submit_review",
) -> tuple[FindingRecord, ReviewItemRecord]:
    ...
```

其中：

- `priority` / `risk_level` / `queue` 继续兼容 generic Review API；
- Finding Review 的 `summary` 不再信任客户端输入；
- canonical summary 统一使用数据库中的：

```text
finding.statement
```

原因：ReviewItem 对 Finding 的摘要必须描述真实被审核对象，不能由客户端提交任意不一致文本。

---

# 9. RC1.2 — ReviewItem 首次创建字段规则

在 `submit_finding_for_review()` 已锁定并验证 Finding 后：

```python
review_summary = finding.statement
```

首次 ReviewItem 固定：

```python
ReviewItemRecord(
    case_id=case_id,
    object_type="finding",
    object_id=finding.id,
    summary=review_summary,
    priority=priority,
    risk_level=risk_level,
    queue=queue,
    status=item_status,
)
```

---

# 10. RC1.3 — Existing ReviewItem 不覆盖 metadata

若唯一 ReviewItem 已经存在：

```text
priority
risk_level
queue
summary
```

默认保持已有值。

不要因为重复 `POST /reviews/items` 而偷偷修改审核队列配置。

这保证 generic submit 是幂等“提交/复审”动作，而不是隐式 update API。

---

# 11. RC1.4 — Finding UI 调用同步调整

当前 `FindingService.update_status(..., "under_review")` 调用原子方法时如果仍传：

```python
summary=record.statement
```

删除该参数。

改为：

```python
finding, _review_item = await self._repository.submit_finding_for_review(
    case_id=case_id,
    finding_id=finding_id,
)
```

保持当前 UI 行为不变。

---

# 12. RC1.5 — ReviewService.submit_item 的 Finding 早分支

当前 generic 逻辑：

```text
validate object_type
→ list_review_items(limit=1000)
→ existing scan
→ create_review_item
→ activity
```

改为：

```python
if object_type not in review_domain.OBJECT_TYPES:
    ...

if object_type == "finding":
    _finding, item = await self._repository.submit_finding_for_review(
        case_id=case_id,
        finding_id=object_id,
        priority=priority,
        risk_level=risk_level,
        queue=queue,
        actor=actor,
    )
    return item

# 其他 object type 继续当前 generic logic
...
```

必须在：

```text
list_review_items()
create_review_item()
add_activity_log()
```

之前 early return。

原因：`submit_finding_for_review()` 已经负责：

- Activity；
- 唯一 ReviewItem；
- Finding 状态；
- re-review；
- 历史不一致修复；
- 并发兜底。

如果 ReviewService 再执行 generic activity，会重复记录。

---

# 13. RC1.6 — Submit 行为矩阵保持原 Post-Closure 语义

Generic API 对 Finding 必须与 Findings UI 完全一致：

| Finding 状态 | 结果 |
|---|---|
| `candidate` | 创建/复用 ReviewItem；Finding → `under_review` |
| `under_review` | 幂等；确保 ReviewItem 存在 |
| `verified` | 复用同一 ReviewItem → `in_review`；Finding → `under_review` |
| `rejected` | 复用同一 ReviewItem → `in_review`；Finding → `under_review` |
| `superseded` | 拒绝 `finding_invalid_transition` |
| Finding 不存在 | 拒绝 |
| Finding 属于其他 Case | 拒绝 |

不得在 `ReviewService.submit_item()` 重新实现这一矩阵。

---


# 13A. RC1.7 — Repository 低层防线：禁止直接 create Finding ReviewItem

仅修改 `ReviewService.submit_item()` 还不足以建立真正的后端不变量，因为未来内部代码仍可能直接调用：

```python
ApplicationRepository.create_review_item(
    ReviewItemRecord(object_type="finding", ...)
)
```

因此必须在现有：

```text
ApplicationRepository.create_review_item()
```

增加低层防线。

固定行为：

```python
if record.object_type == "finding":
    raise ApplicationError(
        "finding review item must use the atomic finding review submission path",
        code="review_finding_atomic_submit_required",
    )
```

然后只有：

```text
submit_finding_for_review()
```

可以在其自己的单事务 session 内直接 `session.add(ReviewItemRecord(...))` 创建 Finding ReviewItem。

这不会影响：

- claim
- evidence
- propagation_edge
- alignment_candidate
- risk_assessment
- hypothesis
- report_conclusion

等其它通用 Review object。

## 为什么必须增加这一层

本轮最终目标不是：

> “公开 Route 恰好调用正确 Service”。

而是：

> **系统中不存在第二个可以独立创建 Finding ReviewItem 的 Repository 入口。**

因此：

```text
ReviewService early branch
+
ApplicationRepository.create_review_item guard
```

两层都必须存在。

## Call-site 检查

执行智能体修改方法签名或增加 guard 后，必须全仓搜索：

```text
submit_finding_for_review(
create_review_item(
```

更新所有真实调用点和测试调用点。

不得仅依赖 full regression 偶然发现旧 `summary=` 参数。


# 14. RC1.7 — Error semantics

为了最小改动，generic Review submit 的 Finding 分支直接沿用现有原子方法错误：

```text
不存在      → finding_not_found
跨 Case     → finding_scope_mismatch
superseded  → finding_invalid_transition
```

无需增加第二套 `review_finding_submit_*` 错误码。

---

# 15. RC2 — Finding Review Decision 必须 fail closed【核心】

## 当前错误行为

当前：

```python
if item.object_type == "finding":
    finding = await session.get(FindingRecord, item.object_id)
    if finding is not None and finding.case_id == item.case_id:
        ...
```

随后无论 Finding 是否存在，都会继续：

```text
session.add(decision)
commit
```

这是 fail-open。

---

# 16. RC2.1 — 新增 Repository 内部统一 target helper

在：

```text
backend/app/application/repositories.py
```

新增私有 helper，建议：

```python
async def _require_finding_review_target(
    self,
    session,
    item: ReviewItemRecord,
) -> FindingRecord:
    finding = await session.scalar(
        select(FindingRecord)
        .where(FindingRecord.id == item.object_id)
        .with_for_update()
    )
    if finding is None or finding.case_id != item.case_id:
        raise ApplicationError(
            "review finding target not found",
            code="review_object_not_found",
        )
    return finding
```

实现可以调整 typing，但语义不可改变。

---

# 17. RC2.2 — Missing 与 Cross-case 使用同一错误码

对已经存在的 ReviewItem：

```text
object_id 指向不存在对象
```

和：

```text
object_id 指向其他 Case
```

统一视为：

> 当前 ReviewItem 没有合法的 case-scoped review target。

统一返回：

```text
review_object_not_found
```

避免通过 Review API 泄漏其他 Case 是否存在同 ID 对象。

---

# 18. RC2.3 — decide_review_item 的新执行顺序

必须改成：

```text
BEGIN TRANSACTION

1. SELECT/LOCK ReviewItem
2. 验证 expected_status
3. 验证 expected_version
4. 若 object_type == finding：
      SELECT/LOCK Finding
      不存在/跨 Case → raise review_object_not_found
5. 获得 Finding target status mapping
6. 修改 ReviewItem
7. 修改 Finding
8. INSERT ReviewDecision
9. COMMIT ONCE
```

Finding target 校验必须发生在：

```text
item.status = target_status
session.add(decision)
```

之前。

---

# 19. RC2.4 — Finding status mapping 也必须 fail closed

当前：

```text
REVIEW_STATUS_TO_FINDING_STATUS
```

覆盖全部 Review statuses。

在 Finding decision path 中，若理论上出现：

```python
finding_status is None
```

禁止静默 skip。

应抛：

```text
review_finding_status_mapping_missing
```

正常业务测试不应触发该错误，它属于 defensive invariant。

---

# 20. RC2.5 — Service 层不得做非事务 Finding pre-check

当前：

```text
ReviewService.decide()
→ repository.decide_review_item()
```

结构保留。

Finding target 校验必须位于 `decide_review_item()` 的同一 transaction 内。

禁止：

```text
Service 先查 Finding
→ Repository 后写入
```

否则重新出现 check/write race。

---

# 21. RC3 — Finding Review Reopen 必须 fail closed【核心】

当前：

```python
if item.object_type == "finding":
    finding = ...
    if finding is not None and finding.case_id == item.case_id:
        finding.status = "under_review"

item.status = "in_review"
commit
```

Finding target 无效时仍会提交 ReviewItem。

---

# 22. RC3.1 — 复用统一 helper

改为：

```python
if item.object_type == "finding":
    finding = await self._require_finding_review_target(session, item)
    finding.status = "under_review"
    session.add(finding)
```

找不到时：

```text
raise review_object_not_found
```

整个 transaction rollback。

---


# 22A. RC3.2 — Reopen 必须验证 ReviewItem/Finding 状态配对

Review Workbench 的 reopen 不能把任意真实 Finding 强制改成 `under_review`。

必须按当前 ReviewItem 状态验证 Finding 状态：

| ReviewItem 当前状态 | Finding 必须为 | reopen |
|---|---|---|
| `accepted` | `verified` | 允许 → 两者进入 `in_review / under_review` |
| `rejected` | `rejected` | 允许 → 两者进入 `in_review / under_review` |
| `needs_more_evidence` | `under_review` | 允许 → ReviewItem → `in_review`，Finding 保持 `under_review` |
| `superseded` | `superseded` | **禁止 reopen** |
| 其它状态 | 由现有 `review_domain.validate_transition()` 决定 | 不新增旁路 |

如果 ReviewItem 与 Finding 状态不匹配，例如：

```text
ReviewItem=accepted
Finding=candidate
```

必须 fail closed：

```text
code = review_finding_state_mismatch
```

如果：

```text
Finding=superseded
```

无论 ReviewItem 是 accepted/rejected/superseded，都不得通过 Workbench reopen 将其复活。

建议错误：

```text
finding_invalid_transition
```

或统一：

```text
review_finding_state_mismatch
```

本计划固定推荐使用：

```text
review_finding_state_mismatch
```

以区分 Review/Finding 交叉状态不一致。

## 为什么不能复用 `review_domain.validate_transition()` 单独判断

Review domain 目前允许：

```text
ReviewItem.superseded → in_review
```

但 Finding domain 明确没有：

```text
Finding.superseded → under_review
```

因此 Finding 类型 reopen 必须同时满足两个领域状态机，而不能只看 ReviewItem 状态机。


# 23. RC3.2 — 非 Finding Item 保持原行为

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

仍然使用原通用 Review 逻辑。

本轮不要求为全部 Review object 引入实体存在性同步。

原因：本轮是封闭 Finding 特有的产品状态同步旁路，不扩大 Optimization V2 Closure 边界。

---

# 24. RC3.3 — ReviewService.reopen 保持当前接线

当前：

```text
ReviewService.reopen()
→ ApplicationRepository.reopen_review_item_atomic()
```

是正确的。

不要再增加 Service 层 Finding existence pre-check。

只修 Repository 原子方法。

---

# 25. RC4 — 历史 dangling Finding ReviewItem 处理原则

本轮不新增 migration，不自动删除历史业务数据。

修复以后：

- 新 dangling Finding ReviewItem 无法通过 API 创建；
- 旧 dangling item 无法 decision；
- 旧 dangling item 无法 reopen；
- 历史记录仍可用于审计。

建议在开发/部署数据库执行只读审计：

```sql
SELECT
    ri.id,
    ri.case_id,
    ri.object_id,
    ri.status
FROM review_items ri
LEFT JOIN findings f
    ON f.id = ri.object_id
WHERE ri.object_type = 'finding'
  AND (
      f.id IS NULL
      OR f.case_id <> ri.case_id
  );
```

在 `optimization-v2-delivery.md` 记录审计结果。

如果执行环境只有 disposable test DB，可记录：

```text
dangling finding review item audit: not applicable to disposable test databases
```

---

# 26. RC4.1 — Claim / Release 边界

本轮不重构：

```text
claim_review_item()
release_review_item()
```

理由：

- 修复后所有新 Finding ReviewItem 都只能来自合法原子 submit；
- decision 与 reopen 已对历史 dangling target fail closed；
- Finding 当前没有独立物理 DELETE API；
- 本轮目标是封闭能够产生或完成非法 Finding Review 生命周期的旁路。

不要扩展成 Review 全域实体完整性重构。

---

# 27. RC4 — 必须新增 Backend API/Transaction Tests

优先扩展：

```text
backend/tests/test_review.py
backend/tests/test_findings.py
```

---

# 28. Test R1 — Generic Review API + Candidate Finding

准备：

```text
Case A
candidate Finding F
```

调用：

```http
POST /api/v1/cases/A/reviews/items
```

body：

```json
{
  "object_type": "finding",
  "object_id": "<F>",
  "summary": "客户端伪造的摘要",
  "priority": 7,
  "risk_level": "high",
  "queue": "priority"
}
```

断言：

```text
HTTP 201
Finding.status == under_review
exactly one Finding ReviewItem
ReviewItem.status == unreviewed
ReviewItem.object_id == F
ReviewItem.priority == 7
ReviewItem.risk_level == high
ReviewItem.queue == priority
ReviewItem.summary == Finding.statement
ReviewItem.summary != 客户端伪造摘要
```

这条测试直接证明 generic API 已强制进入原子 Finding path。

---

# 29. Test R2 — Generic Finding Submit 重复调用幂等

第一次：

```text
priority=7
queue=priority
```

第二次：

```text
priority=1
queue=other
summary=other
```

断言：

```text
ReviewItem count == 1
Finding == under_review
ReviewItem.id unchanged
priority/risk/queue/summary 不被第二次提交覆盖
```

---

# 30. Test R3 — Generic Submit + Nonexistent Finding

调用：

```json
{
  "object_type": "finding",
  "object_id": "finding-does-not-exist"
}
```

断言：

```text
请求失败
code == finding_not_found
Finding ReviewItem count 不增加
Activity 不产生虚假 finding review submission
```

---

# 31. Test R4 — Generic Submit + Cross-case Finding

准备：

```text
Case A → Finding F
Case B
```

调用：

```text
POST /cases/B/reviews/items
object_type=finding
object_id=F
```

断言：

```text
请求失败
code == finding_scope_mismatch
Case B 不产生 ReviewItem
Finding F 状态不变化
```

---

# 32. Test R5 — Generic Submit Verified Finding 进入复审

准备真实流程：

```text
candidate
→ submit
→ approve
→ Finding verified
→ ReviewItem accepted
```

调用 generic：

```text
POST /cases/{case}/reviews/items
object_type=finding
object_id=F
```

断言：

```text
同一个 ReviewItem
ReviewItem = in_review
Finding = under_review
ReviewItem count = 1
```

证明 generic endpoint 与 Findings UI 的 re-review semantics 一致。

---

# 33. Test R6 — Dangling Finding Review Decision Fail Closed

测试中直接通过 Repository/DB 构造历史脏数据：

```text
ReviewItem:
  case_id=A
  object_type=finding
  object_id=missing-F
  status=in_review
```

不要通过 API 创建，因为修复后 API 应拒绝。

调用合法 approve decision。

断言：

```text
请求失败
code == review_object_not_found
ReviewItem.status 仍 in_review
ReviewDecision count 不增加
```

---

# 34. Test R7 — Cross-case Target Review Decision Fail Closed

构造：

```text
Case A → Finding F
ReviewItem case_id=B
ReviewItem object_id=F
ReviewItem object_type=finding
ReviewItem status=in_review
```

断言：

```text
review_object_not_found
ReviewItem 不变化
Finding F 不变化
ReviewDecision 不产生
```

---

# 35. Test R8 — Dangling Finding Review Reopen Fail Closed

构造：

```text
ReviewItem:
  object_type=finding
  object_id=missing-F
  status=accepted
```

调用：

```text
POST /cases/A/reviews/{item_id}:reopen
```

断言：

```text
请求失败
code == review_object_not_found
ReviewItem.status 仍 accepted
```

---

# 36. Test R9 — Valid Finding Reopen 回归

已有 Post-Closure 测试继续通过：

```text
ReviewItem accepted
Finding verified
→ reopen
ReviewItem in_review
Finding under_review
```

本轮不要删除或弱化。

---

# 37. Test R10 — 非 Finding Generic Review 行为不回归

现有：

```text
object_type=claim
submit → claim → decide → reopen
```

必须继续通过。

不得因为 Finding early branch 影响其它 Review objects。

---

# 38. Repository 级事务测试

除 API 测试外，至少增加两条直接 repository 测试。

## A. Decision missing target rollback

调用：

```text
repository.decide_review_item()
```

断言：

```text
ApplicationError(review_object_not_found)
ReviewItem unchanged
0 new ReviewDecision
```

## B. Reopen missing target rollback

调用：

```text
repository.reopen_review_item_atomic()
```

断言：

```text
ApplicationError(review_object_not_found)
ReviewItem unchanged
```

核心测试不得只断言 `raises`，必须重新查询数据库证明 0 partial write。

---

# 39. E2E 要求

本次不需要修改 Scenario B 主流程。

当前 Scenario B：

```text
Finding UI submit
→ Review Workbench
→ claim
→ approve
→ verified
→ reopen
→ under_review
→ approve
→ verified
```

已经正确。

只需重新运行现有：

```text
e2e-smoke.cjs
e2e-interact.cjs
```

证明本次后端收敛没有破坏 UI。

---

# 40. Browser 验收要求

记录实际结果：

```text
Smoke: ?/?
Closure A-F: ?/?
Skipped: 0 for Closure
Unexpected console/pageerror: 0
```

Harness 中原有与 V2 无关的 Kill Switch 条件性 skip 可以继续按当前说明记录，不得把它计为 Closure A–F skip。

---

# 41. Frontend Gate

本次预计无前端业务代码改动。

仍必须运行：

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

全部通过。

---

# 42. Backend 专项回归

至少运行：

```text
backend/tests/test_review.py
backend/tests/test_findings.py
backend/tests/test_claim_review.py
backend/tests/test_provenance.py
backend/tests/test_report_documents.py
backend/tests/test_legacy_compatibility.py
```

全部 green 后再进入 full regression。

---

# 43. Backend Full Regression

沿用当前交付已经验证的全量方法：

```text
backend/tests 下全部 test files 集合 1:1 覆盖
```

可以继续使用当前分片方案解决 SSE/xdist 已知问题。

要求记录：

```text
unique tests collected = N
unique tests final green = N
unresolved failed = 0
unexpected skipped = 0
```

不要预先硬编码最终 test 数为 858。

本次新增测试后数量应上升，以实际 collection 为准。

---

# 44. SQLite Lock 记录规则

若 full regression 再次出现：

```text
database is locked
```

必须：

1. 记录首次失败测试；
2. 判断是否与已知 xdist SQLite contention 同类；
3. 对所属文件串行复跑；
4. 只有串行通过且无业务断言失败才可标记 infrastructure flake；
5. delivery 文档同时记录 unique-test result 和 raw re-run，不混淆计数。

---

# 45. CI 状态说明

当前评审 HEAD 没有可见 GitHub commit status checks。

若执行结束时仍然如此，最终 delivery 文档写：

```text
Regression evidence is based on the repository-local executed test matrix.
No GitHub commit status checks were available for this HEAD.
```

这是事实说明，不是 Closure blocker。

---

# 46. 实现后的最终调用结构

必须变为：

```text
Finding UI
   │
   └─ FindingService.update_status(under_review)
          │
          └─ ApplicationRepository.submit_finding_for_review()
                 └─ ONE TRANSACTION
                    Finding + unique ReviewItem

Generic Review API
POST /reviews/items
object_type=finding
   │
   └─ ReviewService.submit_item()
          │
          └─ ApplicationRepository.submit_finding_for_review()
                 └─ SAME TRANSACTION
                    Finding + unique ReviewItem

Review Workbench reopen
   │
   └─ ReviewService.reopen()
          │
          └─ ApplicationRepository.reopen_review_item_atomic()
                 └─ require valid Finding target
                    ReviewItem + Finding

Review decision
   │
   └─ ReviewService.decide()
          │
          └─ ApplicationRepository.decide_review_item()
                 └─ require valid Finding target
                    ReviewItem + ReviewDecision + Finding
```

系统中不再存在：

```text
create Finding ReviewItem without validating/synchronizing Finding
```

的生产路径。

---

# 47. 禁止实现方式

## 禁止 1：删除 `finding` Review object type

不得从 `review_domain.OBJECT_TYPES` 删除 `finding`。

## 禁止 2：删除通用 Review API

不得删除：

```http
POST /cases/{case_id}/reviews/items
```

其它 Review object 仍依赖该接口。

## 禁止 3：Route 层复制状态矩阵

不得在 `reviews.py` 中自行查 Finding、改 Finding、创建 ReviewItem。

Route 只负责请求解析与调用 Service。

## 禁止 4：ReviewService 复制 submit_finding_for_review 逻辑

只能 early branch 调原子方法。

## 禁止 5：Decision Service 层做非事务 pre-check

Finding target 校验必须与 decision 写入处于同一 DB transaction。

## 禁止 6：缺 Finding 时继续保存 ReviewDecision

任何 Finding ReviewItem target invalid 都必须 fail closed。

## 禁止 7：为历史 dangling item 自动物理删除

本轮只阻断其继续进入 decision/reopen。

## 禁止 8：新增 migration

本次无 schema change。

---

# 48. 推荐 Commit 划分

代码与测试建议一个核心 commit：

```text
fix: close generic finding review api bypass
```

应包含：

- `ReviewService.submit_item()` finding early branch；
- `submit_finding_for_review()` metadata 兼容；
- canonical Finding summary；
- shared Finding review target helper；
- decision fail closed；
- reopen fail closed；
- API / repository tests。

验收与文档：

```text
docs: finalize optimization v2 review api consistency
```

---

# 49. optimization-v2-delivery.md 最终记录模板

完成测试后追加：

```markdown
# Optimization V2 Review API Consistency Patch Result

Status: CLOSED

Baseline:
e2f60b8a10138779700a7af741dddd73ea3dcc22

## RC1 — Generic Finding Review submit
- POST /reviews/items with object_type=finding now delegates to
  ApplicationRepository.submit_finding_for_review().
- Finding existence and case scope are enforced.
- Finding status and unique ReviewItem are written in the same transaction.
- Existing ReviewItem metadata is preserved on idempotent re-submit.

## RC2 — Decision fail closed
- Finding Review decisions require a real same-case Finding target inside
  the decision transaction.
- Missing/cross-case targets return review_object_not_found.
- No ReviewDecision or ReviewItem state is committed on failure.

## RC3 — Reopen fail closed
- Finding Review reopen requires a real same-case Finding target inside the
  reopen transaction.
- Invalid targets leave ReviewItem unchanged.

## Tests
- Review API targeted tests: ...
- Finding targeted tests: ...
- Backend full regression: N/N unique tests green.
- Frontend typecheck: passed.
- Frontend lint: passed.
- Frontend test: passed.
- Frontend build: passed.
- Browser smoke: ...
- Browser Closure A-F: ... / 0 skipped.
- Unexpected console/pageerror: 0.
```

---

# 50. 最终 Definition of Done

只有以下全部成立，才允许重新声明 Optimization V2 正式 CLOSED：

```text
[ ] Generic POST /reviews/items + finding 走 submit_finding_for_review
[ ] Generic submit 不可创建 nonexistent Finding ReviewItem
[ ] Generic submit 不可创建 cross-case Finding ReviewItem
[ ] Generic submit candidate → Finding under_review
[ ] Generic submit verified/rejected 复用同一 ReviewItem 进入 re-review
[ ] Duplicate generic submit remains idempotent
[ ] Review priority/risk/queue 首次创建兼容
[ ] Finding Review summary 来自 finding.statement
[ ] Existing ReviewItem metadata 不被重复 submit 覆盖
[ ] ApplicationRepository.create_review_item 不能直接创建 finding ReviewItem
[ ] 全仓 submit_finding_for_review/create_review_item 调用点已核对

[ ] Finding Review decision missing target fail closed
[ ] Finding Review decision cross-case target fail closed
[ ] Failed decision creates 0 ReviewDecision
[ ] Failed decision leaves ReviewItem unchanged
[ ] Failed decision leaves unrelated Finding unchanged
[ ] Finding Review decision 要求 Finding.status == under_review
[ ] 历史 candidate + in_review item 不能直接把 Finding 裁决为 verified/rejected

[ ] Finding Review reopen missing target fail closed
[ ] Finding Review reopen cross-case target fail closed
[ ] Failed reopen leaves ReviewItem unchanged
[ ] Reopen 验证 ReviewItem/Finding 状态配对
[ ] superseded Finding 无法通过 Workbench reopen 复活
[ ] Valid accepted/rejected Finding reopen regression still passes

[ ] Non-Finding Review submit/claim/decide/reopen regression passes
[ ] Existing Finding UI Scenario B remains green

[ ] Backend targeted tests green
[ ] Backend full regression all unique tests green
[ ] 0 unresolved backend failure
[ ] 0 unexpected backend skip

[ ] Frontend typecheck green
[ ] Frontend lint green
[ ] Frontend tests green
[ ] Frontend build green

[ ] Browser smoke green
[ ] Browser Closure A-F green
[ ] Closure skipped = 0
[ ] Unexpected console/pageerror = 0

[ ] optimization-v2-delivery.md updated with actual results
```

---

# 51. 最终评审目标

本补丁完成后，Finding Review 的所有生产入口将统一遵守同一套后端不变量：

```text
Finding exists
+ same Case
+ unique ReviewItem
+ atomic submit
+ human decision
+ atomic terminal state
```

完整生命周期：

```text
Candidate Finding
     │
     ├── Findings UI submit
     │
     └── Generic Review API submit(object_type=finding)
              │
              ▼
      ONE ATOMIC SUBMIT PATH
              │
      Finding = under_review
      unique ReviewItem exists
              │
              ▼
        Human Review Workbench
              │
       ┌──────┴──────┐
       │             │
    decision       reopen
       │             │
       ▼             ▼
  atomic terminal  atomic re-review
       │             │
       ▼             ▼
verified/rejected under_review
```

不存在合法 target 时：

```text
submit   → reject
decision → reject
reopen   → reject
```

不会再生成“幽灵 ReviewItem”或“Review 已变化但 Finding 不存在”的系统状态。

完成 RC0–RC5 后，Optimization V2 的 Finding/Human Review 状态边界即可认为真正从 UI 层、Service 层到 Repository transaction 层全部闭合。

此后不应再继续扩大 Optimization V2 Closure 范围，可以进入下一阶段产品演进。
