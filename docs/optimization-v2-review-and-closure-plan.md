# Nothing-in-the-dark Optimization V2 本轮评审结果与最终返工实施计划

> 文档性质：Optimization V2 评审结论 + Closure/返工实施规格  
> 评审基线：`Ethan-Martinez-creater/Nothing-in-the-dark` 当前 `main` 分支，2026-08-29  
> 关联文档：`docs/Nothing-in-the-dark_Optimization_Execution_Plan_V2.md`、`docs/optimization-v2-baseline.md`、`docs/optimization-v2-delivery.md`  
> 面向对象：继续负责直接修改仓库、运行测试和提交实现的执行智能体。  
> 本文不是新的产品方案讨论稿。对本文中已经确定的修复方式，执行智能体应以“实现既定方案”为目标，不再重新进行产品方案选择。

---

## 1. 本轮评审结论

本轮 Optimization V2 已经产生实质性产品架构变化。系统不再主要以 Conversation/Chat 作为一级产品心智，而是形成了：

```text
Workspace
├── Home
├── Signals
├── Investigations
│   └── Investigation
│       ├── Overview
│       ├── Live Data
│       ├── Evidence
│       ├── Network
│       ├── Timeline
│       ├── Findings
│       ├── Report
│       └── Activity
├── Reports
└── Administration
```

同时，Contextual Copilot、Collection Definition、Finding、Global Signals、ReportDocument 等新的产品状态层已经开始落地。因此，本轮不能评价为“仅调整前端页面”或“仅改名”。

但当前还不能判定 Optimization V2 已经完成。现阶段更准确的结论是：

> **V2 的 Investigation-centric 产品骨架已经基本形成，但若干底层业务不变量、Evidence Integrity、Human Review 权限边界、Report Publish Gate、Propagation Workspace 以及最终 E2E Closure 尚未达到 V2 目标。**

综合当前实现：

- 结构层完成度：约 **85%–90%**
- 功能语义完成度：约 **70%–75%**
- 当前阶段：**V2 Closure / Correctness Pass**
- 下一阶段原则：**先完成返工闭环，再考虑新增产品功能**

---

## 2. 当前 M0–M8 重新评级

| Milestone | 当前评级 | 评审结论 |
|---|---|---|
| M0 基线 | ✅ 完成 | 基线记录、legacy compatibility 护栏已建立 |
| M1 IA / Shell | ✅ 基本完成 | Investigation Router、Global Shell、产品语言已形成 |
| M2 Copilot / Activity | 🟢 较好 | 结构化 `ui_context` 方案正确；Copilot 历史重建仍是过渡实现 |
| M3 Collection | 🟡 基本完成 | version/active/transaction 较完整；`exclusions`/`filters` 当前未真正影响采集 |
| M4 Evidence / Findings | 🟠 部分完成 | Finding 架构方向正确，但存在 Review 绕过和无效 Evidence 引用问题 |
| M5 Network / Timeline / Live Data | 🔴 未达到最终目标 | 路由和工作区外壳已形成，但 Propagation 核心 Canvas 未实现，Evidence/Timeline 多处仍是旧组件重新挂载 |
| M6 Signals | 🟡 基本完成 | Alert Adapter 正确，但 Signal API 没有真正复用 Alert 状态转移约束 |
| M7 Report Publishing | 🟠 部分完成 | lifecycle/lock/revise 正确；Publish Gate 没有覆盖真实 Report citation schema |
| M8 Closure | 🟠 未完成 | Posts、M5.7、Subscriptions 分流、Copilot 历史、E2E、完整回归和临时产物清理尚未闭环 |

---

# 3. 本次返工必须继续遵守的执行协议

本节优先级高于后文单个工作包。

## C-01：继续按原子工作包推进

执行顺序必须是：

```text
读取当前代码
→ 完成一个 Closure 工作包
→ 运行该包专项测试
→ 修复失败
→ 运行受影响区域回归
→ 单独提交
→ 再进入下一个工作包
```

禁止再次进行“大批量修改后统一测试”。

## C-02：保持当前已经完成的产品架构，不回退旧工作台

不得为了修复问题重新把功能塞回：

- `CaseWorkspaceView.vue`
- Chat 一级页面
- 全 Case Debate 模式
- 原来的 sidebar-only 产品结构

旧代码可以作为逻辑来源，但新实现必须继续留在 Investigation-centric 架构中。

## C-03：核心 Harness 继续视为保护区

以下语义禁止为了返工而修改：

