# Nothing-in-the-dark — Optimization V2 Final Closure Execution Plan

> 文档用途：交付给执行智能体，指导 **Optimization V2 最后一轮 Closure 修复与验收**。  
> 评审基线：`main` HEAD `79e8842520d7c53ceacce2ee0a3a1ce4926938ca`（2026-08-30）。  
> 前置文档：`docs/Nothing-in-the-dark_Optimization_Execution_Plan_V2.md`、`docs/optimization-v2-review-and-closure-plan.md`、`docs/optimization-v2-delivery.md`。  
> 本轮性质：**Final Correctness Pass**。不得重新设计产品架构，不得扩大为新一轮 Optimization V3。

---

# 0. 执行结论与边界

上一轮 C0–C11 返工已经使 Optimization V2 主体目标基本成立：Finding HITL、Evidence Integrity、Report Publish Gate、Signals、Collection、Provenance、Propagation Network、Evidence/Timeline/Live Data Workspace、M5.7 IA 迁移、Copilot 历史重建与 Legacy 清理均已进入目标架构。

本轮只关闭评审后仍存在的最后缺口：

| ID | 严重度 | 问题 | 本轮要求 |
|---|---:|---|---|
| FC1 | P0 | Propagation Edge 仍以 `bool human_confirmed` 同时表达“未审核/已驳回”，Graph 与 Detail 语义冲突 | 改为显式三态，并保持旧字段兼容 |
| FC2 | P1 | `FindingService.create_manual()` 在 Evidence 校验失败前已 commit Finding/部分 links | 创建路径必须原子化，失败不得留下任何部分写入 |
| FC3 | P1 | 新 Evidence Workspace 丢失旧 Sidebar 的 unassigned Evidence 浏览入口 | 恢复可浏览、可选择、可进入 Copilot Context 的未归属证据列表 |
| FC4 | P2 | Finding → Report reverse provenance 不解析 generic citation；Posts 测试存在 `or True` 无效断言 | 补一致性并清理虚假测试 |
| FC5 | P1 / Closure | `e2e-interact.cjs` 当前 Scenario A–F 多数仅验证 API/页面 h1，交付文档却描述为完整浏览器交互覆盖 | 补真实 UI interaction E2E，并修正文档口径 |
| FC6 | Closure | `optimization-v2-delivery.md` 已提前写 `Optimization V2 CLOSED` | 仅在 FC1–FC5 与最终 Gates 全部通过后才能正式 CLOSED |

本轮**禁止**处理以下内容：

- 不重新设计 Investigation IA；
- 不修改 LangGraph / Durable Run / Approval / Sandbox / SSE 的核心运行语义；
- 不更换 ECharts、Vue、FastAPI、SQLAlchemy、Alembic 等技术栈；
- 不增加新的图数据库、Evidence 数据模型、Report 数据模型或另一套 Review 系统；
- 不重做 Collection / Signal / Report / Copilot 已通过的架构；
- 不删除 `/narratives` 兼容路由，本项仍属于已记录的后续清理；
- 不为了 E2E 增加生产环境可访问的 test-only API；
- 不把本轮扩展成 UI 视觉重构。

---

# 1. 执行协议

执行智能体必须按 **FC0 → FC1 → FC2 → FC3 → FC4 → FC5 → FC6** 顺序实施。每个工作包必须：

1. 先读取本文指定文件和现有测试；
2. 只修改该工作包需要的代码；
3. 先运行专项测试；
4. 专项测试通过后再提交独立 commit；
5. 在 `docs/optimization-v2-delivery.md` 的 **Final Closure** 部分记录实际实现和测试结果；
6. 不得因为测试不方便而降低业务约束、删除断言或把失败路径改成 skip；
7. 不得把“接口返回 200”当作 UI interaction E2E 的替代品。

推荐 commit 顺序：

```text
fix: model propagation edge review as explicit tri-state
fix: make manual finding creation atomic
fix: restore unassigned evidence workspace access
fix: complete generic provenance and strengthen post tests
test: close investigation interaction e2e gaps
docs: finalize optimization v2 closure
```

如实现过程中必须增加一个很小的中间修复 commit，可以增加，但不得把多个无关 FC 工作包混在一个 commit 中。

---

# 2. FC0 — Final Closure 基线

## 2.1 目标

建立本轮可审计基线，不改业务代码。

## 2.2 必做

执行并记录：

```bash
git status --short
git rev-parse HEAD
git log -8 --oneline
```

确认：

- HEAD 基于本次评审后的 `main`；
- 工作树没有未说明的业务改动；
- `backend/.pytest-*-tmp/` 等运行产物仍未被跟踪；
- 当前最新 Alembic revision 为 `20260829_0048_report_documents.py`，因此本轮 Propagation migration 使用 **0049**，不得复用旧 revision 编号。

在 `docs/optimization-v2-delivery.md` 末尾先加入：

```markdown
# Optimization V2 Final Closure

Status: IN PROGRESS
Baseline HEAD: <sha>
```

在 FC6 完成前，不得将该段写成 `CLOSED`。

## 2.3 DoD

- [ ] 基线 SHA 已记录；
- [ ] 工作树状态已记录；
- [ ] Final Closure 状态为 `IN PROGRESS`；
- [ ] 无业务代码改动。

---