- Durable Agent Run
- LangGraph checkpoint
- approval interrupt/resume
- runtime case scope 注入
- Tool permission
- Sandbox / egress / secret policy
- cancellation
- Run Event / SSE
- Evidence 不得由 UI Context 替代
- Agent 不得直接产生 `verified Finding`
- Agent 不得直接产生 `published Report`

## C-04：优先复用当前唯一生产路径

遇到已有 Repository/Service/API 时，必须复用或向下抽取共用逻辑。

例如：

- Alert 状态机应抽共用 validator，而不是在 SignalService 再写第二份。
- Copilot 历史应复用旧 `CaseWorkspaceView` 已验证的 `buildChatItems()` 算法，而不是继续维护一个简化版本。
- Propagation Workspace 应读取现有 `PropagationNodeRecord` / `PropagationEdgeRecord` / `SourcePostRecord`，禁止新建第二套图数据表。

## C-05：不得用“UI 不显示”代替后端权限约束

例如 Finding 页面没有“标记 verified”按钮，不代表后端可以允许该状态。

所有 HITL / Evidence / Case scope 约束必须在后端成立。

## C-06：测试必须验证错误路径

每个 P0/P1 修复至少必须包含：

- 正常路径
- 非法状态路径
- 跨 Case 路径
- 不存在资源路径
- 重复/幂等路径（适用时）

---

# 4. 评审发现的问题总表

## P0 — 完成 V2 前必须修复

1. Finding 可以绕过 Review 直接进入 `verified/rejected`
2. Finding 可以绑定不存在或跨 Case 的 Evidence
3. Report Publish Gate 没有验证真实 Report Agent `citation_links[].evidence_ids[]` 结构，未知引用还能被静默跳过

## P1 — 必须在 Closure 阶段完成

4. Propagation Network Workspace 实际未实现
5. Signal API 没有真正复用 Alert 状态机
6. Provenance 仍是局部 one-hop prototype，且受无效 Evidence link 影响
7. Collection Definition 的 `exclusions` / `filters` UI 状态与实际 crawl 行为不一致
8. Evidence Workspace 仍主要是旧 `EvidenceSidebar` 全尺寸挂载
9. Timeline Workspace 仍主要是旧 `NarrativeTimelineView` 路由嵌套
10. Live Data 缺少原始 Posts 列表
11. M5.7：Semantics / Goals 尚未迁入 Investigation
12. Copilot Drawer 历史重建弱于旧 `CaseWorkspaceView`
13. SubscriptionsView 尚未按新 IA 分流
14. 已提交 pytest / MediaCrawler 临时产物尚未从 Git tracking 中清除
15. 完整 backend 回归和浏览器级 E2E 尚未完成

---

# 5. Closure 工作包执行顺序

执行智能体必须按以下顺序推进：

```text
C0  返工基线与仓库清理
C1  Finding HITL 边界
C2  Finding Evidence Integrity
C3  Report Publish Gate
C4  Signal 状态机统一
C5  Provenance 修正
C6  Collection Definition 实际生效
C7  Propagation Network Workspace
C8  Evidence / Timeline / Live Data 深化
C9  M5.7 + Subscriptions 分流
C10 Copilot 历史重建
C11 Legacy 删除与最终 E2E Closure
```

P0 工作包 C1–C3 完成前，不进入 C7 之后的大型前端工作。

---

# 6. C0 — 返工基线与仓库清理

## 目标

在继续改代码前建立“当前 V2 交付版本”的返工基线，并清理已经被错误提交的临时测试产物。

## 先读取

- `docs/optimization-v2-delivery.md`
- `.gitignore`
- 当前 Git status
- 当前被 Git 跟踪的 `.pytest-*-tmp`、MediaCrawler 测试临时目录、`.db`、运行生成 JSONL

## 必须执行

1. 记录当前 HEAD SHA 到新的返工记录章节。
2. 使用 `git ls-files` 确认哪些临时文件已经被 Git tracking。
3. 对已经进入 Git 的测试临时目录执行 `git rm --cached` 或直接删除后提交。
4. `.gitignore` 保留未来忽略规则。
5. 不删除真正作为固定测试 fixture 使用的静态样本；只删除运行时临时输出。
6. 在 `docs/optimization-v2-delivery.md` 追加 Closure 开始记录，不覆盖原交付记录。

## 验收

- `git status` 仅包含预期清理文件
- 后端测试不再在 Git 工作树中生成新的 tracked 临时目录
- 不影响正式 fixture

## 建议提交

```text
chore: clean tracked test artifacts and start optimization v2 closure
```

---

# 7. C1 — 封死 Finding 绕过 Human Review 的状态路径【P0】

## 当前代码事实

主要文件：

- `backend/app/application/finding_service.py`
- `backend/app/api/routes/findings.py`
- `backend/app/services/review.py`
- `backend/app/application/repositories.py`
- `backend/tests/test_findings.py`

当前 `FindingService.ALLOWED_TRANSITIONS` 允许 `under_review → verified/rejected`，并且普通 Finding status API 直接调用 `FindingService.update_status()`。

现有 Review 决策事务已经能够在 `decide_review_item()` 中同步 Finding 状态，因此不需要新建另一套 Review 系统。

## 最终目标

```text
Agent/Materializer
    ↓
candidate
    ↓ 普通 Finding API
under_review
    ↓ 只能由 Review Decision Transaction
verified / rejected
```

## 必须修改

### 7.1 拆分“用户可触发状态迁移”和“Review 内部状态映射”

在 `finding_service.py` 中：

1. 普通 `update_status()` 不再允许任何目标状态为 `verified` 或 `rejected`。
2. 普通状态迁移表明确保留：
   - `candidate → under_review`
   - `under_review → candidate`
   - `candidate/under_review/verified/rejected → superseded`
   - `verified/rejected → under_review`（用于重新提交复审）
3. 当普通 API 尝试设置 `verified/rejected` 时，返回明确业务错误：
   `code = finding_review_required`
4. 不要复用模糊的 `finding_invalid_transition` 来表达“必须经 Review”。

### 7.2 Review 决策继续走现有原子事务

保留 `ApplicationRepository.decide_review_item()` 当前同事务更新 ReviewItem / ReviewDecision / Finding 的实现。

禁止改成：

```text
先提交 Review
再调用 FindingService.update_status()
```

否则会破坏原子性。

### 7.3 Route/Schema 同步收窄

`UpdateFindingStatusRequest` 应只接受普通 API 真正允许的目标状态。

Service 仍保留最终防线。

## 必须新增/修改测试

1. `candidate -> verified` 失败
2. `candidate -> under_review -> verified` 仍失败，错误码 `finding_review_required`
3. `candidate -> under_review -> rejected` 仍失败
4. Review `approved` 后 Finding 原子变 `verified`
5. Review `rejected` 后 Finding 原子变 `rejected`
6. Review decision 失败时 Finding 状态不变化
7. 已 verified Finding 可以重新进入 `under_review`，但再次 verified 仍需 Review

必须改写当前把 `under_review -> verified` 当正常行为的旧测试。

## Definition of Done

- 任意普通 Finding API 都不能直接产生 `verified/rejected`
- Review 为唯一最终裁决来源
- Review 与 Finding 同事务同步仍成立

## 建议提交

```text
fix: enforce review-only finding verification
```

---

# 8. C2 — Finding Evidence Integrity 与 Case Scope【P0】

## 当前问题

当前 Finding evidence link 只验证 Finding 属于当前 Case 和 relation 合法，没有验证 `evidence_ref` 是否真实存在、是否为 EvidenceRecord、是否属于同一 Case。

## 最终目标

本轮 Closure 将 `FindingEvidenceLinkRecord.evidence_ref` 明确定义为：

> **必须引用当前 Case 内真实存在的 `EvidenceRecord.id`。**

原始 Post/Artifact 来源继续使用 `FindingSourceLinkRecord`，不要把 Post/Comment ID 混入 Evidence link。

## 必须修改

主要文件：

- `backend/app/application/finding_service.py`
- `backend/app/infrastructure/database/finding_repository.py`
- `backend/app/api/routes/findings.py`
- `backend/tests/test_findings.py`

### 8.1 新增 Evidence 校验 helper

在 `FindingService` 中增加确定性 helper，例如：

```text
_validate_evidence_ref(case_id, evidence_ref)
```

逻辑：

1. `session.get(EvidenceRecord, evidence_ref)`
2. 不存在 → `finding_evidence_not_found`
3. `evidence.case_id != case_id` → `finding_evidence_scope_mismatch`
4. 成功后才允许创建 link

禁止只通过 `ev-` 前缀判断。

### 8.2 手动 Add Evidence API fail closed

顺序：

```text
校验 Finding
→ 校验 relation
→ 校验 EvidenceRecord + case scope
→ 创建 link
```

### 8.3 Artifact materializer 不允许伪造 Evidence link

`sync_from_artifact()` / `_materialize()` 读取 Expert Artifact Evidence ID 时：

- Finding 本身仍可创建为 candidate
- 不存在/跨 Case Evidence 不创建 link
- 不让整个 Finding materialization 因单个坏引用失败
- 返回可审计 warning

建议扩展 sync 响应：

```json
{
  "created": 1,
  "skipped": 0,
  "warnings": [
    {
      "type": "invalid_evidence_ref",
      "artifact_id": "...",
      "finding_source_path": "...",
      "evidence_ref": "..."
    }
  ]
}
```