# 3. FC1 — Propagation Edge Human Review 显式三态

## 3.1 当前真实问题

当前：

```python
PropagationEdgeRecord.human_confirmed: bool = False
```

并且人工确认接口直接执行：

```python
record.human_confirmed = confirmed
```

因此：

```text
新生成且从未审核 Edge -> False
人工明确驳回 Edge       -> False
```

两种状态在数据库不可区分。

与此同时前端：

- `PropagationGraph.vue` 将 `False` 当作红色“驳回”；
- `PropagationDetailPanel.vue` 将 `False` 当作“人工未确认（推断关系）”。

这是 P0 领域语义冲突。本工作包必须让后端成为三态唯一事实来源。

## 3.2 固定实现方案

### 3.2.1 数据模型

修改：

```text
backend/app/infrastructure/database/models.py
```

在 `PropagationEdgeRecord` 新增：

```python
human_review_state: Mapped[str] = mapped_column(
    String(16),
    default="unreviewed",
    server_default="unreviewed",
    index=True,
)
```

合法值固定为：

```text
unreviewed
confirmed
rejected
```

**保留现有 `human_confirmed: bool` 字段，不在本轮删除。** 该字段仅作为兼容字段：

```text
human_review_state=confirmed -> human_confirmed=True
human_review_state=unreviewed/rejected -> human_confirmed=False
```

新的生产逻辑和前端展示均不得再通过 `human_confirmed` 推断“未审核”和“驳回”的区别。

### 3.2.2 Alembic migration

新增：

```text
backend/migrations/versions/20260830_0049_propagation_review_state.py
```

Alembic revision 标识必须沿用仓库现有完整字符串惯例：

```python
revision = "20260830_0049"
down_revision = "20260829_0048"
```

不得简写为 `"0049"` / `"0048"`。

要求：

```text
revision = "20260830_0049"
 down_revision = "20260829_0048"
```

upgrade 顺序：

1. `propagation_edges` 增加 `human_review_state VARCHAR(16)`，临时允许 server default `unreviewed`；
2. 先把所有 `human_confirmed = true` 行回填为 `confirmed`；
3. 对 `human_confirmed = false` 的历史行：
   - 如果现有 `evaluations.details` 中**能够可靠确定**该 edge 曾有人工 propagation confirmation 且最新决策为 false，则回填 `rejected`；
   - 如果当前 Evaluation 数据不包含足够的 edge id + confirmed/rejected 信息，**禁止猜测**，统一回填 `unreviewed`；
4. 新列最终必须 `NOT NULL`；
5. downgrade 删除该列，不删除旧 `human_confirmed`。

迁移原则：宁可把历史无法证明的 False 视为 `unreviewed`，也不能错误标记为“人工驳回”。

### 3.2.3 Repository / API 写路径

修改至少：

```text
backend/app/application/repositories.py
backend/app/api/routes/propagation.py
backend/app/schemas/propagation.py
```

将 `confirm_propagation_edge(... confirmed: bool ...)` 的写入语义固定为：

```python
if confirmed:
    record.human_review_state = "confirmed"
    record.human_confirmed = True
else:
    record.human_review_state = "rejected"
    record.human_confirmed = False
```

必须继续写现有 evaluation audit；若当前 audit details 未显式记录以下字段，本轮补齐：

```json
{
  "propagation_edge_id": "...",
  "human_review_state": "confirmed|rejected",
  "confirmed": true
}
```

其中 `confirmed` 保留兼容，`human_review_state` 为新的准确语义。

### 3.2.4 Response DTO

`PropagationEdgeResponse` 增加：

```python
human_review_state: Literal["unreviewed", "confirmed", "rejected"]
```

旧 `human_confirmed` 继续返回，避免破坏已有消费者。

新代码必须以 `human_review_state` 为展示依据。

### 3.2.5 Frontend Type

修改：

```text
frontend/src/types/api.ts
frontend/src/services/... propagation API 对应模块（如类型重复定义则统一）
```

`PropagationGraphEdgeDTO` 增加三态字段。

### 3.2.6 Graph 三态显示

修改：

```text
frontend/src/components/network/PropagationGraph.vue
```

固定视觉语义：

```text
unreviewed -> 灰色虚线，宽度 1.5，语义“算法推断 / 尚未人工复核”
confirmed  -> 绿色实线，宽度 3，语义“人工确认”
rejected   -> 红色虚线或红色实线，但必须与 unreviewed 明显区分，语义“人工驳回”
```

不要再写：

```ts
edge.human_confirmed === false ? rejected : inferred
```

必须 switch `edge.human_review_state`。

### 3.2.7 Detail 三态显示

修改：

```text
frontend/src/components/network/PropagationDetailPanel.vue
```

Badge 固定：

```text
unreviewed -> 人工未复核（推断关系）
confirmed  -> 人工已确认
rejected   -> 人工已驳回
```

操作后刷新必须能看到准确结果：

```text
未审核 -> 点击“确认关系成立” -> confirmed
未审核 -> 点击“驳回该关系”   -> rejected
rejected -> 点击“确认关系成立” -> confirmed（允许人工改判，Evaluation 留审计）
confirmed -> 点击“驳回该关系”  -> rejected（允许人工改判，Evaluation 留审计）
```

不新增另一套 ReviewItem；传播边继续复用既有 confirmation API + Evaluation audit。