### 8.4 幂等

重复 sync 不重复 Finding、不重复 link、不重置人工状态。

## 必须新增测试

1. 真实同 Case Evidence → 成功
2. Evidence 不存在 → 拒绝
3. Evidence 属于其他 Case → 拒绝
4. Artifact 无效 Evidence → Finding 创建、link 跳过、返回 warning
5. 混合合法/非法 Evidence → 只保存合法 link
6. Provenance 不再出现不存在的 Evidence Node

## 建议提交

```text
fix: validate finding evidence references and case scope
```

---

# 9. C3 — 重写 Report Publish Gate 的 Citation Normalization【P0】

## 当前代码事实

主要文件：

- `backend/app/application/report_document_service.py`
- `backend/app/infrastructure/database/report_repository.py`
- `backend/tests/test_report_documents.py`
- 当前 Report Agent / report Artifact 输出定义

真实 Report Artifact 常见：

```json
{
  "citation_links": [
    {
      "conclusion": "...",
      "evidence_ids": ["ev-1", "ev-2"]
    }
  ]
}
```

当前逻辑没有逐个验证 `evidence_ids[]`，并会对无法解析的 ref 直接跳过。

## 最终目标

> 报告中出现的每一个 citation reference，在发布前都必须被解析，并验证真实存在且属于当前 Case。未知 citation shape 不得静默跳过。

## 必须修改

### 9.1 统一归一化为 `(type, id, path)`

替换单一 `_evidence_id_from_ref()`。

至少支持：

- `"ev-1"`
- `{"evidence_id": "ev-1"}`
- `{"evidence": "ev-1"}`
- `{"evidence_ids": ["ev-1", "ev-2"]}`
- `{"finding_id": "..."}`
- `{"finding_ids": ["..."]}`
- `{"artifact_id": "..."}`
- `{"artifact_ids": ["..."]}`
- generic `{"ref": "..."}` / `{"id": "..."}`

generic ref 无类型时：

1. 当前 Case Evidence
2. 当前 Case Finding
3. 当前 Case Artifact
4. 都不存在 → invalid

不能只靠 ID 前缀认定存在。

### 9.2 Unknown shape fail closed

例如：

```json
{"conclusion": "..."}
```

若没有任何可解析引用，生成 `unresolvable_ref`，阻止 publish。

### 9.3 Case scope

至少验证：

- `EvidenceRecord`
- `FindingRecord`
- `ArtifactRecord`

全部要求 `record.case_id == report.case_id`。

### 9.4 错误详情

主 error code 可保持：

`report_publish_validation_failed`

但应返回/记录具体 problems，例如：

```text
citation_links[2].evidence_ids[1] → evidence_not_in_case
```

## 必须新增测试

1. 字符串 Evidence 合法
2. `evidence_ids[]` 全合法
3. 数组含不存在 Evidence → publish 失败
4. 数组含跨 Case Evidence → publish 失败
5. Finding citation 合法
6. Artifact citation 合法
7. generic ref 可解析
8. unknown dict shape → publish 失败
9. API 层真实覆盖跨 Case publish failure

## 建议提交

```text
fix: validate real report citation structures before publish
```

---

# 10. C4 — Signal 与 Monitor 共用同一 Alert 状态机【P1】

## 当前问题

`SignalService.change_status()` 直接调用 `MonitorRepository.set_alert_status()`，而 Repository setter 当前主要只是赋值，因此 Signal API 可能允许非法逆向状态变化。

## 最终目标

Monitor Route 与 Signal Route 共用同一状态转换规则，不复制第二份。

## 必须修改

主要文件：

- `backend/app/api/routes/monitors.py`
- `backend/app/application/signal_service.py`
- `backend/app/infrastructure/database/monitor_repository.py`
- 可新增 `backend/app/services/alert_state.py`
- `backend/tests/test_signals.py`
- 现有 monitor alert 状态测试

### 10.1 抽纯 domain validator

从现有 Monitor 行为中抽出：

```text
validate_alert_transition(current_status, target_status)
```

不访问数据库。

### 10.2 Repository 作为最终防线

`set_alert_status()`：

```text
读取 current
→ validator
→ 非法则抛业务错误
→ 合法更新
```

### 10.3 SignalService 只做 action mapping

```text
acknowledge → acknowledged
resolve → resolved
suppress → suppressed
```

合法性统一由 validator 决定。

## 必须新增测试

- open → acknowledged
- acknowledged → resolved
- resolved → acknowledged 失败
- 其它旧 Monitor 不允许的逆向转换失败
- Signal API 与 Monitor API 对非法转换语义一致

## 建议提交

```text
fix: unify monitor and signal alert transitions
```

---

# 11. C5 — Provenance Correctness 与双向链路补齐【P1】

## 当前问题

当前 Provenance 已有 Case-scoped one-hop 基础，但：

1. Finding upstream 可能输出坏 Evidence ID
2. Artifact root 通过 FindingSourceLink 间接判断存在
3. 未 materialize Finding 的真实 Artifact 可能 404
4. Finding downstream 为空
5. ReportDocument 未进入 provenance

## 最终目标

仍保持 case-scoped one-hop API，不做无限深图遍历，但至少形成：

```text
Evidence → Finding → ReportDocument
Artifact → Finding
```

## 必须修改

主要文件：

- `backend/app/application/provenance_service.py`
- 相关 provenance tests

### 11.1 Artifact resolver

直接读取 `ArtifactRecord` 并校验 `case_id`。

FindingSourceLink 只用于 downstream。

### 11.2 Finding Evidence upstream

兼容历史脏数据：

- link 指向不存在 Evidence 时，不输出正常 Evidence node
- `warnings` 返回 `dangling_evidence_ref`

### 11.3 Finding downstream → ReportDocument

复用 C3 citation normalizer，不复制 citation parser。

### 11.4 增加 `report_document` root

ReportDocument：

- upstream：Evidence / Finding / Artifact citations
- downstream：后续 revision（若现有 `supersedes_id` 可查询）

### 11.5 跨 Case 不泄漏

继续统一返回 `provenance_object_not_found`。

## 必须新增测试

- 真实 Artifact 无 Finding 仍可查
- Artifact → Finding
- Evidence → Finding
- Finding → ReportDocument
- ReportDocument → refs
- dangling link → warning
- 跨 Case root → 统一 404

## 建议提交

```text
fix: complete case-scoped provenance relationships
```

---

# 12. C6 — Collection Definition 的 exclusions / filters 必须真正生效【P1】

## 当前代码事实

主要文件：

- `backend/app/application/collection_service.py`
- `backend/app/harness/tool_factory.py`
- `backend/app/application/ports/crawler.py`
- crawler adapter
- Collection 前端组件/API

当前 Active Definition 能影响 `keywords`，但 `exclusions` / `filters` 没有真正进入采集行为。

## 最终方案

### 12.1 exclusions 在项目自身 crawl handler 中过滤

不修改第三方 MediaCrawler 搜索 DSL。

流程：

```text
crawler.collect()
→ normalized posts
→ apply_collection_filters()
→ coverage / persistence
```

对 exclusion：

- 在当前 normalized post 可访问的 title/content/description 等文本字段上做 case-insensitive substring 排除
- 命中任一 exclusion 的 post 不进入持久化
- comment 跟随被排除父记录处理

### 12.2 filters 只支持当前可确定映射的 key

先读取当前 Collection UI 实际生成的 filter keys。

对当前能明确映射到 normalized 数据的 key 实现确定性过滤。

未知 filter key：

```text
collection_filter_unsupported
```

禁止“保存成功、运行时忽略”。

### 12.3 Crawl audit

返回/trace 增加：

```json
{
  "collection_definition": {"id": "...", "version": 3},
  "collection_filter_stats": {"before": 120, "after": 98, "excluded": 22}
}
```

## 必须新增测试

- active definition keyword 继续生效
- exclusion 真实过滤
- 未知 filter 明确失败
- 无 active definition 时旧路径不变
- approval / sandbox 相关既有测试全部保持

## 禁止

- 不得只在 UI 上过滤
- 不得绕过 SocialCrawlerPort
- 不得修改 approval/sandbox 顺序
- 不得新增通用查询语言

## 建议提交

```text
fix: apply active collection exclusions and supported filters
```

---

# 13. C7 — 实现真正的 Propagation Network Workspace【P1】

## 当前代码事实

当前 `InvestigationNetworkView.vue` 的 Propagation 模式挂载 `VisualSidebar`，而 `VisualSidebar.vue` 仅展示 `PlatformComparisonCard`。

后端已有：

- `PropagationNodeRecord`
- `PropagationEdgeRecord`
- `GET /cases/{case_id}/propagation-edges`
- edge 的 source/target/relation/confidence/feature_scores/evidence_ids/human_confirmed

因此禁止新建第二套传播图表。

## 最终页面