## 3.3 测试要求

后端至少增加/修改：

```text
backend/tests/test_propagation_graph.py
现有 propagation confirmation 对应测试文件
migration tests（如仓库已有 migration test 基础设施则加入现有套件）
```

必须覆盖：

1. 新 edge 默认 `human_review_state == unreviewed`；
2. 默认 `human_confirmed == False`；
3. confirm(true) -> `confirmed` + `human_confirmed=True`；
4. confirm(false) -> `rejected` + `human_confirmed=False`；
5. rejected -> confirmed 改判；
6. confirmed -> rejected 改判；
7. graph API 返回三态字段；
8. 旧 True 数据 migration -> confirmed；
9. 无可靠人工审计的旧 False 数据 migration -> unreviewed；
10. migration upgrade/downgrade 可执行。

前端：

```text
frontend/src/components/network/PropagationGraph.test.ts
frontend/src/components/network/PropagationDetailPanel.test.ts
```

必须分别断言三个状态的渲染，不能只测 bool。

## 3.4 专项验收

至少运行：

```bash
cd backend
pytest tests/test_propagation_graph.py <existing-propagation-confirmation-tests>

cd ../frontend
npm run test -- PropagationGraph PropagationDetailPanel
npm run typecheck
```

并执行 Alembic：

```bash
cd backend
alembic upgrade head
```

如项目已有 PG offline DDL 校验方式，继续执行，确保 0049 在 PostgreSQL 方言可生成合法 SQL。

## 3.5 禁止方案

- 禁止仅把 `human_confirmed` 改成 `bool | None` 而不处理现有审计与兼容；
- 禁止用前端局部变量记住“rejected”；刷新后状态必须来自数据库；
- 禁止把所有历史 False 回填为 rejected；
- 禁止删除旧 `human_confirmed`；
- 禁止新建另一套 propagation review 表。

## 3.6 DoD

- [ ] 数据库能够区分 unreviewed / confirmed / rejected；
- [ ] Graph、Detail、API 三处语义一致；
- [ ] 刷新后驳回状态仍为“人工已驳回”；
- [ ] 旧消费者继续能读取 `human_confirmed`；
- [ ] 迁移和专项测试通过。

---

# 4. FC2 — Finding Manual Creation 原子性

## 4.1 当前真实问题

当前 `FindingService.create_manual()` 先执行：

```python
record = await self._findings.create(record)  # commit
```

随后才校验/写 source link、Evidence links；而 `FindingRepository.create()`、`create_source_link()`、`add_evidence_link()` 都各自独立 session + commit。

因此非法 Evidence 请求可能出现：

```text
API 返回失败
但 Finding 已存在，甚至部分 Evidence Link 已存在
```

本工作包必须保证手动 Finding 创建为“全成或全败”。

## 4.2 固定实现方案

### 4.2.1 先完成所有纯校验

修改：

```text
backend/app/application/finding_service.py
```

`create_manual()` 在任何持久化前完成：

1. `kind` 校验；
2. statement 非空；
3. confidence 规范化；
4. source 参数完整性规则保持现有兼容；
5. `evidence_links` 中 relation 必须属于：

```text
supports / contradicts / context
```

6. 所有 Evidence ID 必须真实存在且属于当前 case。

如果任意 Evidence 非法，必须在 INSERT Finding 之前抛错。

### 4.2.2 Repository 新增单事务入口

修改：

```text
backend/app/infrastructure/database/finding_repository.py
```

新增一个明确的 atomic method，建议命名：

```python
async def create_with_links(
    self,
    record: FindingRecord,
    *,
    source_link: tuple[str, str, str] | None = None,
    evidence_links: list[tuple[str, str]] | None = None,
) -> FindingRecord:
```

内部只能打开 **一个 session**：

```text
session.add(Finding)
flush()                         # 获得 id，不 commit
optional session.add(SourceLink)
for each evidence -> session.add(EvidenceLink)
commit once
refresh Finding
```

任何异常：

```text
context manager rollback
Finding / SourceLink / EvidenceLink 均不得残留
```

`create_manual()` 必须改为调用该 atomic method。

### 4.2.3 不扩大 Materializer 范围

Artifact materializer 当前策略是：

```text
非法 evidence ref -> 跳过该 link + warning
Finding 仍物化
```

这是上一轮已确定的宽容策略，本轮**不得改成 fail closed**。

仅当执行智能体发现 `_materialize()` 存在由本次 repository helper 引起的必要兼容调整时，才允许复用 helper；不得借此改变 warning/幂等语义。

## 4.3 测试要求

扩展：

```text
backend/tests/test_findings.py
```

必须新增明确的数据库残留断言：

### Case 1：不存在 Evidence

```text
before: findings = N
create_manual(evidence_links=[real, missing]) -> error
post: findings 仍为 N
post: 不存在该 statement 的 Finding
post: 不存在 source link
post: 不存在 evidence link
```

### Case 2：跨 Case Evidence

同样必须 0 partial write。

### Case 3：第二个 link 写入失败

使用可控方式制造第二个 link 冲突/异常，验证整个 transaction rollback，而不是只验证前置校验。

不要通过 monkeypatch 让 `create_manual()` 根本不进入 Repository；至少要测试 repository atomic helper 在数据库异常时 rollback。