```text
┌ Toolbar ─────────────────────────────────────────────┐
│ Propagation | Alignment | Integrity | Filters        │
├─────────────────────────────┬────────────────────────┤
│                             │ Selected Object Detail │
│      Propagation Graph      │ relation / confidence  │
│      nodes + edges          │ feature scores         │
│                             │ evidence IDs           │
│                             │ review state           │
└─────────────────────────────┴────────────────────────┘
```

## 后端实施

### 13.1 新增 graph DTO endpoint

推荐：

```text
GET /api/v1/cases/{case_id}/propagation-graph
```

返回：

```json
{"nodes": [...], "edges": [...]}
```

数据只能来自现有持久化表。

执行前读取 `PropagationNodeRecord` 的真实字段；映射 UI DTO，不修改表迎合 UI。

Node DTO 使用当前已有字段，并可 join `SourcePostRecord` 提供 platform/label/excerpt/published_at。

Edge 复用当前 `PropagationEdgeResponse` 核心字段。

## 前端实施

### 13.2 新建真正图组件

建议：

```text
frontend/src/components/network/PropagationGraph.vue
frontend/src/components/network/PropagationDetailPanel.vue
```

项目已经依赖 ECharts，优先使用 ECharts graph series，不再引入大型图库。

### 13.3 视觉语义

至少区分：

- observed / confirmed
- inferred
- human rejected（若当前模型可表达）
- confidence

### 13.4 Selection → Copilot Context

Node：

```text
workspace=network
selected_type=propagation_node
selected_id=node.id
```

Edge：

```text
workspace=network
selected_type=propagation_edge
selected_id=edge.id
```

### 13.5 Detail

Edge 至少显示：

- relation
- confidence
- algorithm version
- feature scores
- evidence IDs
- human confirmation
- Evidence / Provenance 导航

Node 只展示真实已有数据，不把 candidate origin 描述成事实。

### 13.6 Human confirmation

继续复用现有 edge confirmation API。

确认传播边不得自动生成 verified Finding；若需要 promotion，只能创建 candidate Finding。

## 必须新增前端测试

- Propagation mode 不再渲染 `PlatformComparisonCard`
- graph 数据映射
- node/edge selection context
- detail confidence/evidence
- loading/empty/error

## Definition of Done

`Network → 传播网络` 展示真实传播图。

## 建议提交

```text
feat: implement investigation propagation network workspace
```

---

# 14. C8 — Evidence / Timeline / Live Data 工作区深化【P1】

建议拆为 C8.1–C8.3。

## C8.1 Evidence Workspace

### 当前问题

`InvestigationEvidenceView.vue` 主要是 `EvidenceSidebar(open=true)`。

### 实施

不重写 Evidence backend。

将 `EvidenceSidebar.vue` 的业务内容抽为可复用组件，例如：

```text
EvidenceClaimList.vue
EvidenceDetailPanel.vue
```

旧 Sidebar 可临时包装这些组件；新 Workspace 直接使用内容组件。

### 页面

左侧：

- Claim list
- status/verdict/confidence
- filter：all/pending/verified/rejected

右侧：

- Claim 全文
- support/oppose/context Evidence 分组
- source metadata
- review action
- related Findings
- provenance link

直接复用现有 `EvidenceItem.stance = support/oppose/context`。

### Copilot Context

Claim：

```text
workspace=evidence
selected_type=claim
selected_id=claim.id
```

Evidence：

```text
workspace=evidence
selected_type=evidence
selected_id=evidence.id
```

---

## C8.2 Timeline Workspace

### 当前问题

`InvestigationTimelineView.vue` 当前直接嵌入 `NarrativeTimelineView`。

### 实施

抽出实际内容组件，例如：

```text
TimelineWorkspaceContent.vue
```

新旧 route 临时复用。

至少提供：

1. Volume Timeline
2. Platform Timeline
3. Narrative Timeline

如果缺少聚合 API，可在现有持久化数据上增加轻量 read-only 聚合 endpoint，不新增持久化表。

### Time Range Context

用户选择时间段：

```json
{
  "workspace": "timeline",
  "time_range": {"start": "...", "end": "..."}
}
```

---

## C8.3 Live Data Posts

### 后端

在现有 `SourcePostRecord` / SocialRepository 上新增分页 API：

```text
GET /api/v1/cases/{case_id}/posts
```

参数至少：

```text
platform?
q?
from?
to?
limit
cursor 或 offset
```

必须分页。

Response 仅暴露当前真实稳定字段。

### 前端

Live Data tabs：

```text
Posts | Media | Platform Comparison
```

Posts 支持：

- platform filter
- keyword filter
- time range
- open source URL
- select → Copilot context

Post selection：

```text
workspace=live_data
selected_type=social_post
selected_id=post.id
```

## 测试

- backend case scope/pagination/filter
- frontend posts tab
- selection context
- empty/error/loading

---

# 15. C9 — M5.7 与 Subscriptions 分流【P1】

## C9.1 SemanticAnnotations → Evidence

1. 抽主要内容为 `SemanticAnnotationsPanel.vue`
2. Evidence Workspace 增加 `Claims / Evidence / Semantics` 子 tab
3. `/semantics` 改兼容重定向到对应 Investigation Evidence
4. 后端 semantics API 保留

## C9.2 GoalPlanning → Overview / Plan

1. 抽 `GoalPlanPanel.vue`
2. Investigation Overview 加入 Plan 区域/子 tab
3. `/goals` 改兼容重定向
4. 继续复用现有 Goal/Plan backend

## C9.3 SubscriptionsView 分流

当前包含：

- subscriptions
- webhook endpoints
- deliveries
- share

最终方案固定如下。

### A. Subscriptions / Endpoints / Deliveries

迁入：

```text
Administration → Notifications
```

新增：

```text
AdministrationNotificationsView.vue
```

保留现有 Case selector，后端不需要重构。

### B. Share

从 SubscriptionsView 移出。

Report 分享进入：

```text
Reports / Report Detail → Share
```

Artifact/Narrative 分享可留在对象 detail action；本轮最少保证 Report 分享迁移。

### C. Legacy `/subscriptions`

新页面接管后：

```text
/subscriptions → /administration/notifications
```

---

# 16. C10 — Copilot Drawer 使用旧工作台已验证的完整历史重建算法【P1】

## 当前问题

Copilot Drawer 自己维护简化版 history merge，旧 `CaseWorkspaceView.vue` 已有更完整的 `buildChatItems()`：

- expert assistant turn 归属
- coordinator final assistant
- orphan artifacts
- run without turn
- rebuild 状态保留

## 最终方案

### 16.1 抽共享 helper

新增：

```text
frontend/src/services/chat/buildChatItems.ts
```

把旧 `CaseWorkspaceView` 已验证算法迁入。

### 16.2 CopilotDrawer

删除 Drawer 内简化：

- `findAssistantAfter`
- `buildItems`

直接用共享 helper。

### 16.3 refresh 状态保留

继续保留：

- trace
- traceLoading
- approvals
- liveEvents
- liveToolCalls
- liveModelCalls
- artifact error state

### 16.4 CaseWorkspaceView

在最终删除前，也改用共享 helper，确保只有一份逻辑。

## 必须新增前端测试

1. coordinator + expert child
2. 多 assistant turns
3. expert turn 不被 coordinator 消费
4. orphan artifact
5. refresh 后 approval 保留
6. refresh 后 trace 保留

## 建议提交

```text
fix: share robust chat history reconstruction with copilot
```

---

# 17. C11 — Legacy 删除与最终 Closure【P1】

只有 C1–C10 完成后执行。

## 17.1 Legacy 删除条件

### `CaseWorkspaceView.vue`

只有当：

- Copilot history 已共享
- Evidence/Network/Timeline/Live Data/Findings/Report/Activity 都有新 route
- Semantics/Goals 已迁入
- router 已无生产引用

才删除。

### `VisualSidebar.vue`

PropagationGraph 和 Live Data PlatformComparison 接管后，如无生产引用则删除。

### `EvidenceSidebar.vue`

若无兼容入口则删除；仍有 legacy wrapper 时延后至 route 清除。

## 17.2 完整后端回归

不能只跑 133 项核心回归。

必须在最终代码上跑完整 backend pytest，并记录：

```text
passed = ?
failed = 0
skipped = ?（必须说明）
```

不得把改动前 `778 passed` 当作改动后全量结果。

## 17.3 前端完整质量门

按当前 `frontend/package.json`：

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

全部通过。

## 17.4 浏览器级 E2E

必须运行现有：

```bash
npm run e2e:smoke
npm run e2e:interact
```

如覆盖不足，扩展现有脚本，不新建第三套 E2E 框架。

### Scenario A — Investigation + Copilot

```text
打开 Investigation
→ Evidence
→ 选择 Claim
→ Copilot context 正确
→ 发送问题
→ Run 正常完成
```

### Scenario B — Finding Review

```text
candidate Finding
→ 提交审核
→ 普通 API 无法 verified
→ Review approve
→ verified
```

### Scenario C — Report Publish

```text
导入 report artifact
→ citation 合法
→ publish
→ revision 新 draft
```

失败场景：

```text
不存在/跨 Case citation
→ publish 被阻止
```

### Scenario D — Propagation Network

```text
Network/Propagation
→ 图真实渲染
→ 选择 edge
→ Detail confidence/evidence
→ Copilot context 更新
```