### Case 4：正常创建

Finding + source + 多 Evidence links 一次成功，数据完整。

## 4.4 专项验收

```bash
cd backend
pytest tests/test_findings.py
pytest tests/test_findings.py tests/test_provenance.py tests/test_report_documents.py
```

## 4.5 禁止方案

- 禁止“失败后再 delete Finding”补偿式修复；
- 禁止继续每个 link 单独 commit；
- 禁止只增加测试、不改事务边界；
- 禁止改变 Artifact materializer 的 warning 策略。

## 4.6 DoD

- [ ] 手动 Finding 创建任何失败均 0 partial write；
- [ ] 正常创建仍支持 source + 多 Evidence links；
- [ ] Existing Finding update/add_evidence_link API 行为不回退；
- [ ] Finding / Provenance / Report 相关回归通过。

---

# 5. FC3 — 恢复 Unassigned Evidence 可浏览入口

## 5.1 当前真实问题

`InvestigationEvidenceView.vue` 仍取得：

```ts
summary.unassigned
```

但只显示：

```text
未分组证据 N
```

旧 `EvidenceSidebar` 已删除后，用户无法查看这些未绑定 Claim 的 Evidence。

本工作包不新增后端 API，只恢复信息访问能力。

## 5.2 固定 UI 方案

修改：

```text
frontend/src/views/investigation/InvestigationEvidenceView.vue
frontend/src/components/evidence/EvidenceDetailPanel.vue（仅必要调整）
```

新增一个小型内容组件：

```text
frontend/src/components/evidence/UnassignedEvidenceList.vue
frontend/src/components/evidence/UnassignedEvidenceList.test.ts
```

### 5.2.1 Evidence Workspace 层级保持不变

顶部仍然：

```text
Claims | Semantics
```

不得再增加第三个顶层 `Unassigned` tab。

在 `Claims` 工作区左栏，新增 scope switch：

```text
Claims (claimCount) | Unassigned (unassignedCount)
```

行为：

- `Claims`：继续显示现有 Claim filters + `EvidenceClaimList`；
- `Unassigned`：显示 `summary.unassigned`；
- 默认仍为 `Claims`；
- Case 切换时 scope 重置为 `Claims`。

### 5.2.2 Unassigned list 内容

每项至少显示：

```text
stance
excerpt
source_type
platform / author（metadata 有则显示）
relevance
```

不得伪造 Claim、Finding 或来源标题。

点击 item：

```ts
selectedClaim = null
selectedItem = item
setUiContext({
  workspace: 'evidence',
  selected_type: 'evidence',
  selected_id: item.id,
})
```

右侧继续复用现成 `EvidenceDetailPanel`，因为其 `item` 模式不要求 claim。

### 5.2.3 空状态

```text
Claims 为空、Unassigned 有数据
```

不能再给用户“尚无证据”的误导提示。

要求：

- Claims scope：显示“暂无已归组主张；可切换到 Unassigned 查看未归属证据”；
- Unassigned scope：正常显示未归属 Evidence；
- 两者都空才显示现有“尚无证据”引导。

## 5.3 测试要求

扩展：

```text
frontend/src/views/investigation/InvestigationEvidenceView.test.ts
```

并新增 `UnassignedEvidenceList.test.ts`。

必须覆盖：

1. unassigned count 正确；
2. 切换到 Unassigned 后显示 excerpt；
3. 点击 unassigned item 后 DetailPanel 收到 item；
4. `setUiContext()` 为 `selected_type=evidence`；
5. 0 claims + >0 unassigned 时数据可见；
6. 两者均为空才显示全局 empty guide；
7. Semantics tab 不受影响。

## 5.4 专项验收

```bash
cd frontend
npm run test -- InvestigationEvidenceView UnassignedEvidenceList EvidenceDetailPanel
npm run typecheck
npm run lint
```

## 5.5 禁止方案

- 禁止新建后端 API；
- 禁止恢复已删除的 `EvidenceSidebar.vue`；
- 禁止把 unassigned Evidence 自动绑定到虚构 Claim；
- 禁止在 UI 隐藏 `summary.unassigned` 但只保留计数。

## 5.6 DoD

- [ ] 未归属 Evidence 可被用户看到；
- [ ] 可进入现有 DetailPanel；
- [ ] 可进入 Copilot Context；
- [ ] 0 Claim 场景不再丢信息。

---

# 6. FC4 — Provenance Generic Reverse Lookup + 测试真实性

## 6.1 Finding → Report generic citation

当前 Report citation 支持：

```json
{"ref": "finding-id"}
```

`ReportDocument -> Finding` 能解析 generic ref，但 `_reports_citing_finding()` 只识别 parser 直接返回的 `type == finding`，因此反向链路可能漏掉 generic Finding citation。

修改：

```text
backend/app/application/provenance_service.py
```

在 `_reports_citing_finding(session, case_id, finding_id)` 中，对每个 normalized ref：

```text
if ref_type == "finding" and ref_id == finding_id -> match
if ref_type == "generic":
    actual = await _resolve_generic_ref_type(session, case_id, ref_id)
    if actual == "finding" and ref_id == finding_id -> match
```

不得复制第三份 generic resolver。

扩展：

```text
backend/tests/test_provenance.py
```

新增完整双向断言：