### Scenario E — Live Data

```text
Posts
→ platform filter
→ 选择 post
→ Copilot context 更新
```

### Scenario F — Signals

```text
open
→ acknowledge
→ resolve
→ 非法逆向转换被拒绝
```

## 17.5 更新交付文档

在 `docs/optimization-v2-delivery.md` 追加 Closure：

- C0–C11 commit SHA
- 修复结果
- full backend test
- frontend 四项质量门
- E2E A–F
- 真正剩余限制

## Optimization V2 CLOSED 条件

```text
[ ] Finding verified/rejected 只能来自 Review
[ ] Finding 不存在/跨 Case Evidence 被拒绝
[ ] Report 真实 citation schema 全量校验
[ ] Signal / Monitor 共用状态机
[ ] Provenance 无伪造 Evidence node
[ ] Collection UI 已激活字段真实影响 crawl
[ ] Network/Propagation 是真实 graph workspace
[ ] Evidence 是真正 workspace
[ ] Timeline 有 time-range context
[ ] Live Data 有分页 Posts
[ ] Semantics/Goals 完成迁移
[ ] Subscriptions 完成新 IA 分流
[ ] Copilot 使用共享完整历史重建
[ ] legacy 满足条件后删除/redirect
[ ] tracked test temp artifacts 清理
[ ] full backend pytest 0 failed
[ ] frontend typecheck 通过
[ ] frontend lint 通过
[ ] frontend test 通过
[ ] frontend build 通过
[ ] browser E2E Scenario A–F 通过
```

---

# 18. 对执行智能体的提交与报告要求

每个工作包结束后必须：

1. 独立 commit，不混入无关 Closure 项。
2. 使用明确 `fix:` / `feat:` / `chore:`。
3. 在 `optimization-v2-delivery.md` Closure 区域追加：
   - commit
   - 修改文件
   - 核心行为变化
   - 专项测试命令
   - 测试结果
4. 文件移动/职责变化时适配当前唯一生产路径，不因路径差异停止。
5. 只有触及原 V2 E-04 Harness 保护区且无法兼容时才暂停并报告。

推荐提交序列：

```text
chore: clean tracked test artifacts and start optimization v2 closure
fix: enforce review-only finding verification
fix: validate finding evidence references and case scope
fix: validate real report citation structures before publish
fix: unify monitor and signal alert transitions
fix: complete case-scoped provenance relationships
fix: apply active collection exclusions and supported filters
feat: implement investigation propagation network workspace
feat: deepen investigation evidence workspace
feat: add paginated live data posts
feat: integrate investigation timeline context
refactor: migrate semantics and goals into investigation
refactor: split subscriptions into administration and report sharing
fix: share robust chat history reconstruction with copilot
chore: remove retired legacy workspace paths
test: close optimization v2 browser and full regression matrix
```

---

# 19. 下一次评审重点

下一轮不再主要检查“页面/API 是否存在”，而重点验证四类不变量。

## A. Product State 是否真实

- Active Collection 真的影响 crawl
- verified 真的意味着 Human Review
- published 真的意味着 citation gate 通过
- Signal 状态真的受统一状态机约束

## B. Evidence Grounding 是否真实

关键链路必须是：

```text
Finding
→ EvidenceRecord
→ Claim / Source
```

不能再把“看起来像 Evidence ID 的字符串”当 Evidence。

## C. Investigation Workspace 是否真正可调查

尤其检查：

- Propagation graph 真实可交互
- Evidence 按 Claim 组织和查看
- Timeline 产生 time-range context
- Live Data 查看真实原始帖子

## D. Closure 是否被测试证明

必须是：

```text
完整后端回归
+ 前端 typecheck/lint/test/build
+ 浏览器 E2E
```

不能用改动前 baseline 或局部测试替代最终验收。

---

# 20. 本轮返工最终目标

本次 Closure 完成后，Nothing-in-the-dark 应真正达到：

> **Investigation-centric、Evidence-grounded、Human-review-governed、Agent-assisted Social & Narrative Intelligence Workbench**

其中：

- Chat/Copilot 是调查辅助，而不是页面结构本身；
- Finding 是可管理、可挑战、可人工裁决的调查结论；
- Evidence 是真实、Case-scoped、可追溯的数据；
- Network 是调查 Canvas，而不是普通统计图；
- Report 的 `published` 具有确定性质量含义；
- Signal 使用统一 operational state machine；
- Collection UI 中激活的定义真实影响采集；
- 所有关键能力通过完整回归和浏览器闭环验证。

在以上 Closure 工作包完成前，不建议开始新的大范围产品能力扩展。