```text
Report citation {ref: finding.id}
Report provenance upstream contains Finding
Finding provenance downstream contains same ReportDocument
```

## 6.2 删除 Posts 测试中的虚假断言

当前 `backend/tests/test_posts.py` 存在：

```python
assert first["source_url"] == "..." or True
```

必须删除这种永真条件。

不要简单改成依赖排序的 `first` 断言。应通过稳定键找到目标 post，例如：

```python
weibo_post = next(post for post in all_posts if post["native_id"] == "w1")
assert weibo_post["source_url"] == "https://weibo.com/w1"
```

同时检索本轮涉及的测试文件是否还有明显的：

```text
or True
assert True
if ...: pass
```

仅修复与 FC1–FC5 相关文件中的虚假断言，不开展全仓测试风格重构。

## 6.3 专项验收

```bash
cd backend
pytest tests/test_provenance.py tests/test_posts.py
```

## 6.4 DoD

- [ ] generic Finding citation 双向 provenance 一致；
- [ ] Posts 字段白名单/URL 测试是真实断言；
- [ ] 无通过 `or True` 绕过失败的 Closure 测试。

---

# 7. FC5 — Browser E2E 从 Smoke 升级为真实 Interaction Closure

## 7.1 当前问题

现有 `frontend/e2e-interact.cjs` 已真实启动浏览器，但 Optimization V2 Scenario A–F 中多处实际是：

```text
GET endpoint -> 200
page -> h1 exists
```

交付文档却写成“Scenario A–F 全部覆盖”。本轮必须让名称与实际覆盖一致。

本工作包**继续使用当前 Playwright CJS 脚本**，不得引入 Cypress 或第三套 E2E framework。

## 7.2 Fixture 原则

真实交互 E2E 需要稳定数据。不得新增生产 test API。

允许新增：

```text
backend/scripts/seed_final_closure_e2e.py
```

该脚本只作为本地/CI E2E fixture producer，通过现有 Repository/Service 直接写测试数据库，并输出 JSON IDs。

脚本必须使用正常模型/Repository，不得 raw SQL 绕过业务表结构（migration backfill 测试除外）。

固定 fixture 至少包含：

- 一个 Investigation；
- 3 个 Source Posts；
- 1 个 Claim；
- 2 个 Evidence（1 linked + 1 unassigned）；
- 2 个 Propagation Nodes；
- 1 个 `human_review_state=unreviewed` Propagation Edge；
- 1 个 candidate Finding；
- 一个合法 Report Document/Artifact fixture；
- 一个含非法/cross-case citation 的 Report fixture；
- 一个 Signal/Alert fixture；
- 另一个 Case，用于 cross-case citation / Evidence 隔离。

脚本 stdout 最后一行打印单行 JSON：

```json
{
  "case_id": "...",
  "other_case_id": "...",
  "claim_id": "...",
  "evidence_id": "...",
  "unassigned_evidence_id": "...",
  "propagation_edge_id": "...",
  "finding_id": "...",
  "valid_report_id": "...",
  "invalid_report_id": "...",
  "signal_id": "..."
}
```

E2E runner 使用这些真实 ID，不依赖随机页面内容猜测。

若已有更合适的 repo 内 fixture seed 机制，可复用，但最终数据集合与输出字段必须等价；不得由执行智能体重新选择不同的验收目标。

## 7.3 Scenario A — Investigation Shell + Evidence → Copilot Context

Browser 必须执行：

1. 打开 `/investigations/{case_id}/evidence`；
2. 验证 Investigation Shell 标题；
3. 验证 Claim 文本真实显示；
4. 点击一个 Evidence；
5. 验证右侧 Evidence Detail 出现其 excerpt/source metadata；
6. 验证 UI Context 已切到：

```text
workspace=evidence
selected_type=evidence
selected_id=<evidence_id>
```

7. 切换 `Unassigned`；
8. 点击 `<unassigned_evidence_id>`；
9. 再次验证 Detail + Context。

### Copilot Context 的 E2E 断言方式

不得直接读取 Vue 内部 ref。

优先采用现有 Copilot UI 可见 context chip/label；如果当前 UI 没有可见 context 表示，则通过“发送一条轻量问题”并捕获正常 chat/run 请求，断言请求 payload 的 `ui_context` 中包含上述字段。

如果 demo mode 能稳定完成 run，则继续等待 run terminal state，并断言页面无 error；若当前模型调用本身不是本地 E2E 的稳定依赖，则**只允许**把“LLM 最终回答内容”从强断言中移除，但 `ui_context` 出现在真实后端请求这一点必须验证，不能重新退回 unit test。

## 7.4 Scenario B — Finding Review 真闭环

不得只通过 API 完成全部流程。

至少：

1. 浏览器打开 Investigation Findings；
2. 页面显示 candidate Finding；
3. 通过 UI 将其提交/进入 review 工作流（根据现有实际 UI 的入口，不新增另一套 Review 页面）；
4. 浏览器打开现有 Review/Approval 对应页面并完成 claim + approve（如果 Investigation Findings 本身已有完整动作，则在当前页完成）；
5. 回到 Findings；
6. 验证该 Finding 显示为 verified。

同时保留 API negative assertion：普通 Finding status endpoint 请求 `verified` 仍返回 422。

E2E 的重点是：**用户正常 UI 路径可以完成 review，而绕过 UI 的普通 status API 不可以伪造终审。**

## 7.5 Scenario C — Report Publish Gate 正反两路

必须有两个 fixture：valid / invalid。

Browser：

### Valid

1. 打开 Investigation Report / Reports 实际发布界面；
2. 选择合法 draft；
3. 通过 UI 执行正常的 review/publish 操作；
4. 验证状态显示 published。

### Invalid

1. 打开含 nonexistent/cross-case citation 的 draft；
2. 点击 publish；
3. 后端必须拒绝；
4. UI 必须显示可理解的发布失败状态/错误；
5. 重新读取 report 后状态仍不得为 published。

不得用“不存在 artifact import 失败”代替 publish gate。

## 7.6 Scenario D — Propagation Network 真实交互

Browser：

1. 打开 `/investigations/{case_id}/network`；
2. 验证 ECharts canvas 已渲染且 graph fixture 非空；
3. 选择 fixture Edge；
4. Detail Panel 必须显示 relation / confidence / `人工未复核（推断关系）`；
5. 点击“驳回该关系”；
6. 等待 graph refresh；
7. Detail 必须显示“人工已驳回”；
8. 页面 reload；
9. 再次选择同 Edge，仍显示“人工已驳回”；
10. 点击“确认关系成立”；
11. 刷新后显示“人工已确认”。

### ECharts Canvas 可测试性

不得使用硬编码随机坐标点击 force graph。

如果现有 ECharts canvas 无稳定 DOM selector，本轮允许在 `PropagationGraph.vue` 增加**不改变产品行为的 E2E test hook**，但必须满足：

- 仅在 `VITE_E2E=true` 时暴露；
- 不增加生产 API；
- 不显示额外生产 UI；
- hook 只允许按已存在 edge/node id 触发与真实 chart click 相同的 `select` emit；
- Production build（未设置 `VITE_E2E`）不应暴露该 hook。

推荐形式：在 E2E mode 将一个最小函数挂到 chart DOM element 的 expando property，由 Playwright `page.evaluate()` 按 edge id 调用；函数内部只能 `emit('select', ...)`，不得直接修改 store/context/数据库。这样 Detail 与后续 API 仍走真实生产路径。

必须同时保留现有 `PropagationGraph.test.ts` 对真实 chart click mapping 的 unit test，E2E hook 不是 unit test 的替代品。

## 7.7 Scenario E — Live Data Posts UI

Browser：

1. 打开 `/investigations/{case_id}/live-data`；
2. 默认 Posts tab；
3. 页面至少显示 seed 的 3 posts；
4. 输入关键词 filter，只剩目标 post；
5. 切 platform filter；
6. 点击 post；
7. 验证 Copilot Context：

```text
workspace=live_data
selected_type=social_post
selected_id=<post_id>
```

8. 如果 fixture 数量足够分页，再验证“加载更多”；若当前只 seed 3 条，则 seed 脚本必须增加到 `page_size + 1`，从而真实验证 load-more，不得跳过。

建议 fixture 直接 seed 51 条轻量 posts（其中 3 条有明确语义，其余为 filler），以验证默认 `limit=50 + has_more`。

## 7.8 Scenario F — Signals UI 状态机

Browser：

1. 打开 `/signals`；
2. 找到 fixture signal；
3. UI 点击 acknowledge；
4. 状态显示 acknowledged；
5. UI 点击 resolve；
6. 状态显示 resolved；
7. 使用 API 对同一 signal 尝试 acknowledge，必须 400 `alert_status_transition_invalid`；
8. 页面刷新后仍为 resolved。

如当前 UI 对 resolved item 不显示非法 acknowledge 按钮，这是正确行为，不要求为了测试暴露非法按钮。

## 7.9 E2E 结果标准

`e2e-interact.cjs` 输出中必须区分：

```text
Smoke checks
Optimization V2 Closure A-F interaction checks
Other Harness checks
```

A–F 不允许出现：

```text
SKIPPED
```

现有 Kill Switch 成功路径如果仍因 policy_exception fixture 不可自包含，可以继续按原先限制记录为 unrelated skip，但不得把它计入 A–F 的 pass 数。

必须断言：

```text
console error = 0
pageerror = 0
A-F failed = 0
```

## 7.10 禁止方案

- 禁止把 API `GET 200` 写成“UI interaction passed”；
- 禁止只检查 `h1`；
- 禁止新增 public `/test/seed` API；
- 禁止对 ECharts 使用不可重复的硬编码坐标；
- 禁止因测试困难而删除 Scenario D 的 edge confirm/reject 交互；
- 禁止把 A–F 任一项标成 skipped 后仍写 “Scenario A–F 全部通过”。

## 7.11 DoD

- [ ] A Evidence/Context 有真实 browser interaction；
- [ ] B Finding Review 有真实 UI 闭环；
- [ ] C Report valid/invalid publish gate 有 browser 正反路；
- [ ] D Propagation 三态在浏览器内完成 unreviewed→rejected→confirmed；
- [ ] E Posts filter/select/context/load-more 通过；
- [ ] F Signal acknowledge→resolve + illegal reverse 被拒；
- [ ] A–F 0 skipped / 0 failed；
- [ ] console/pageerror 0。

---

# 8. FC6 — 最终 Regression、文档与 CLOSED Gate

## 8.1 先更新 Delivery 的事实，不先写结论

修改：

```text
docs/optimization-v2-delivery.md
```

增加：

```markdown
## Final Closure FC1 — Propagation Review Tri-state
...
## Final Closure FC2 — Finding Atomic Creation
...
## Final Closure FC3 — Unassigned Evidence
...
## Final Closure FC4 — Provenance/Test Integrity
...
## Final Closure FC5 — Interaction E2E
...
```

每一节只能记录**实际已经完成**的：

- commit SHA；
- 修改文件；
- 行为变化；
- 专项测试命令；
- 实际 pass/fail/skip 数。

不得复制本文计划内容冒充完成记录。

## 8.2 后端完整回归

上一轮记录为 833 passed。由于本轮增加 migration/tests，最终数量预期会上升，但**不得硬编码目标数量**。

要求：

```text
所有 backend/tests 下测试文件 100% 被执行
0 failed
0 unexpected skipped
```

如果因 SSE/xdist 死锁仍需分批执行，可以继续使用上一轮“文件集合 1:1”方式，但必须：

1. 脚本生成测试文件全集；
2. 所有批次 union == 全集；
3. intersection 不要求为空，但不得依靠重复执行掩盖缺失；
4. 记录总 passed/failed/skipped；
5. FC1–FC4 新测试必须明确包含在批次中。

重点回归必须包含：

```text
test_findings.py
test_provenance.py
test_report_documents.py
test_propagation_graph.py
propagation confirmation tests
test_posts.py
test_signals.py
test_monitoring.py
test_legacy_compatibility.py
test_resilience.py
```

## 8.3 Migration Gate

必须记录：

```text
0048 -> 0049 upgrade success
0049 -> 0048 downgrade success（测试数据库）
0048 -> 0049 -> head success
PostgreSQL offline DDL / 项目现有 PG migration check success
```

不得仅依赖 `create_schema()`，因为生产部署使用 Alembic。

## 8.4 Frontend 四项 Gate

必须全部执行：

```bash
cd frontend
npm run typecheck
npm run lint
npm run test
npm run build
```

全部：

```text
0 error / 0 failed
```

## 8.5 Browser Gate

真实 backend + frontend + fixture 环境执行：

```text
e2e-smoke.cjs
e2e-interact.cjs
```

交付记录必须分别写：

```text
Smoke: x/x passed
Closure A-F: x/x passed, 0 skipped
Other Harness checks: x passed, y skipped（如有，说明与 V2 Closure 无关）
Console/PageError: 0
```

不要再写含糊的“41 checks 全过（1 skipped）”同时又称 A–F 全部覆盖。

## 8.6 最终 CLOSED 条件

只有以下全部成立后，才允许把 Delivery 最后的：

```markdown
Status: IN PROGRESS
```

改成：

```markdown
# Optimization V2 CLOSED
```

最终 checklist：

```text
[ ] FC1 Propagation 三态数据库/DTO/UI 完成
[ ] FC1 migration 0049 upgrade/downgrade 通过
[ ] FC2 Finding create 0 partial write
[ ] FC3 Unassigned Evidence 可浏览/可选择/可进入 Context
[ ] FC4 generic Finding citation reverse provenance 完整
[ ] FC4 Closure tests 无 or True 等虚假断言
[ ] FC5 Browser Scenario A–F 真实交互 0 failed / 0 skipped
[ ] Backend full regression 0 failed
[ ] Frontend typecheck 0 error
[ ] Frontend lint 0 error
[ ] Frontend test 0 failed
[ ] Frontend build success
[ ] e2e-smoke success
[ ] console/pageerror = 0
[ ] docs/optimization-v2-delivery.md 与实际结果一致
```

任何一个未满足：

```text
Optimization V2 FINAL CLOSURE PENDING
```

不得写 CLOSED。

---

# 9. 文件级变更清单

执行智能体应优先按下表定位，不要全仓盲目重构。

| 文件 | 必需/条件 | 修改内容 |
|---|---|---|
| `backend/app/infrastructure/database/models.py` | 必需 | Propagation `human_review_state` |
| `backend/migrations/versions/20260830_0049_propagation_review_state.py` | 必需 | 三态 migration/backfill |
| `backend/app/application/repositories.py` | 必需 | propagation confirm 写三态；audit details |
| `backend/app/schemas/propagation.py` | 必需 | response 三态字段 |
| `backend/app/api/routes/propagation.py` | 条件 | DTO/confirmation 接口适配 |
| `backend/tests/test_propagation_graph.py` | 必需 | graph API 三态 |
| propagation confirmation 现有测试文件 | 必需 | confirm/reject/re-review |
| `frontend/src/types/api.ts` | 必需 | Edge DTO 三态 |
| `frontend/src/components/network/PropagationGraph.vue` | 必需 | 三态线型/颜色 |
| `frontend/src/components/network/PropagationGraph.test.ts` | 必需 | 三态渲染 |
| `frontend/src/components/network/PropagationDetailPanel.vue` | 必需 | 三态 badge/actions |
| `frontend/src/components/network/PropagationDetailPanel.test.ts` | 必需 | 三态操作 |
| `backend/app/application/finding_service.py` | 必需 | 全校验后 atomic create |
| `backend/app/infrastructure/database/finding_repository.py` | 必需 | `create_with_links()` 单事务 |
| `backend/tests/test_findings.py` | 必需 | 失败 0 partial write |
| `frontend/src/views/investigation/InvestigationEvidenceView.vue` | 必需 | Claims/Unassigned scope |
| `frontend/src/components/evidence/UnassignedEvidenceList.vue` | 必需 | 未归属 Evidence 列表 |
| `frontend/src/components/evidence/UnassignedEvidenceList.test.ts` | 必需 | list selection |
| `frontend/src/views/investigation/InvestigationEvidenceView.test.ts` | 必需 | 0 claims + unassigned 等 |
| `backend/app/application/provenance_service.py` | 必需 | generic reverse lookup |
| `backend/tests/test_provenance.py` | 必需 | 双向 generic citation |
| `backend/tests/test_posts.py` | 必需 | 删除 `or True` |
| `backend/scripts/seed_final_closure_e2e.py` | 推荐固定方案 | deterministic E2E fixture |
| `frontend/e2e-interact.cjs` | 必需 | A–F interaction |
| `frontend/e2e-smoke.cjs` | 仅必要时 | 不扩大，仅兼容新数据/route |
| `docs/optimization-v2-delivery.md` | 必需 | Final Closure 实际记录 |

如果执行中发现实际路径与表格有细微差异（例如 propagation API service 模块拆分），允许在**现有对应生产模块**中修改，但禁止新建平行实现。

---

# 10. 最终业务不变量

本轮完成后必须保持以下不变量：

## Finding

```text
Agent/普通 API 不能产生 verified/rejected Finding
终审只能来自 Review 决策
Evidence link 必须指向真实同 Case Evidence
Manual create 失败不能留下半成品
```

## Report

```text
Published Report 的 citation 必须解析到当前 Case 的真实对象
unknown / nonexistent / cross-case citation fail closed
```

## Propagation

```text
算法 Edge 初始 = unreviewed
人工确认 = confirmed
人工驳回 = rejected
三者刷新后可区分
Graph 与 Detail 使用同一后端状态
人工改判必须留 Evaluation audit
```

## Evidence

```text
Claim-bound Evidence 可见
Unassigned Evidence 也可见
选择任意 Evidence 均能进入 Detail 与 Copilot Context
```

## Signals

```text
Signal/Monitor 继续共用同一 Alert 状态机
本轮不得引入第二套状态机
```

## Harness

```text
Durable Run / Approval / Sandbox / SSE 核心语义不因本轮修复变化
```

---

# 11. 执行智能体最终交付格式

实现完成后，执行智能体必须在 `docs/optimization-v2-delivery.md` 记录以下最终摘要：

```markdown
# Optimization V2 Final Closure Result

Baseline: <sha>
Final HEAD: <sha>

## FC1 Propagation Review Tri-state
- Commit:
- Files:
- Migration:
- Tests:
- Result:

## FC2 Finding Atomic Creation
- Commit:
- Files:
- Tests:
- Result:

## FC3 Unassigned Evidence
- Commit:
- Files:
- Tests:
- Result:

## FC4 Provenance / Test Integrity
- Commit:
- Files:
- Tests:
- Result:

## FC5 Interaction E2E
- Commit:
- Smoke:
- Scenario A:
- Scenario B:
- Scenario C:
- Scenario D:
- Scenario E:
- Scenario F:
- Skipped inside A-F: 0
- Console/PageError: 0

## Final Regression
- Backend: <passed> passed / 0 failed / <expected-skips> skipped
- Migration: upgrade ✓ / downgrade ✓ / PG check ✓
- Frontend typecheck: ✓
- Frontend lint: ✓
- Frontend test: <passed> passed / 0 failed
- Frontend build: ✓
- Browser smoke: ✓
- Browser A-F: ✓

# Optimization V2 CLOSED
```

如果最终仍存在任何已知限制，必须区分：

```text
A. V2 Closure blocker -> 不允许 CLOSED
B. 明确不属于 V2 scope 的 future cleanup -> 可在 CLOSED 后记录
```

不得把 blocker 写进“已知限制”后仍宣称完成。

---

# 12. 评审者最终验收关注点

下一次评审不会重新检查所有 V2 产品设计，而会重点抽查：

1. `PropagationEdgeRecord` 是否真的有 `human_review_state`；
2. migration 是否没有把所有历史 False 错当 rejected；
3. reject Edge 后刷新是否仍为 rejected；
4. `create_manual()` 是否只有一次最终 commit，失败是否 0 partial write；
5. 0 Claim + N unassigned Evidence 时 UI 是否能看到 N 条数据；
6. `{ref: finding_id}` 的 Report ↔ Finding provenance 是否双向一致；
7. `test_posts.py` 是否已无 `or True`；
8. E2E Scenario D 是否真的完成 Edge reject/confirm，而非 GET graph；
9. E2E Scenario C 是否真的尝试 valid/invalid publish，而非不存在 artifact；
10. E2E A–F 是否 0 skipped；
11. Delivery 中的测试数字是否与实际日志一致；
12. 在全部 Gate 通过之前，文档是否没有提前写 `CLOSED`。

全部满足后，本轮 Optimization V2 可以正式结束，不再需要继续 Closure 返工。
