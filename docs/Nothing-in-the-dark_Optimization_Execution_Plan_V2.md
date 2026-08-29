# Nothing-in-the-dark 本轮产品化优化执行计划 V2（执行智能体实施规格）

> 版本：V2 Execution Specification  
> 基线仓库：`Ethan-Martinez-creater/Nothing-in-the-dark`，计划制定时基于 `main` 分支现有实现  
> 面向对象：负责直接修改仓库代码、运行测试、提交实现的执行智能体  
> 目标：在不重写现有 Harness 核心的前提下，将项目演进为 Investigation-centric、Evidence-grounded、Agent-assisted Social & Narrative Intelligence Workbench。

---

## V2 相比 V1 的新增内容

V1 已经定义了产品目标、M0–M8 实施阶段、主要数据模型、API、页面与非目标。V2 **不改变 V1 的产品方向**，而是在其上增加执行智能体真正落地所需的实施约束：

1. **当前代码真实接线点**：明确指出现有函数和文件，例如 `AgentRunService.start()`、`GraphWorker._finalize_expert_run()`、`tool_factory.crawl()`、`ReviewService`、`ApplicationRepository.delete_case()` 等应如何改。
2. **原子任务拆分**：把 M0–M8 拆成可单独开发、测试和验收的 `Mx.y` 工作包，禁止一次性重构全部系统。
3. **确定性数据语义**：补全 FK、删除策略、版本、状态机、并发、跨 Case scope、历史兼容等规则，减少执行智能体自行选方案。
4. **页面级交互规格**：明确主区域、详情面板、默认状态、选择行为、Copilot 上下文、空/错/加载状态，防止只把 Sidebar 换成 Page 而未完成 Investigation UX。
5. **旧实现迁移与删除矩阵**：定义旧组件何时复用、何时 redirect、何时可以删除。
6. **执行决策树与停止条件**：遇到仓库实现差异时优先适配现有代码；只有触及核心安全/运行语义才暂停该子任务。
7. **完整验收矩阵**：每个工作包都有修改范围、禁止方案、测试和 Definition of Done。

---

# V2 执行总协议（必须优先于后文所有建议）

执行智能体必须遵守以下工作协议。

### E-01：按工作包推进，不得一次执行 M0–M8

正确节奏：

```text
读取当前代码 → 实现一个 Mx.y → 运行该工作包测试 → 修复 → 运行阶段回归 → 再进入下一个 Mx.y
```

禁止：

```text
先批量创建所有表和页面 → 最后统一修测试
```

### E-02：先确认代码事实，再修改

每个工作包都列有“先读取”。执行前必须打开对应文件并确认函数仍存在。

若文件名变化但职责不变：适配当前路径。

若职责已经被重构：选择**当前唯一生产路径**完成同一目标，不要把旧架构重新引回来。

### E-03：不允许执行智能体重新做产品方案选择

本文中使用“必须”“应直接”“禁止”的地方视为确定方案。

只有以下情况允许实现层小范围选择：

- 组件内部函数拆分；
- CSS class 命名；
- Repository 私有 helper 命名；
- 测试 fixture 组织；
- 在不改变 API/状态机的前提下选择等价 SQLAlchemy 查询表达式。

### E-04：核心 Harness 属于保护区

以下语义视为不可变约束：

- Durable Agent Run；
- LangGraph checkpoint；
- approval interrupt/resume；
- Tool permission；
- runtime case scope 注入；
- sandbox / egress / secret policy；
- cancellation；
- Run Event/SSE；
- Evidence 不得由 UI Context 替代；
- Agent 不得直接产生“verified Finding”或“published Report”。

### E-05：数据库变更必须向后兼容

新增表/列允许；不得为了产品命名迁移已有 `cases`、`case_id`、`agent_runs`、`artifacts` 等核心表。

旧 Case 在没有新增数据时必须仍能打开和运行。

### E-06：所有新增 ID API 都必须做 Case scope 验证

禁止通过知道一个 UUID 就读取另一个 Case 的：

- Collection Definition；
- Finding；
- Finding evidence link；
- Provenance；
- Report Document；
- Signal 详情。

### E-07：旧行为只有在新行为完全接管后才能删除

任何 legacy 页面、route、component、API 都必须满足本文“删除条件”后再删除。

### E-08：禁止通过降低质量门槛完成任务

不得：

- skip 测试；
- 删除失败测试；
- 关闭 TypeScript strict/typecheck；
- catch Exception 后静默返回成功；
- 绕过 Approval；
- 把 Sandbox 改成裸执行；
- 用大量 `any` 逃避类型；
- 为了 UI 简化而删除 trace、evidence、version 或 review 信息。

### E-09：遇到阻塞时的决策顺序

```text
1. 是否可复用现有 service/repository？
2. 是否可通过新增 adapter/service 解决？
3. 是否可保持旧 API 并增加新 API？
4. 是否只需要迁移 UI？
5. 只有以上都不成立，才考虑修改核心基础设施。
```

### E-10：需要暂停而不是自行改架构的情况

仅当出现以下情况时停止当前子任务并报告：

- 必须删除/替换 LangGraph 才能完成；
- 必须绕过 Tool Policy/Sandbox；
- 必须破坏已有 Case 数据；
- 当前数据库模型与本文假设完全冲突且无法兼容迁移；
- 现有测试揭示本文目标会违反安全约束。

普通编译错误、文件移动、接口小差异不是暂停理由，执行智能体应自行适配。

---

# Part I — 产品目标、架构边界与阶段规划（V1 保留并作为 V2 的架构主体）

## 0. 文档使用方式

这不是产品建议清单，也不是允许执行智能体自由选型的方案草案。本文件应被视为本轮优化的**实施规格（implementation specification）**。

执行智能体应：

1. 严格按本文件的阶段顺序推进，除修复阻塞问题外不要跨阶段并行大改。
2. 每个阶段开始前先阅读本文列出的现有文件，确认当前代码没有发生与本计划冲突的重大变化。
3. 优先复用现有 Repository、Service、API、Vue Component 和 Harness 原语；没有必要时不得重新实现已有能力。
4. 每完成一个阶段必须运行对应测试并满足阶段验收标准，再进入下一阶段。
5. 若实现过程中发现现有代码与本文描述存在小范围差异，应按“**保持现有行为 + 达成本文目标**”原则适配，不得擅自扩大目标。
6. 若发现必须改变核心 Harness 语义、数据安全策略或持久化模型的高风险冲突，应停止该子任务并形成明确说明；不要通过删除安全检查、绕过审批、关闭测试等方式“完成”任务。
7. 不得以“重写整个前端/后端”作为简化方案。本轮目标是**演进式重构**。

---

# 1. 本轮优化的最终目标

当前系统已经具备较强的 Harness、Evidence、HITL、Durable Runtime、RAG、传播分析、监测与治理能力，但产品呈现仍明显偏向“多功能 Agent Chat”。

本轮目标是把系统从：

> **Chat-centric Multi-Agent 舆情分析 Demo**

重构为：

> **Investigation-centric、Evidence-grounded、Agent-assisted Social & Narrative Intelligence Workbench**

目标产品心智模型：

```text
Workspace
├── Home                  全局态势概览
├── Signals               全局信号/告警收件箱
├── Investigations        调查案例
│   └── Investigation
│       ├── Overview      调查概览、范围、状态、计划
│       ├── Live Data     已采集数据、平台数据、媒体资产
│       ├── Evidence      主张、证据、语义信息
│       ├── Network       传播网络、跨平台对齐、完整性信号
│       ├── Timeline      时间线、叙事演化
│       ├── Findings      可管理的调查结论
│       ├── Report        报告草稿、版本、发布
│       └── Activity      Agent 活动、审批、运行轨迹
├── Reports               已发布/待发布报告
└── Administration
    ├── Approvals
    ├── Review
    ├── Memory
    ├── Security
    ├── Observability
    └── Resilience
```

AI 不再是页面结构本身，而是贯穿各调查页面的 **Contextual Copilot**：

- 在 Evidence 页面询问当前 Claim；
- 在 Network 页面解释当前选中节点/边；
- 在 Timeline 页面解释某个峰值；
- 在 Finding 页面挑战某条结论；
- 在 Report 页面基于当前报告草稿发起修订；
- 高级用户仍可展开完整 Run Trace。

---

# 2. 当前实现基线：必须复用的已有能力

执行前应确认以下文件仍是当前实现主路径。

## 2.1 Case 与 Agent 主链

现有：

- `backend/app/api/routes/cases.py`
  - Case CRUD
  - `POST /cases/{case_id}/messages`
  - Runs / Artifacts / Turns 查询
- `backend/app/schemas/cases.py`
  - `CreateCaseRequest`
  - `CaseResponse`
- `backend/app/graphs/agent_loop.py`
  - Durable LangGraph loop
  - steering → model → tool → steering
  - checkpoint + approval interrupt
- `backend/app/harness/runtime.py`
  - Agent 执行、预算、审批、权限、工具调用
- `backend/app/harness/agents.py`
  - Coordinator
  - Opinion / Propagation / Verification / Evidence Critic / Report / Citation Validator
- `backend/app/api/routes/runs.py`
  - steering
  - cancel
  - SSE
  - approve
  - resume
  - trace

**本轮不得重写以上核心执行链。**

## 2.2 Tool、采集与安全

现有：

- `backend/app/harness/tools.py`
- `backend/app/harness/tool_factory.py`
- `backend/app/harness/sandbox.py`
- `collect_social_posts`
- Tool permission / approval / sandbox / secret / egress policy

**采集仍必须经过当前审批、安全与 Sandbox 约束。不得为了新 Collection UI 绕过现有 Tool Registry。**

## 2.3 Evidence / RAG / Review

现有：

- `backend/app/api/routes/evidence.py`
- `backend/app/infrastructure/database/knowledge_repository.py`
- `backend/app/api/routes/reviews.py`
- Claims / Evidence / Review Queue / Comments / Activity
- PostgreSQL keyword + vector hybrid retrieval
- Evidence ID / Claim ID
- 人工 Review

这些能力是新 Evidence / Findings 工作区的基础，不应重新造一套独立 Evidence 存储。

## 2.4 Monitoring

现有：

- `backend/app/api/routes/monitors.py`
- `backend/app/schemas/monitoring.py`
- `Monitor`
- `query_spec`
- schedule
- rules
- executions
- alerts
- alert 状态机：
  - `open`
  - `acknowledged`
  - `resolved`
  - `suppressed`

本轮 Global Signals 应优先把现有 Alert **适配为统一 Signal DTO**，而不是立即新建重复的 Signal 持久化系统。

## 2.5 Report

现有：

- `backend/app/api/routes/artifacts.py`
- `backend/app/services/reports.py`
- report Artifact
- report versions
- diff
- regenerate
- HTML export
- sensitive redaction

新的 Report Publishing 应建立在这些能力上。

## 2.6 前端

现有：

- `frontend/src/App.vue`
  - Project + Case 列表
  - “会话”式左侧导航
  - “治理与控制”折叠菜单
- `frontend/src/router/index.ts`
- `frontend/src/views/CaseDashboardView.vue`
- `frontend/src/views/CaseWorkspaceView.vue`
- `frontend/src/components/CaseComposer.vue`
- `frontend/src/components/chat/*`
- `frontend/src/components/evidence/*`
- `frontend/src/components/visual/*`
- `frontend/src/components/monitoring/*`
- `frontend/src/components/media/*`
- `frontend/src/components/alignment/*`
- `frontend/src/components/integrity/*`
- `frontend/src/components/debate/*`
- `frontend/src/services/api.ts`
- `frontend/src/types/api.ts`

`CaseWorkspaceView.vue` 当前承担过多职责，本轮应逐步拆分，但不能一次删除重写。

---

# 3. 强制边界：本轮明确不做什么

这些边界用于阻止执行智能体在实现过程中失控扩展。

## 3.1 不重命名数据库核心 `case` 域

产品 UI 改称：

- Investigation
- 调查
- 调查案例

但后端当前 `case_id`、`CaseRecord`、`cases` 表本轮保留。

原因：

- 当前大量 Repository、Artifact、Claim、Evidence、Run、Monitor 都依赖 `case_id`；
- 为纯命名一致性进行数据库级全局重命名风险高、收益低。

允许：

- 新 API/DTO 在产品层使用 `InvestigationOverview` 等命名；
- 前端统一显示“调查”。

禁止：

- 批量把所有 `case_id` 改为 `investigation_id`。

## 3.2 不新增更多 Expert Agent

本轮不增加：

- Trend Agent
- Signal Agent
- Timeline Agent
- Collaboration Agent
- Search Agent 等

现有 Agent 已足够支撑本轮。

新增产品能力应优先由：

- Service
- deterministic transformation
- 现有 Agent
- 现有 Artifact

完成。

## 3.3 不重写 Durable Runtime

不得替换：

- LangGraph
- SSE
- Run Event
- Approval interrupt
- AgentRuntime
- ToolRegistry

只允许为了 Contextual Copilot 增加少量运行上下文注入。

## 3.4 不重写传播算法、爬虫、ML Worker

本轮不修改算法目标：

- `propagation_algorithm.py`
- BGE-M3
- Sentiment Worker
- MediaCrawler

除非是新页面调用已有能力时发现明确 Bug。

## 3.5 不实现完整多人身份系统

当前仓库没有成熟 User / Tenant / RBAC 域。

本轮保留：

- `local_operator`
- actor string
- Review claim/release/comment
- Approval

但不新增完整：

- 登录
- OAuth
- Tenant
- RBAC
- Case membership
- public ACL

这些进入下一轮。

## 3.6 不做匿名公网分享

Report 本轮做到：

- Draft
- Review-ready
- Published（系统内部状态）
- Archive
- Export

不做无需认证的公网 share token。

---

# 4. 产品语言与领域对象统一

本轮必须首先统一用户可见术语。

| 当前 UI | 新 UI |
|---|---|
| 会话 | 调查 / Investigation |
| 新建会话 | 新建调查 |
| 对话列表 | 调查列表 |
| Case Workspace | Investigation Workspace |
| 可视化 | Network / Timeline 等业务页面 |
| 完整性 | Network 内的 Integrity 分析 |
| 对齐 | Network 内的 Alignment 分析 |
| 辩论模式 | Finding 的“挑战/红队验证”动作 |
| Agent Run 卡片 | 默认“分析活动”，高级视图才显示 Run Trace |
| 治理与控制 | Administration / 管理 |

后端代码名暂不强制改变。

---

# 5. 目标前端信息架构

## 5.1 Global Shell

左侧一级导航：

```text
Home
Signals
Investigations
Reports
Administration
```

Investigations 下保留现有 Project 分组。

示例：

```text
Investigations
├── 未归类
│   ├── 调查 A
│   └── 调查 B
├── Project X
│   ├── 调查 C
│   └── 调查 D
└── + 新建调查
```

Administration 默认折叠：

```text
Administration
├── Approvals
├── Review
├── Memory
├── Security
├── Observability
└── Resilience
```

不要继续把 Timeline、Goals、Subscriptions、Semantics 放在“治理”中。

## 5.2 Investigation Shell

路由建议：

```text
/investigations/:caseId/overview
/investigations/:caseId/live-data
/investigations/:caseId/evidence
/investigations/:caseId/network
/investigations/:caseId/timeline
/investigations/:caseId/findings
/investigations/:caseId/report
/investigations/:caseId/activity
```

旧：

```text
/cases/:caseId
```

保留兼容 Redirect：

```text
/cases/:caseId -> /investigations/:caseId/overview
```

不要立即删除旧路径。

## 5.3 Copilot

Investigation Shell 右侧放置可展开 Copilot Drawer。

Copilot 是所有子页面共享的。

必须支持当前 UI Context：

```ts
type InvestigationUiContext = {
  workspace:
    | 'overview'
    | 'live_data'
    | 'evidence'
    | 'network'
    | 'timeline'
    | 'findings'
    | 'report'
    | 'activity'
  selected_type?: string
  selected_id?: string
  selected_label?: string
  filters?: Record<string, unknown>
  time_range?: {
    start?: string
    end?: string
  }
}
```

不能仅把 UI Context 拼接成普通用户文本。

应通过后端结构化字段进入 Run metadata / ContextBuilder。

---

# 6. 实施阶段总览

按以下顺序执行：

```text
M0  基线与安全网
 ↓
M1  产品语言 + Router + Global Shell
 ↓
M2  Investigation Shell + Contextual Copilot + Activity 拆分
 ↓
M3  Collection Definition / Saved Search
 ↓
M4  Evidence Workspace + Findings + Provenance
 ↓
M5  Network / Timeline 一等工作区
 ↓
M6  Global Signals Inbox
 ↓
M7  Report Draft / Publish
 ↓
M8  Administration 重组 + 旧页面清理 + E2E 闭环
```

不得把 M3-M7 全部同时重构。

---

# 7. M0：建立基线与安全网

## 7.1 目标

在任何产品结构修改前确保：

- 后端测试基线可记录；
- 前端 typecheck / unit test 可记录；
- 关键 E2E 当前行为可记录；
- 路由和 API 行为有明确快照。

## 7.2 执行任务

### 后端

执行现有测试。

至少覆盖：

- Agent loop
- durable runtime
- approval
- evidence
- monitoring
- report
- review
- propagation
- content security

若全量测试过慢，可先运行专项，再运行全量。

记录：

- pass 数
- fail 数
- 当前已存在失败

不得把已有失败错误归因于本轮。

### 前端

执行：

```bash
npm run typecheck
npm run test
npm run build
```

若 Playwright 环境可用：

```bash
npm run e2e:smoke
npm run e2e:interact
```

### 建立变更检查清单

确认：

- `POST /cases/{id}/messages` 正常
- SSE 正常
- approval 正常
- `GET /cases/{id}/evidence-summary` 正常
- monitor CRUD 正常
- alert 状态变更正常
- report artifact export 正常

## 7.3 验收

M0 不改变业务行为。

产出：

- 本地测试记录
- 若有基线失败，形成 `KNOWN_BASELINE_FAILURES.md` 或在开发记录中明确说明

---

# 8. M1：产品语言、Router 与 Global Shell

## 8.1 目标

先改变用户心智模型，不改后端业务模型。

完成后：

- 用户看到的是 Investigation，而不是 Conversation；
- Global Navigation 已符合目标 IA；
- 旧功能仍可访问；
- Case Workspace 尚未完全拆完也没关系。

## 8.2 修改文件

优先：

- `frontend/src/App.vue`
- `frontend/src/router/index.ts`
- `frontend/src/views/CaseDashboardView.vue`
- `frontend/src/components/CaseComposer.vue`
- `frontend/src/services/api.ts`
- `frontend/src/types/api.ts`

新增：

```text
frontend/src/views/HomeView.vue
frontend/src/views/InvestigationsView.vue
frontend/src/views/SignalsView.vue          # 先建路由骨架，M6 完成内容
frontend/src/views/ReportsView.vue          # 先建路由骨架，M7 完成内容
frontend/src/views/admin/AdminShellView.vue
```

## 8.3 Router 修改

目标：

```ts
/
  -> HomeView

/investigations
  -> InvestigationsView

/investigations/:caseId
  -> redirect overview

/investigations/:caseId/overview
  -> InvestigationShellView + OverviewView

/signals
  -> SignalsView

/reports
  -> ReportsView

/admin/approvals
/admin/reviews
/admin/memories
/admin/security
/admin/observability
/admin/resilience
```

旧路由：

```text
/cases/:caseId
/approvals
/reviews
/memories
/security
/observability
/resilience
```

全部先做 redirect。

不要直接删除。

## 8.4 App Shell 修改

当前 `App.vue` 需要逐步从“大量业务逻辑 + 导航”中抽离。

建议新增：

```text
frontend/src/components/shell/GlobalSidebar.vue
frontend/src/components/shell/GlobalTopbar.vue
frontend/src/components/shell/InvestigationList.vue
```

`App.vue` 负责：

- 加载 capabilities
- 渲染 Global Shell
- RouterView

Investigation CRUD/Project 列表逻辑迁移到：

- `InvestigationList.vue`
- 或 `useInvestigations.ts`

## 8.5 UI 文案修改

必须改：

- 新建会话 → 新建调查
- 对话 → 调查
- 搜索会话 → 搜索调查
- 创建分析案例 → 创建调查
- 会话列表 → 调查列表

可以保留数据库和 API 中 Case 命名。

## 8.6 Home 初版

M1 Home 不需要全部 Operational Dashboard 数据。

先提供：

- 最近 Investigation
- 新建 Investigation
- Pending Approvals 数
- Active Run 简要信息（能低成本获取的前提下）

M6 后再接 Signals。

删除首页技术宣传为主的结构：

- “FastAPI”
- “LangGraph”
- “MCP/A2A”
- 大面积产品能力说明

允许保留小型 About / System info，但不能占主要屏幕。

## 8.7 M1 测试

前端新增：

```text
frontend/src/views/HomeView.test.ts
frontend/src/components/shell/GlobalSidebar.test.ts
frontend/src/router/index.test.ts
```

至少断言：

- 旧 `/cases/:id` redirect
- 新建调查仍调用原 `POST /cases`
- Project 分组仍可使用
- 删除 Investigation 仍按原 Case 删除
- Administration 路由可达

## 8.8 M1 验收

用户进入系统后：

1. 首页是工作状态页面，不再是产品宣传页；
2. 一级概念是“调查”；
3. 原 Case 数据完整保留；
4. 旧 URL 不报 404。

---

# 9. M2：Investigation Shell、Contextual Copilot 与 Activity 拆分

这是本轮最关键的前端结构重构。

## 9.1 目标

把当前：

```text
CaseWorkspaceView
├── Chat
├── Debate
├── Evidence Sidebar
├── Visual Sidebar
├── Monitoring Sidebar
├── Media Sidebar
├── Alignment Sidebar
└── Integrity Sidebar
```

拆成：

```text
InvestigationShell
├── InvestigationHeader
├── InvestigationNav
├── Child Workspace RouterView
└── CopilotDrawer
```

## 9.2 不要一次删除 CaseWorkspaceView

执行方式：

1. 先从 `CaseWorkspaceView.vue` 抽取逻辑；
2. 建新 Shell；
3. 子页面逐步接入；
4. M8 再删除旧 View。

## 9.3 新增前端文件

```text
frontend/src/views/investigation/InvestigationShellView.vue
frontend/src/views/investigation/InvestigationOverviewView.vue
frontend/src/views/investigation/InvestigationLiveDataView.vue
frontend/src/views/investigation/InvestigationEvidenceView.vue
frontend/src/views/investigation/InvestigationNetworkView.vue
frontend/src/views/investigation/InvestigationTimelineView.vue
frontend/src/views/investigation/InvestigationFindingsView.vue
frontend/src/views/investigation/InvestigationReportView.vue
frontend/src/views/investigation/InvestigationActivityView.vue

frontend/src/components/investigation/InvestigationHeader.vue
frontend/src/components/investigation/InvestigationNav.vue
frontend/src/components/copilot/CopilotDrawer.vue
frontend/src/components/copilot/CopilotContextBadge.vue

frontend/src/composables/useRunSubscriptions.ts
frontend/src/composables/useInvestigationContext.ts
```

可选使用 Pinia，但不要为了重构引入大量 store。

若使用 Pinia，最多先建立：

```text
stores/investigation.ts
stores/copilot.ts
```

## 9.4 抽取 Run Subscription

当前 `CaseWorkspaceView.vue` 内有：

- EventSource
- cursor
- polling fallback
- run finalize
- live model/tool calls
- approvals
- trace

将这些逻辑迁移至：

```ts
useRunSubscriptions()
```

必须保持当前语义：

- 每个 active run 独立订阅；
- cursor 去重；
- SSE error → polling；
- polling 无新事件 → 重建 SSE；
- terminal → 拉全量 trace；
- 404 → 停止订阅；
- approval 状态不能因为 UI 重建而丢失。

不要简化掉 polling fallback。

## 9.5 Contextual Copilot 后端修改

### 修改 Schema

找到：

- `CreateMessageRequest`（位于现有 run schema）

增加：

```python
class UiContext(BaseModel):
    workspace: Literal[
        "overview",
        "live_data",
        "evidence",
        "network",
        "timeline",
        "findings",
        "report",
        "activity",
    ]
    selected_type: str | None = None
    selected_id: str | None = None
    selected_label: str | None = None
    filters: dict[str, object] = {}
    time_range: dict[str, str | None] | None = None
```

`CreateMessageRequest`：

```python
ui_context: UiContext | None = None
```

### 修改 AgentService

`agent_service.start(...)` 增加可选 `ui_context`。

存入 Run metadata：

```json
{
  "ui_context": {
    "workspace": "network",
    "selected_type": "propagation_edge",
    "selected_id": "..."
  }
}
```

不得信任客户端传入 `case_id`。

### 修改 ContextBuilder

在 `backend/app/application/context_builder.py` 中：

- 读取 run metadata 的 `ui_context`
- 生成独立 system context block

格式示意：

```text
当前用户界面上下文：
- 工作区：Network
- 当前选中对象：propagation_edge / xxx
- 当前过滤条件：...
```

规则：

- 只是上下文，不代表证据；
- selected_id 若需要事实内容，Agent 必须通过 Tool 查询；
- UI context 不得自动提升 trust；
- 不得把 filters 当作用户授权边界。

## 9.6 前端 sendMessage

`frontend/src/services/api.ts`：

```ts
sendMessage(
  caseId,
  content,
  approveCrawl,
  artifactId,
  uiContext
)
```

Investigation child page通过共享 context provider 设置：

```ts
setUiContext({
  workspace: 'network',
  selected_type: 'propagation_edge',
  selected_id: edge.id,
})
```

Copilot 发消息时自动带上。

## 9.7 Copilot UI

默认：

- 右侧窄 Drawer；
- 可折叠；
- 显示当前上下文 Badge；
- 用户可“一键清除选中对象”，但 workspace context 保留；
- 保留当前 ChatThread 的大部分消息渲染；
- 不在所有页面重复创建独立聊天记录，仍属于同一 Case Turn/Run 历史。

高级运行内容不在默认消息流展开。

## 9.8 Activity 页面

Activity 页面负责承接：

- Run 列表
- 当前运行
- approvals
- semantic events
- advanced trace

默认显示语义化活动：

```text
正在收集微博与知乎数据
已完成观点分析
发现 3 个传播源头候选
事实核查生成 5 张核查卡
等待批准扩大真实采集范围
报告生成完成
```

只有点击：

```text
查看技术轨迹
```

才显示：

- model call
- tool call
- token
- cost
- retry
- raw event

## 9.9 Semantic Event Mapper

新增前端：

```text
frontend/src/services/activityFormatter.ts
```

纯函数：

```ts
formatRunEvent(event): SemanticActivity
```

不要改变后端 Event 类型。

至少映射：

- agent_queued
- agent_start
- expert_dispatched
- expert_completed
- expert_failed
- tool_execution_start/end
- approval_required
- approval_pending
- steering_received/applied
- agent_end
- agent_error

未知 Event：

- Advanced Trace 显示 raw
- 默认 Activity 不崩溃

## 9.10 Debate 调整

从 Investigation Header 删除“对话 | 辩论”一级切换。

保留：

- `DebatePanel`
- Debate backend APIs

M4 后在 Finding Detail 加：

```text
挑战此结论
```

初版实现可以：

1. create debate；
2. 自动发送 Finding statement + evidence refs 为首条上下文；
3. 打开 Debate Modal / Drawer。

不需要本阶段新增 Debate 数据库字段。

## 9.11 M2 验收

必须完成：

- Investigation 子路由可切换；
- Run 仍能实时更新；
- approval 仍能工作；
- Copilot 在不同 workspace 发送 structured `ui_context`；
- Advanced Trace 仍可看到原完整 trace；
- Header 不再有 Debate 一级模式。

---

# 10. M3：Collection Definition / Saved Search

这是把“Agent 隐式搜索”升级成“可查看、可编辑、可版本化的采集定义”。

## 10.1 目标

每个 Investigation 有一个显式 Active Collection Definition。

用户可以看到：

- 自然语言调查目标
- 平台
- 各平台关键词
- 排除词
- 时间范围
- 可选账号/URL过滤
- 版本
- 当前 Active 状态

Agent 可以帮助生成，但最终配置是可见对象。

## 10.2 数据模型

新增表：

```text
collection_definitions
```

字段建议：

```text
id                  UUID/string PK
case_id             FK cases.id
version             int
status              draft | active | superseded
goal                text
platforms           JSON
platform_queries    JSON
exclusions          JSON
filters             JSON
generated_by_run_id nullable
created_at
updated_at
```

约束：

- `(case_id, version)` unique
- 一个 Case 同时最多一个 active，由 service transaction 保证
- 激活新版本时旧 active → superseded
- 不物理覆盖旧版本

不要使用单行 mutable JSON 覆盖历史。

## 10.3 Migration

新增下一顺序 Alembic migration。

禁止修改旧 migration。

SQLite 与 PostgreSQL 均应可运行。

## 10.4 Backend 层

新增：

```text
backend/app/schemas/collections.py
backend/app/services/collection_definitions.py
backend/app/api/routes/collections.py
```

Repository：

优先在当前 ApplicationRepository 增加：

```python
create_collection_definition(...)
list_collection_definitions(case_id)
get_collection_definition(id)
get_active_collection_definition(case_id)
activate_collection_definition(case_id, definition_id)
```

若 ApplicationRepository 已过大，可建：

```text
CollectionDefinitionRepository
```

但必须在 `ApplicationContainer` 中按当前依赖模式装配。

不要直接在 API route 写 SQL。

## 10.5 API

建议：

```http
GET  /cases/{case_id}/collection-definitions
GET  /cases/{case_id}/collection-definitions/active
POST /cases/{case_id}/collection-definitions
POST /cases/{case_id}/collection-definitions:generate
POST /cases/{case_id}/collection-definitions/{id}:activate
```

更新采用新版本，而不是 PATCH 原版本：

```http
POST /cases/{case_id}/collection-definitions/{id}:revise
```

Request：

```json
{
  "goal": "...",
  "platforms": ["weibo", "zhihu"],
  "platform_queries": {
    "weibo": ["词1", "词2"],
    "zhihu": ["词3"]
  },
  "exclusions": ["广告"],
  "filters": {}
}
```

## 10.6 Generate 逻辑

不要新增 Search Agent。

复用现有：

```python
generate_platform_keywords(...)
```

路径现位于 Harness search optimizer。

建议抽一个可复用纯 application/service boundary：

```text
CollectionPlanningService
```

规则：

1. 若 LLM configured：
   - 使用现有平台关键词优化能力；
2. 若失败：
   - 每个平台 fallback 到 `case.topic`；
3. 生成结果先保存为 `draft`；
4. 不自动激活；
5. UI 必须让用户确认。

## 10.7 collect_social_posts 接线

当前 `collect_social_posts` 在执行时动态 `generate_platform_keywords(...)`。

修改为：

```text
if case_id 有 active collection:
    使用 active platform_queries
else:
    保持现有 generate_platform_keywords fallback
```

注意：

- 不改变 approval；
- 不改变 sandbox；
- 不改变 crawler；
- 不允许模型通过 tool arguments 指定其他 case definition；
- active definition 由 runtime 注入的 `case_id` 查询。

Tool Output 增加可选诊断字段：

```json
{
  "collection_definition_id": "...",
  "collection_version": 3
}
```

便于审计。

## 10.8 Monitor 接线

现有 `Monitor.query_spec` 保留。

创建 Monitor 时：

- UI 默认从 Active Collection Definition 预填；
- 保存为 snapshot；
- 可在 `query_spec` 中附：

```json
{
  "collection_definition_id": "...",
  "collection_definition_version": 3,
  ...
}
```

不要让 Monitor 每次执行动态引用最新 Active，否则历史执行不可复现。

## 10.9 前端

新增：

```text
frontend/src/components/collection/CollectionDefinitionCard.vue
frontend/src/components/collection/CollectionDefinitionEditor.vue
frontend/src/components/collection/CollectionVersionList.vue
```

放在：

- Investigation Overview
- 或 Live Data 顶部

UI：

```text
采集定义 v3 · ACTIVE

微博
  + 关键词 A
  + 关键词 B
  - 排除 C

知乎
  + ...

[编辑新版本] [历史版本]
```

首次没有定义：

```text
生成建议采集方案
```

不要直接弹出复杂高级 Boolean Builder。

本轮先结构化关键词 + exclusions。

## 10.10 M3 测试

后端：

```text
test_collection_definitions.py
test_collection_tool_integration.py
```

必须测试：

- version increase
- one active
- old version preserved
- case scope
- crawl uses active definition
- no active → current fallback
- monitor snapshot 不随 active version 变化

前端：

- generate draft
- edit
- activate
- show active version

---

# 11. M4：Evidence Workspace、Findings 与双向 Provenance

## 11.1 目标

当前系统拥有：

- Claim
- Evidence
- Artifact
- Review
- Propagation Edge

但缺少面向用户的稳定“调查结论对象”。

新增：

```text
Finding
```

Finding 是用户可以：

- 查看
- 审核
- 接受/拒绝
- 追踪证据
- 挑战
- 引入 Report

的结论对象。

## 11.2 Finding 数据模型

新增：

```text
findings
finding_evidence_links
finding_source_links
```

### findings

```text
id
case_id
kind
title
statement
status
confidence
source_run_id nullable
created_at
updated_at
```

`kind`：

```text
opinion
verification
propagation
narrative
integrity
manual
```

`status`：

```text
candidate
under_review
verified
rejected
superseded
```

不要直接使用 `published`，发布属于 Report 层。

### finding_evidence_links

```text
finding_id
evidence_ref
relation
created_at
```

`relation`：

```text
supports
contradicts
context
```

`evidence_ref` 使用已有稳定 Evidence / Post ref。

### finding_source_links

用于记录 Finding 从哪里产生：

```text
finding_id
source_type
source_id
source_path
```

示例：

```text
artifact
artifact-id
conclusions[0]
```

unique：

```text
(source_type, source_id, source_path)
```

保证重复 sync 不创建重复 Finding。

## 11.3 Finding Service

新增：

```text
backend/app/services/findings.py
backend/app/schemas/findings.py
backend/app/api/routes/findings.py
```

核心：

```python
sync_from_artifact(artifact)
create_manual_finding(...)
list_findings(...)
get_finding(...)
update_status(...)
add_evidence_link(...)
remove_evidence_link(...)
```

## 11.4 自动 materialize 范围

本轮只做明确可确定结构。

### Opinion Artifact

如果：

```json
{
  "conclusions": [
    {
      "claim": "...",
      "evidence_ids": [],
      "confidence": 0.8
    }
  ]
}
```

每个 conclusion → candidate Finding。

### Verification Artifact

如果：

```json
{
  "cards": [
    {
      "claim": "...",
      "verdict": "...",
      "confidence": 0.8,
      "supporting_evidence": [],
      "contradicting_evidence": []
    }
  ]
}
```

每张卡 → verification Finding。

### Propagation

不要把每条 edge 自动变 Finding。

允许：

- `origin_candidates`
- 或用户手动 Promote 某个 edge 为 Finding。

### Report

Report 不反向生成 Finding。

## 11.5 触发方式

最优：

当 Expert Artifact 创建成功后，执行 deterministic：

```python
finding_service.sync_from_artifact(artifact)
```

若当前 Artifact 创建路径较分散，先在 Artifact persistence service 的统一位置接线。

如果不存在统一位置：

可先增加：

```http
POST /cases/{case_id}/findings:sync
```

并在前端/Agent completion 后触发。

但最终应做到 idempotent。

## 11.6 Review 集成

不要再造 Finding Review 状态机。

Finding 的人工 Review 使用当前：

```text
ReviewItem
```

提交：

```text
object_type = "finding"
object_id = finding.id
```

Review 决策完成后，通过 service 同步：

```text
accepted -> finding.status = verified
rejected -> finding.status = rejected
```

需要保证：

- Review Service 是决策事实来源；
- Finding 状态更新应由集成逻辑完成；
- 不允许前端直接越过 Review 把 candidate 改 verified。

## 11.7 Evidence Workspace

把当前 `EvidenceSidebar` 升级为 full view。

布局建议：

```text
┌───────────────┬──────────────────────────────┐
│ Claims/List   │ Selected Claim / Evidence    │
│ Filters       │                              │
│               │ Supporting                   │
│               │ Contradicting                │
│               │ Context                      │
│               │ Source metadata              │
└───────────────┴──────────────────────────────┘
```

功能：

- Claim filter
- platform
- stance
- source type
- unassigned evidence
- selected evidence detail
- source URL
- published time
- evidence → related Finding

## 11.8 双向 Provenance API

新增：

```http
GET /cases/{case_id}/provenance/{object_type}/{object_id}
```

返回：

```json
{
  "object": {...},
  "upstream": [
    {"type": "evidence", "id": "...", "relation": "supports"}
  ],
  "downstream": [
    {"type": "finding", "id": "...", "relation": "used_by"},
    {"type": "report", "id": "...", "relation": "cited_by"}
  ]
}
```

不要一开始造全局通用 graph database。

`ProvenanceService` 可通过现有表 + Finding links 聚合。

支持至少：

- claim
- evidence
- finding
- artifact
- propagation_edge

若某类型暂时没有下游，返回空列表。

## 11.9 Finding 页面

布局：

```text
左：Finding 列表
中：Finding detail
右/Drawer：Evidence + Review history + Copilot
```

Finding Card：

- kind
- statement
- confidence
- status
- evidence count
- review state

动作：

- 提交审核
- 添加/移除证据
- 打开 Provenance
- 挑战此结论
- 加入 Report

## 11.10 “挑战此结论”

复用 Debate：

执行：

1. `createDebate(caseId, finding.title)`
2. `addDebateMessage(...)`

首条 message 自动生成：

```text
请针对 Finding {id} 进行对抗性审查。
结论：...
当前支持证据：...
当前反驳证据：...
重点寻找：
1. 过度推断
2. 反例
3. 替代解释
```

打开 Debate Modal / Drawer。

不要恢复整个 Case 的 Debate 模式。

## 11.11 M4 测试

后端：

- Artifact sync idempotent
- Opinion Finding
- Verification Finding
- Evidence link scope
- Review accepted/rejected sync
- provenance reverse lookup
- different case access denied

前端：

- Evidence full workspace
- Finding list/detail
- review action
- challenge action
- provenance navigation

---

# 12. M5：Network 与 Timeline 一等工作区

## 12.1 目标

把“可视化”从一个技术展示面板变成调查空间。

不要新增算法。

## 12.2 Network 页面组成

整合：

- propagation graph
- origin candidates
- node roles
- alignment
- integrity
- account groups
- edge critique

布局：

```text
┌────────────────────────────────────────────┐
│ Filters / Graph modes                     │
├──────────────────────────┬─────────────────┤
│                          │ Selected object │
│      Graph Canvas        │                 │
│                          │ Evidence        │
│                          │ Confidence      │
│                          │ Reasons         │
│                          │ Findings        │
└──────────────────────────┴─────────────────┘
```

Graph modes：

```text
Propagation
Alignment
Integrity
```

不要把这三个继续作为全局顶部按钮。

## 12.3 Graph Selection

选择 Node / Edge 时：

设置 Copilot Context：

```ts
{
  workspace: 'network',
  selected_type: 'propagation_edge',
  selected_id: edge.edge_id
}
```

Detail 必须显示：

- observed / inferred
- confidence
- feature_scores
- evidence_ids
- algorithm_version
- human_confirmed（如现有）

不能仅显示漂亮的连线。

## 12.4 Edge → Finding

提供：

```text
提升为调查结论
```

只创建 manual/propagation Finding，不修改传播算法输出。

## 12.5 Timeline 页面

复用：

- Narrative Timeline
- platform time series
- monitoring event timestamps
- run/activity markers（可选）

页面回答：

- 事件何时开始
- 何时爆发
- 哪个平台先变化
- Narrative 如何变化

至少提供：

```text
Volume Timeline
Narrative Timeline
Platform Timeline
```

用户选择时间区间时：

```ts
ui_context.time_range
```

传给 Copilot。

## 12.6 现有页面迁移

当前：

- `NarrativeTimelineView.vue`

迁移为 Investigation Timeline 子组件或路由内容。

当前：

- `AlignmentPanel`
- `IntegrityPanel`
- `VisualSidebar`

重构为全尺寸组件。

不要复制逻辑形成 Sidebar 版和 Page 版长期并存。

可以先让组件支持：

```ts
mode="sidebar" | "page"
```

过渡，M8 删除 sidebar-only 入口。

## 12.7 Media

Media 不单独成为一级 Investigation tab。

放到：

```text
Live Data
```

作为：

```text
Posts | Media
```

子 tab。

## 12.8 Semantics

`SemanticAnnotationsView` 不属于 Administration。

放到 Evidence：

```text
Evidence
├── Claims & Evidence
└── Semantics
```

## 12.9 Goals / Planning

`GoalPlanningView` 不属于 Administration。

放到 Overview：

```text
Overview
├── Scope
├── Collection
├── Investigation Plan
└── Current Status
```

尽量复用现有 Goals API。

## 12.10 M5 验收

- Network 可以全屏工作；
- 每条 Edge 可以回到 Evidence；
- selected node/edge 可进入 Copilot Context；
- Timeline 可选择时间范围；
- Narrative 不再位于治理导航；
- Alignment / Integrity 不再是顶栏独立按钮。

---

# 13. M6：Global Signals Inbox

## 13.1 目标

将现有 Monitor Alert 从 Case 内部功能升级成全局工作流。

本轮不新建 signals table。

通过 adapter 把 Monitor Alert 映射成统一 Signal。

## 13.2 Signal DTO

新增：

```text
backend/app/schemas/signals.py
backend/app/services/signals.py
backend/app/api/routes/signals.py
```

DTO：

```python
class SignalResponse(BaseModel):
    id: str
    source_type: str
    source_id: str
    case_id: str
    case_title: str
    signal_type: str
    severity: str
    status: str
    title: str
    why_it_matters: str
    confidence: float | None
    evidence_refs: dict[str, object]
    detected_at: datetime
    updated_at: datetime
```

当前映射：

```text
source_type = monitor_alert
source_id   = alert.id
```

`signal_type` 来自 rule type：

```text
absolute_volume -> volume_spike
rate_growth     -> growth_spike
anomaly         -> anomaly
key_account     -> key_actor
narrative       -> narrative_shift
```

## 13.3 API

```http
GET /signals
```

Query：

```text
status
severity
case_id
signal_type
limit
```

详情：

```http
GET /signals/{signal_id}
```

动作：

```http
POST /signals/{signal_id}:acknowledge
POST /signals/{signal_id}:resolve
POST /signals/{signal_id}:suppress
```

内部委托现有 Monitor Alert 状态 API / repository。

不要复制 alert status state machine。

## 13.4 Signals 页面

布局：

```text
左：filters
中：signal feed
右：signal detail
```

Signal Card：

- severity
- title
- case
- why it matters
- detected time
- evidence count
- status

动作：

- Acknowledge
- Open Investigation
- Ask Copilot
- Resolve
- Suppress

## 13.5 Home 接线

Home Operational Dashboard 至少展示：

```text
Open Signals
Pending Approvals
Active Investigations
Running Agents
Recent Reports
```

不要在前端对每个 Case 做大量 N+1 请求。

新增 workspace 聚合端点：

```text
backend/app/api/routes/workspace.py
```

例如：

```http
GET /workspace/overview
```

返回轻量 DTO：

```json
{
  "counts": {
    "investigations": 12,
    "open_signals": 5,
    "pending_approvals": 2,
    "running_runs": 3
  },
  "recent_investigations": [],
  "top_signals": [],
  "recent_reports": []
}
```

只返回首页需要的信息。

## 13.6 Monitor UI

Monitoring 不再是 Case Header 顶部按钮。

放到：

```text
Investigation Overview -> Monitoring card
```

卡片：

- active monitor
- schedule
- last execution
- next execution
- open signals
- manage

点击进入配置 Drawer/Modal。

## 13.7 M6 测试

后端：

- alert → signal mapping
- global filters
- signal state delegates to alert
- case scope correct
- workspace overview no N+1-style route composition bug

前端：

- signal filter
- state action
- open case
- home signal widgets

---

# 14. M7：Report Draft、内部 Publish 与 Export

## 14.1 目标

把 report 从“Agent Artifact”升级成用户可管理的最终产物。

保留 Artifact 作为 Agent 输出和历史版本。

新增一个产品层：

```text
ReportDocument
```

## 14.2 数据模型

新增：

```text
report_documents
```

字段：

```text
id
case_id
source_artifact_id
status
title
content_json
version
published_at nullable
created_at
updated_at
```

status：

```text
draft
in_review
published
archived
```

规则：

- draft 可编辑；
- published 不可直接编辑；
- 修改 published 必须 clone 成新 draft；
- published 永远指向冻结内容；
- source_artifact_id 记录来自哪个 Agent report artifact。

## 14.3 不修改 Artifact 的不可变语义

不要让 UI PATCH 原 Report Artifact。

流程：

```text
Report Artifact
   ↓ import
ReportDocument(draft)
   ↓ manual edit
ReportDocument(draft vN)
   ↓ validation
ReportDocument(published)
```

## 14.4 Backend

新增：

```text
backend/app/schemas/report_documents.py
backend/app/services/report_documents.py
backend/app/api/routes/reports.py
```

API：

```http
GET  /reports
GET  /reports/{report_id}

POST /cases/{case_id}/reports:from-artifact
POST /reports/{report_id}:revise
POST /reports/{report_id}:submit-review
POST /reports/{report_id}:publish
POST /reports/{report_id}:archive

GET  /reports/{report_id}/download
```

## 14.5 Draft 更新

使用 optimistic version：

```json
{
  "expected_version": 3,
  "title": "...",
  "content": {...}
}
```

发生冲突：

- 返回明确 409/application error
- 不静默覆盖

## 14.6 Publish Gate

发布前必须 deterministic 校验：

1. Report 属于 Case；
2. 所有 `citation_links` 中的 Evidence ID 存在；
3. Evidence 属于同 Case；
4. 内容通过现有 sensitive redaction/export policy；
5. 若报告状态为 `in_review`，必须满足 Review 规则。

至少做到 1-4。

不要让 Publish 同步等待一次新的 Agent Run。

Citation Validator Agent 可作为：

- “运行高级引用验证”按钮；
- 产生验证 Artifact；

但不是 Publish API 的硬同步依赖。

## 14.7 Export

复用：

```python
render_html_report
```

扩展为接受 ReportDocument `content_json`。

原：

```text
/artifacts/{id}/download
```

继续保留。

新：

```text
/reports/{id}/download
```

用于产品层 Published/Draft export。

本轮只要求 HTML。

PDF/DOCX 可进入后续。

## 14.8 Reports 页面

Global Reports：

```text
Draft
In Review
Published
Archived
```

显示：

- title
- investigation
- source artifact
- status
- updated
- published time

Investigation Report：

- latest Agent report artifact
- Create Draft
- Editor
- Citation validation summary
- Version/Revision
- Publish
- Export

## 14.9 Report Editor

本轮无需做富文本编辑器。

使用结构化编辑：

```text
Title
Executive Summary
Sections
Citation Links
Disclaimer
```

Section 支持：

- edit
- reorder
- remove
- add

避免引入大型 editor dependency。

## 14.10 Finding → Report

Finding 页面动作：

```text
加入报告
```

实现：

- 若无 draft，提示/创建 draft；
- 将 Finding statement + evidence refs 添加为 section 或 citation block；
- 去重 by finding id。

Report content 中建议加入内部字段：

```json
{
  "source_finding_ids": ["..."]
}
```

导出时可以不显示。

## 14.11 M7 测试

后端：

- artifact → draft
- published immutable
- revise creates new version/state
- stale expected_version rejected
- invalid citation blocks publish
- cross-case citation blocked
- export redaction preserved

前端：

- create draft
- edit
- publish
- archived
- add finding
- export

---

# 15. M8：Administration 重组、旧入口清理与闭环

## 15.1 Administration 映射

保留：

```text
ApprovalInboxView
ReviewWorkbenchView
MemoryGovernanceView
SecurityEventsView
ObservabilityView
ResilienceConsoleView
```

移动到：

```text
/admin/approvals
/admin/reviews
/admin/memories
/admin/security
/admin/observability
/admin/resilience
```

页面实现可以继续复用原 View。

## 15.2 非 Administration 页面迁移

### NarrativeTimelineView

并入：

```text
Investigation Timeline
```

### SemanticAnnotationsView

并入：

```text
Investigation Evidence -> Semantics
```

### GoalPlanningView

并入：

```text
Investigation Overview -> Plan
```

### SubscriptionsView

不要继续放 Administration。

按实际现有能力分流：

- 若主要是监测通知订阅 → Signals
- 若主要是报告通知 → Reports
- 若同时包含两者 → Global Settings / Delivery

本轮不要为保持旧页面而破坏新 IA。

## 15.3 删除 CaseWorkspaceView 的条件

只有满足：

- 所有子页面已有新入口；
- Copilot 工作；
- SSE 工作；
- approval 工作；
- evidence 工作；
- network/timeline 工作；
- legacy redirect 工作；

才能删除：

```text
CaseWorkspaceView.vue
```

若部分能力仍依赖它，保留但不作为主 route。

## 15.4 清理旧 Sidebar

M8 删除：

- Case Header 中 Evidence / Visual / Monitoring / Media / Alignment / Integrity 顶部按钮；
- Debate mode slider；
- 旧 sidebar-only 逻辑。

组件若被新 page 复用，不删。

---

# 16. Backend API 目标变更汇总

本轮新增的核心 API：

```text
/workspace/overview

/cases/{case_id}/collection-definitions
/cases/{case_id}/collection-definitions/active
/cases/{case_id}/collection-definitions:generate
/cases/{case_id}/collection-definitions/{id}:revise
/cases/{case_id}/collection-definitions/{id}:activate

/cases/{case_id}/findings
/cases/{case_id}/findings/{id}
/cases/{case_id}/findings:sync
/cases/{case_id}/findings/{id}/evidence
/cases/{case_id}/provenance/{object_type}/{object_id}

/signals
/signals/{id}
/signals/{id}:acknowledge
/signals/{id}:resolve
/signals/{id}:suppress

/reports
/reports/{id}
/cases/{case_id}/reports:from-artifact
/reports/{id}:revise
/reports/{id}:submit-review
/reports/{id}:publish
/reports/{id}:archive
/reports/{id}/download
```

现有 API 保持：

```text
/cases
/cases/{id}/messages
/runs/*
/artifacts/*
/cases/{id}/evidence-summary
/cases/{id}/monitors/*
/cases/{id}/alerts/*
/cases/{id}/reviews/*
```

不要一次废弃。

---

# 17. 数据库迁移计划

建议按依赖分三次 migration，而不是一个超大 migration。

## Migration A

```text
collection_definitions
```

## Migration B

```text
findings
finding_evidence_links
finding_source_links
```

## Migration C

```text
report_documents
```

Signals 不新增表。

每次 Migration：

- PostgreSQL
- SQLite dev
- upgrade
- downgrade（若项目迁移规范要求）
- foreign key
- index

建议 index：

```text
collection_definitions(case_id, status)
findings(case_id, status)
findings(case_id, kind)
finding_evidence_links(finding_id)
finding_source_links(source_type, source_id)
report_documents(case_id, status)
```

---

# 18. Repository / Service 边界

本轮不得继续把所有逻辑堆进 API route。

推荐：

```text
Application/API
    ↓
Service
    ↓
Repository
```

新增：

```text
CollectionDefinitionService
FindingService
ProvenanceService
SignalService
ReportDocumentService
WorkspaceOverviewService
```

Service 负责：

- 状态机
- transaction
- scope
- version
- aggregation

Repository 负责：

- 数据访问

Route 负责：

- validation
- dependency
- response

---

# 19. Harness 层允许的最小修改集合

本轮 Harness 只允许必要变化。

## 19.1 Context

允许：

- CreateMessage ui_context
- run metadata
- ContextBuilder UI context block

## 19.2 Collection

允许：

- `collect_social_posts` 在运行时读取 Active Collection Definition

必须保留：

- runtime-injected case_id
- permission
- approval
- sandbox
- retry
- audit

## 19.3 Finding

不允许让 Agent 直接“写 verified Finding”。

Agent 产生 Artifact。

Finding 由 deterministic materializer 创建为：

```text
candidate
```

verified 必须来自 Review。

## 19.4 Report

Agent Report 仍产生 Artifact。

Product Report Draft 基于 Artifact。

不要让 Report Agent 直接把 ReportDocument 标为 Published。

---

# 20. 前端组件迁移映射

| 当前 | 新目标 |
|---|---|
| `CaseWorkspaceView.vue` | `InvestigationShellView` + 子 views |
| `ChatThread` | Copilot 内继续复用 |
| `ChatInputBar` | Copilot 输入 |
| `EvidenceSidebar` | `InvestigationEvidenceView` |
| `VisualSidebar` | `InvestigationNetworkView` |
| `MonitoringPanel` | Overview Monitor Card / Signals |
| `MediaPanel` | Live Data -> Media |
| `AlignmentPanel` | Network -> Alignment mode |
| `IntegrityPanel` | Network -> Integrity mode |
| `DebatePanel` | Finding Challenge modal/drawer |
| `NarrativeTimelineView` | Investigation Timeline |
| `SemanticAnnotationsView` | Evidence -> Semantics |
| `GoalPlanningView` | Overview -> Plan |
| `ApprovalInboxView` | Admin |
| `ReviewWorkbenchView` | Admin |
| `MemoryGovernanceView` | Admin |
| `SecurityEventsView` | Admin |
| `ObservabilityView` | Admin |
| `ResilienceConsoleView` | Admin |

---

# 21. 前端 API 与类型管理

`frontend/src/services/api.ts` 当前已经很大。

本轮不要继续无限膨胀。

在不大规模重构旧 API 的前提下，新增模块：

```text
frontend/src/services/api/
  collections.ts
  findings.ts
  signals.ts
  reports.ts
  workspace.ts
```

保留现有：

```text
services/api.ts
```

作为兼容 facade：

```ts
export const api = {
  ...existing,
  ...collectionApi,
  ...findingApi,
  ...
}
```

或者新页面直接 import 模块。

不要在本轮把所有旧 API 全拆完。

---

# 22. Error Handling 要求

所有新功能必须符合现有 ApplicationError 风格。

必须区分：

```text
not_found
scope_mismatch
invalid_transition
version_conflict
validation_failed
```

禁止：

- catch all 后返回 200 `{ok:false}`，除非现有工具内部契约明确要求；
- 前端统一显示“操作失败”而不区分 version conflict。

至少对用户可恢复错误展示：

- Retry
- Reload
- Refresh latest version

---

# 23. 并发与版本要求

Collection Definition：

- immutable versions

Finding：

- status transition 通过 service
- Review 决策控制 verified/rejected

Report：

- optimistic `expected_version`

Monitor：

- 保持当前 version 行为

不得用“最后写 wins”覆盖重要调查产物。

---

# 24. 全局产品状态模型

建议 UI 状态颜色统一。

## Investigation

```text
idle
active
monitoring
completed
archived
```

若后端 Case 现有 status 不完全对应，前端先 derive。

本轮不要为 UI 强制重写 Case status。

## Finding

```text
candidate
under_review
verified
rejected
superseded
```

## Signal

```text
open
acknowledged
resolved
suppressed
```

## Report

```text
draft
in_review
published
archived
```

## Run

保留：

```text
pending
running
waiting_approval
completed
failed
cancelled
```

但默认 UI 用语义文本显示。

---

# 25. Semantic Activity 映射示例

必须避免默认 UI 出现大量底层术语。

例如：

```text
tool_execution_start collect_social_posts
```

显示：

```text
正在采集社交平台数据
```

```text
expert_dispatched opinion
```

显示：

```text
已委派观点分析专家
```

```text
expert_completed verification
```

显示：

```text
事实核查已完成
```

```text
approval_required collect_social_posts
```

显示：

```text
需要批准真实平台采集
```

技术详情：

```text
Tool: collect_social_posts
Run: ...
Cost: ...
Duration: ...
```

只在 Advanced Trace 中显示。

---

# 26. Investigation Overview 详细规格

Overview 是打开 Investigation 的默认页面。

至少包含：

## Header

- title
- topic
- platform chips
- time range
- Case ID secondary
- status

## Scope

- description
- active Collection Definition
- last collection time

## Investigation Plan

复用 Goals / Planning。

## Status

- active run
- pending approval
- latest findings
- open signals

## Recent Outputs

- latest opinion artifact
- latest verification artifact
- latest propagation artifact
- latest report

## Monitoring

- monitor enabled
- schedule
- last execution
- open signals

## Copilot

右侧 persistent。

---

# 27. Live Data 详细规格

子 tab：

```text
Posts
Platform Comparison
Media
```

Posts：

- platform
- author
- published time
- excerpt
- engagement
- source URL
- filters

不要把 Evidence 逻辑混在这里。

Live Data 表示：

> “系统收集到了什么”

Evidence 表示：

> “哪些数据被用于支持或反驳主张”

---

# 28. Evidence 详细规格

子 tab：

```text
Claims & Evidence
Semantics
```

选中 Claim：

- text
- status/verdict
- confidence
- supporting evidence
- contradicting evidence
- unassigned evidence suggestions（若已有）
- review result

选中 Evidence：

- source
- excerpt
- metadata
- related Claims
- related Findings
- related report citations

---

# 29. Network 详细规格

模式：

```text
Propagation
Alignment
Integrity
```

右侧 Detail panel 统一。

Propagation：

- graph
- observed/inferred filter
- confidence threshold
- origin candidates
- node roles

Alignment：

- identity/content/narrative alignment candidates

Integrity：

- coordination cluster
- bot/spam/coordinated behaviour signals

不要让三个模块分别发明不同 selection UX。

---

# 30. Timeline 详细规格

至少整合：

- post volume
- platform timeline
- narrative timeline

交互：

- brush time range
- click spike
- click narrative
- set Copilot UI Context

若已有 ECharts，可复用。

不要换图表库。

---

# 31. Findings 详细规格

列表默认按：

```text
status
confidence
updated_at
```

Filter：

- kind
- status
- confidence

Detail：

```text
Statement
Confidence
Evidence
Contradictions
Source Artifact
Review
Provenance
Challenge
Add to Report
```

---

# 32. Signals 详细规格

默认显示：

```text
open + acknowledged
```

Filter：

- severity
- type
- Investigation
- status

排序：

```text
critical first
then latest detected
```

Signal Detail：

- explanation / why it matters
- trigger metric
- evidence refs
- monitor/rule
- occurrence count
- Investigation link
- state actions

---

# 33. Reports 详细规格

Global：

- draft
- review
- published
- archived

Investigation：

- Agent Artifact history
- Report Document
- source Findings
- citations
- validation
- export

不要把“Artifact 版本”和“发布版本”混为一层 UI。

---

# 34. Administration 详细规格

Administration 面向系统运维/治理。

不得包含普通调查分析功能。

保留：

```text
Approvals
Review
Memory
Security
Observability
Resilience
```

其中：

Review 虽是业务审核，但全局队列适合作为 Admin/Operations 入口；Finding detail 同时提供 contextual review action。

---

# 35. 测试策略

本轮必须采用“现有测试不退化 + 新领域新增专项测试”。

## 35.1 Backend Unit

新增至少：

```text
test_collection_definitions.py
test_findings.py
test_provenance.py
test_signals.py
test_report_documents.py
test_ui_context.py
```

## 35.2 Backend API

覆盖：

- case scope
- invalid id
- cross-case access
- version conflict
- invalid transition
- publish citation gate

## 35.3 Harness Regression

必须继续运行：

- agent loop
- runtime
- durable runtime
- approval
- content security
- crawler cancel
- expert agents

M3 修改 crawl 后重点回归：

- approval
- sandbox
- cancellation

M2 修改 message 后重点回归：

- context builder
- run metadata
- follow-up

## 35.4 Frontend Unit

新增：

```text
GlobalSidebar.test.ts
InvestigationShellView.test.ts
CopilotDrawer.test.ts
InvestigationEvidenceView.test.ts
InvestigationFindingsView.test.ts
InvestigationNetworkView.test.ts
SignalsView.test.ts
InvestigationReportView.test.ts
```

## 35.5 E2E 最终场景

必须至少有一条完整 E2E：

### Scenario 1：调查创建到报告

1. 新建 Investigation；
2. 生成 Collection Definition；
3. 编辑并 Activate；
4. 发起分析；
5. 若真实模式，出现 Approval；
6. approve；
7. Agent Run 实时更新；
8. Evidence 页面出现数据；
9. Findings 自动产生；
10. Finding 提交 Review；
11. 接受；
12. Network 能查看 propagation；
13. 创建 Report Draft；
14. 加入 Finding；
15. Publish；
16. Export HTML。

### Scenario 2：Contextual Copilot

1. 打开 Network；
2. 选择 edge；
3. Copilot Context 显示 edge；
4. 发消息；
5. 后端 Run metadata 存在 ui_context；
6. ContextBuilder 注入；
7. Agent 仍通过 Tool 获取事实。

### Scenario 3：Signals

1. 创建/存在 Monitor Alert；
2. Global Signals 出现；
3. acknowledge；
4. 回到 Investigation；
5. Home count 更新。

---

# 36. 性能要求

本轮不做极限性能优化，但禁止明显退化。

## Home

不要：

```text
list cases
for each case:
  list runs
  list alerts
  list reports
```

必须用聚合 endpoint。

## Investigation

子页面按需加载。

不要 Shell 一次性加载：

- all evidence
- all propagation
- all media
- all monitoring
- all narratives
- all reports

## Copilot

SSE 只针对活跃 Run。

终态断订阅。

---

# 37. 安全要求

必须保持：

- Case scope
- Runtime-injected case_id
- Tool permission
- approval
- sandbox
- egress
- content security
- report redaction

新增对象：

- Collection
- Finding
- Report

所有 ID API 必须验证 `case_id` 或对象归属。

Provenance 不得通过任意 ID 读取其他 Case。

---

# 38. 数据兼容与 Backfill

## Collection

旧 Case 没有 Collection：

- UI 显示“尚未创建采集定义”
- collect tool 继续 fallback 当前逻辑

不要求一次性 backfill。

## Findings

旧 Artifact：

- 提供“同步历史分析结果”按钮
- `findings:sync`

不要求 migration 自动解析所有历史 JSON。

## Report

旧 Report Artifact：

- 仍可下载
- 用户可“创建可发布草稿”

不自动创建 ReportDocument。

---

# 39. 可回滚策略

每个 Milestone 应相对独立。

M1：

- 路由 redirect 可撤销

M2：

- 旧 CaseWorkspace 暂保留

M3：

- 无 active collection 时 fallback 原 crawl

M4：

- Finding 是新增层，不改变 Claim/Evidence

M5：

- 传播算法不变

M6：

- Signal 是 Alert adapter

M7：

- Artifact export 保留

这样任何阶段出现问题都不需要回滚核心 Agent 数据。

---

# 40. 执行智能体的代码修改纪律

## 必须

- 小步修改
- 运行测试
- 保持类型
- 保持异步风格
- 使用当前 Pydantic / SQLAlchemy / FastAPI 约定
- Vue 使用现有 Composition API
- ECharts 继续使用现有依赖
- Lucide 继续使用现有 icon
- 复用 CSS variables / style system

## 禁止

- 引入第二套后端框架
- 引入第二个状态管理框架
- 换掉 Vue
- 换掉 ECharts
- 换掉 LangGraph
- 删除 approval
- 删除 sandbox
- 关闭 typecheck
- `any` 大面积绕过 TypeScript
- 把全部 API response 改成无类型 dict
- 新增重复数据库实体替代已有 Claim/Evidence/Alert

---

# 41. 实施顺序的 Commit / PR 建议

若执行智能体能够提交多个 commit，按以下粒度：

```text
1. chore: capture baseline and add compatibility routes
2. feat: introduce investigation global shell
3. feat: split investigation shell and contextual copilot
4. feat: add versioned collection definitions
5. feat: add findings and provenance workspace
6. feat: promote network and timeline workspaces
7. feat: add global signals inbox
8. feat: add report document publishing workflow
9. refactor: reorganize administration and remove legacy workspace
10. test: complete end-to-end investigation workflows
```

不要一个 commit 修改 200 个文件且混合数据库、UI、Harness。

---

# 42. 每阶段 Definition of Done

每个阶段完成必须同时满足：

```text
[ ] 功能已实现
[ ] API schema 明确
[ ] case scope 验证
[ ] backend tests
[ ] frontend typecheck
[ ] frontend tests
[ ] build
[ ] 旧功能回归
[ ] error/empty/loading state
[ ] 无明显 dead code
```

---

# 43. 本轮最终 Definition of Done

本轮全部完成后，系统应满足以下可观察结果。

## 产品层

- 首页是 Operational Home；
- 用户创建的是 Investigation；
- Case 只是后端兼容名；
- Investigation 有稳定的一级工作区；
- Chat 不再是页面结构主体；
- Copilot 在所有工作区共享；
- Debate 不再是一级模式；
- 管理页与调查页分离。

## 数据采集

- 每个 Investigation 可拥有显式 Collection Definition；
- Collection 可生成、编辑、版本化、激活；
- Crawl 优先使用 Active Definition；
- Monitor 保存 Definition snapshot。

## Evidence

- Evidence 是独立工作区；
- Claim / Evidence 可双向查；
- Finding 成为稳定结论对象；
- Finding 可 Review；
- Finding 可追溯到 Evidence / Artifact。

## Network / Timeline

- Network 全尺寸；
- Edge 可查看 feature/evidence；
- Timeline 可选择时间上下文；
- Alignment / Integrity / Narrative 进入正确业务位置。

## Signals

- 用户有全局 Signal Inbox；
- Signal 基于现有 Alert；
- 支持 acknowledge / resolve / suppress；
- Home 展示关键 Signals。

## Report

- Agent Report Artifact 可转 Draft；
- Draft 可编辑；
- Publish 前验证 Evidence；
- Published 内容冻结；
- 可 Export HTML。

## Agent

- Durable Runtime 不变；
- Approval 不变；
- SSE 不变；
- Copilot 带 structured UI context；
- 默认 Activity 语义化；
- Advanced Trace 保留完整技术轨迹。

---

# 44. 本轮之后再考虑的下一阶段

以下内容明确不属于本轮验收。

## 多人协作

下一轮再实现：

- User
- Organization/Tenant
- RBAC
- Case Owner
- Assignee
- Reviewer
- Watcher
- Mention
- team notification

## 高级 Signal Source

未来 Signal 可以扩展：

- coordinated behaviour
- media reuse
- external intelligence
- cross-case anomaly

当前只做 Monitor Alert adapter。

## Public Distribution

未来：

- authenticated view-only URL
- public share token
- scheduled briefing
- email/Slack report delivery

## 更高级分析

未来：

- community detection
- narrative forecasting
- actor intelligence
- cross-case entity graph

不要在本轮提前实现。

---

# 45. 执行前最终核对清单

执行智能体开始改代码前，应再次确认：

```text
1. 当前 main 分支测试状态
2. 最新 Alembic migration 编号
3. Artifact 创建的统一路径
4. AgentService.start 当前参数
5. CreateMessageRequest 当前位置
6. ApplicationRepository 是否适合继续扩展
7. MonitorRepository 当前查询能力
8. ReviewService 决策后的扩展点
9. Frontend CaseWorkspace 当前测试覆盖
10. 旧 route 是否被 E2E 使用
```

如果其中某项与本文存在差异：

- 按当前代码结构适配；
- 保持目标不变；
- 不因文件名差异重做架构。

---

# 46. 最终实施原则

这轮优化成功的判断标准不是：

> “又新增了多少功能和 Agent。”

而是：

> **现有强大的 Agent / Evidence / Monitoring / Governance 能力是否被重新组织成一个用户能够稳定理解、持续使用、逐层深入的 Investigation Intelligence Workbench。**

代码层必须遵循：

```text
Agent 负责认知与分析
Service 负责产品状态和确定性业务规则
Repository 负责持久化
Evidence 负责事实基础
Review 负责人类最终判断
UI 负责呈现调查工作流
```

不要把 Product State 交给 Agent 自由生成。

不要把 Agent Trace 当成用户主工作流。

不要复制已有 Monitor、Evidence、Review、Artifact 能力。

本轮完成后，`Nothing-in-the-dark` 应从“一个技术能力非常丰富的 Harness Agent 项目”转变为：

> **一个以 Investigation 为中心、Evidence 为事实底座、Network/Timeline 为分析空间、Agent 为上下文认知与执行层、Human Review 为最终责任边界的 Social & Narrative Intelligence Workbench。**


---

# Part II — 当前仓库真实实现接线图

本部分把 V1 中的“应该在哪里实现”进一步固定到当前代码事实。若 `main` 在执行前已有后续提交，先确认职责是否仍一致。

## A. Agent 消息与 UI Context 的唯一正确入口

当前代码：

```text
backend/app/api/routes/cases.py
    create_agent_message()
        ↓
backend/app/application/agent_service.py
    AgentRunService.start()
        ↓
ApplicationRepository.add_turn()
ApplicationRepository.create_agent_run(metadata=...)
        ↓
GraphWorker claim/run
```

因此 M2 的 `ui_context` 必须按以下路径接入：

```text
CreateMessageRequest.ui_context
    ↓
cases.create_agent_message()
    ↓
AgentRunService.start(ui_context=...)
    ↓
run.metadata_json["ui_context"]
    ↓
ContextBuilder.build()
```

禁止：

- 前端把 context 拼进 `content`；
- 在 `GraphWorker` 中解析某种特殊用户文本协议；
- 把 `selected_id` 当证据正文直接注入；
- 由前端传 `case_id` 进入 ui_context 再信任该值。

`AgentRunService.start()` 当前 metadata 已保存 `approve_crawl` 和可选 `artifact_ref`，所以 `ui_context` 应作为同级字段加入，不新建第二套 Run context 表。

---

## B. Expert Artifact 与 Finding 自动物化的确定接线点

当前 Expert 生产路径：

```text
GraphWorker._execute()
    ↓ child expert completed
GraphWorker._finalize_expert_run(run, case, content)
    ↓
_parse_json_content()
    ↓
传播 / 事实核查的确定性持久化补充
    ↓
ApplicationRepository.create_artifact(...)
    ↓
expert_completed message/event
```

因此 Finding 自动物化必须接在：

```python
artifact = await self._repository.create_artifact(...)
```

成功之后、发送 `expert_completed` 之前或之后均可，但应在 `_finalize_expert_run()` 同一次业务流程中调用：

```python
if self._finding_service is not None:
    await self._finding_service.sync_from_artifact(artifact)
```

推荐顺序：

```text
1. persist artifact
2. deterministic finding materialization
3. emit expert_artifact_created / expert_completed
```

Finding materialization 失败策略：

- Artifact 已经是主要专家产物，不能因为 Finding 辅助层解析失败而丢失 Artifact；
- 记录 logger exception；
- 发一个可诊断事件 `finding_sync_failed`（若容易接入）；
- 后续可通过 `POST /cases/{case_id}/findings:sync` 重试；
- 不得删除 Artifact 或把 Expert Run 标 failed，仅因为 Finding materializer 失败。

禁止：

- 在 Expert Prompt 中要求模型直接调用 `create_finding`；
- 前端收到 Artifact 后自己解析 JSON 并 POST Findings；
- 在 `Report Agent` 输出中反向生成 Findings。

---

## C. Collection Definition 与 Crawl 的确定接线点

当前：

```text
backend/app/harness/tool_factory.py
build_tool_registry()
    async def crawl(arguments)
        request = CrawlInput...
        keywords = await generate_platform_keywords(...)
        ↓
        registry.run_external_tool("collect_social_posts", ... keywords ...)
        ↓
        SocialRepository.persist_batch(...)
```

M3 修改时不得更换 Tool。

实现应变为：

```python
keywords = None
collection_ref = None
if request.case_id and collection_service is not None:
    active = await collection_service.get_active(request.case_id)
    if active is not None:
        keywords = collection_service.keywords_for(
            active,
            requested_platforms=request.platforms,
            fallback_topic=request.topic,
        )
        collection_ref = {"id": active.id, "version": active.version}

if keywords is None:
    keywords = await generate_platform_keywords(
        llm, request.topic, request.platforms
    )
```

注意：

- `request.case_id` 仍由 Runtime scope 注入；
- Collection 只提供关键词/过滤定义，不负责实际 crawler 调用；
- Approval 和 Sandbox 的执行顺序完全不动；
- requested platforms 与 active definition platforms 取交集；如果请求中包含 active definition 未定义的平台，则该平台回退 `topic`，不要静默丢平台；
- Tool output 的 diagnostics 中增加 collection reference，不改变 `posts` 主结构。

建议给 `build_tool_registry()` 增加可选依赖：

```python
collection_service: CollectionDefinitionService | None = None
```

并在 bootstrap/container 统一注入。

不要在 Tool 内直接创建新的 SQLAlchemy Session 或绕过 service。

---

## D. Review 与 Finding 的确定接线点

当前 Review 域明确限制对象类型：

```text
backend/app/services/review.py
OBJECT_TYPES = (...)
```

当前不含 `finding`。

M4 必须：

```python
OBJECT_TYPES = (..., "finding")
```

Finding Review 状态不得另起一套“approve finding”端点绕过 `ReviewService`。

状态映射固定如下：

| Review Item status | Finding status |
|---|---|
| `unreviewed` | `candidate` 或保持 `candidate` |
| `in_review` | `under_review` |
| `needs_more_evidence` | `under_review` |
| `accepted` | `verified` |
| `rejected` | `rejected` |
| `superseded` | `superseded` |

### 原子性要求

当前 `ReviewService.decide()` 会通过 Repository 追加 ReviewDecision 并修改 ReviewItem 状态。Finding 状态同步必须避免：

```text
Review 已 accepted
Finding 仍 candidate
```

推荐实现：扩展当前 `ApplicationRepository.decide_review_item(...)` 的事务，在其已加载 `ReviewItemRecord` 后：

```text
if item.object_type == "finding":
    load FindingRecord by item.object_id
    verify finding.case_id == item.case_id
    map target review status -> finding.status
    update finding in the same SQLAlchemy session
commit once
```

如果当前 `decide_review_item` 结构不允许直接扩展，则新增 Repository 事务方法：

```python
decide_review_item_with_finding_sync(...)
```

由 `ReviewService.decide()` 在 `object_type == "finding"` 时使用。

禁止：

- Route 先 decide 再第二次 PATCH Finding；
- 前端在 Review 成功后自己 PATCH Finding；
- 接受 Finding 时直接跳过 ReviewItem。

`claim()` / `release()` 对 Finding 也应同步视觉状态，但不要求增加数据库 Finding assignee 字段；`in_review` 映射由 review 状态决定即可。

---

## E. Case 删除与所有新增表

当前 `ApplicationRepository.delete_case()` 是显式级联清理，而不是完全依赖 DB cascade。

因此每新增一个 Case-scoped 表都必须同步加入 `delete_case()`。

本轮新增：

```text
collection_definitions
findings
finding_evidence_links
finding_source_links
report_documents
```

删除顺序至少满足：

```text
finding_evidence_links
finding_source_links
findings
report_documents      # source_artifact FK 之前
collection_definitions
... existing case tables ...
artifacts
agent_runs/case
```

如果新表使用数据库 `ON DELETE CASCADE`，仍需核对 `delete_case()` 的显式 SQL 不会产生 FK 顺序错误。

必须新增/扩展 `test_case_deletion.py`，断言新增表没有 orphan。

---

## F. 数据模型的实现风格

当前模型约定：

- UUID 以 `String(36)` + `new_id()`；
- 时间使用 `DateTime(timezone=True)` + `utc_now()`；
- JSON/复杂文本按现有 `_Utf8JSON` / `JSON` 语义选择；
- 状态通常使用 `String(32)`，不是数据库原生 enum；
- PostgreSQL 与 SQLite 都需要工作。

本轮新增模型沿用同样风格，不引入数据库 native Enum。

对于包含中文结构化内容、未来可能全文查询的 `content_json`，优先复用 `_Utf8JSON`；简单配置 JSON 可使用 `JSON`。

---

## G. 前端实时 Run 逻辑的迁移点

当前 SSE/polling/approval/local live trace 主要集中在：

```text
frontend/src/views/CaseWorkspaceView.vue
```

M2 必须先提取到 composable，再拆页面。

不能反过来先创建 8 个新 Page，然后各自复制一份 SSE。

目标：

```text
useRunSubscriptions.ts
    owns subscriptions map
    owns cursor
    owns SSE fallback polling
    owns terminal finalize/trace replace
    exposes semantic state
```

Shell/Copilot/Activity 使用同一份状态。

---

## H. 前端测试/构建的当前真实命令

当前 `frontend/package.json` 已定义：

```bash
npm run typecheck
npm run lint
npm run test
npm run build
npm run e2e:smoke
npm run e2e:interact
```

阶段回归至少运行：

```bash
npm run typecheck
npm run test
npm run build
```

影响 UI 结构时再运行对应 E2E。

最终 M8 必须运行全部六项（若 e2e 的外部服务前置条件满足；不满足需记录环境限制，不得伪报通过）。

后端当前 pytest 配置在 `backend/pyproject.toml`，测试路径 `tests`，异步模式为 auto。

---

# Part III — 新增数据模型的确定规格

本部分覆盖 V1 中仍可能让执行智能体自行选择的字段和生命周期。

## 1. CollectionDefinitionRecord

模型放置：

```text
backend/app/infrastructure/database/models.py
```

建议精确字段：

```python
class CollectionDefinitionRecord(Base):
    __tablename__ = "collection_definitions"

    id: str                    # String(36), PK, new_id
    case_id: str               # FK cases.id, index, non-null
    version: int               # >=1, per case monotonically increasing
    status: str                # draft|active|superseded
    goal: str                  # Text
    platforms: list[str]       # JSON
    platform_queries: dict     # JSON
    exclusions: list[str]      # JSON
    filters: dict              # JSON
    generated_by_run_id: str?  # FK agent_runs.id, nullable
    created_at: datetime
    updated_at: datetime
```

约束：

```text
UNIQUE(case_id, version)
PARTIAL UNIQUE(case_id) WHERE status='active'
```

Alembic partial index 同时声明 PostgreSQL 与 SQLite where 条件。

### 生命周期

```text
create manually/generate -> draft
activate draft -> active
old active -> superseded
superseded 永不重新变 active；需要重新启用时 revise/create 新版本
```

禁止物理 DELETE API。

如果用户放弃 draft，可保留；列表默认最近版本优先。

### 版本生成

`version = max(case versions) + 1`。

由 unique 约束保护并发；发生冲突返回 `collection_version_conflict`，调用方可重试创建一次，禁止静默覆盖。

### 激活事务

同一 DB transaction：

```text
1. validate target.case_id
2. validate target.status == draft
3. existing active -> superseded
4. target -> active
5. commit
```

若 partial unique 冲突，返回 `collection_activation_conflict`。

---

## 2. FindingRecord

精确字段：

```python
class FindingRecord(Base):
    __tablename__ = "findings"

    id: str
    case_id: str
    kind: str
    title: str                # max 200
    statement: str            # Text
    status: str               # candidate|under_review|verified|rejected|superseded
    confidence: float | None  # 0..1, nullable when manual/unknown
    source_run_id: str | None # agent_runs.id
    created_at: datetime
    updated_at: datetime
```

索引：

```text
(case_id, status)
(case_id, kind)
source_run_id
```

不提供 DELETE API。

### Finding evidence link

```python
class FindingEvidenceLinkRecord(Base):
    __tablename__ = "finding_evidence_links"

    id: str
    finding_id: str
    evidence_ref: str
    relation: str       # supports|contradicts|context
    created_at: datetime
```

约束：

```text
UNIQUE(finding_id, evidence_ref, relation)
```

### Finding source link

```python
class FindingSourceLinkRecord(Base):
    __tablename__ = "finding_source_links"

    id: str
    finding_id: str
    source_type: str    # artifact|manual|propagation_edge|claim
    source_id: str
    source_path: str    # e.g. conclusions[0], cards[2], origin_candidates[0]
    created_at: datetime
```

约束：

```text
UNIQUE(source_type, source_id, source_path)
```

该唯一约束是 Artifact 重复 sync 的幂等键。

### Finding title 规则

自动 Finding：

- `title` 默认取 statement 前 80 字；
- Verification 可按 verdict 加前缀，但不得改变 statement；
- 不调用 LLM 再生成 title。

### Finding confidence

- Artifact 有合法 0..1：使用；
- 缺失或非法：`None`；
- 不进行自定义“校正”。

---

## 3. ReportDocumentRecord

为避免把“乐观锁版本”和“发布修订版本”混为一体，采用单表 + family/supersedes：

```python
class ReportDocumentRecord(Base):
    __tablename__ = "report_documents"

    id: str
    family_id: str             # 同一逻辑报告系列
    case_id: str
    source_artifact_id: str    # FK artifacts.id
    supersedes_id: str | None  # self FK
    status: str                # draft|in_review|published|archived
    title: str
    content_json: dict         # _Utf8JSON
    lock_version: int          # optimistic lock, starts 1
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
```

索引：

```text
case_id,status
family_id,created_at
source_artifact_id
```

### 生命周期

```text
from artifact -> draft(lock_version=1)
draft edit -> same row, lock_version += 1
draft -> in_review
in_review -> draft         # allowed when changes requested
in_review -> published
published -> archived
published -> revise => NEW draft row, same family_id, supersedes_id=published.id
archived -> no direct edit; revise => NEW draft
```

发布后的 row 内容冻结。

禁止：

- PATCH published row；
- DELETE report document；
- 复用 Artifact `version` 作为 ReportDocument `lock_version`。

### revise 语义

`POST /reports/{id}:revise`：

- 允许 published/archived/in_review/draft；
- 如果原本是 draft，通常应返回当前 draft 或明确要求调用普通 edit，不创建重复 draft；
- published/archived 创建新 draft；
- `source_artifact_id` 默认继承；
- `content_json` copy；
- `lock_version=1`。

---

## 4. Signal 不新增持久化模型

Signal ID 在本轮直接使用 `AlertOccurrenceRecord.id`。

```text
SignalResponse.id == alert.id
source_type == "monitor_alert"
source_id == alert.id
```

不要为适配 UI 新建 signals 表并复制 Alert 状态。

---

# Part IV — 状态机与权限语义

## 1. Finding 状态机

允许：

```text
candidate -> under_review
under_review -> verified
under_review -> rejected
under_review -> candidate          # review revoked/reopened as applicable
verified -> under_review           # reopen
rejected -> under_review           # reopen
candidate|under_review|verified|rejected -> superseded
```

UI 不提供直接 `candidate -> verified`。

`verified/rejected` 只能由 Review 决策产生。

---

## 2. Report 状态机

```text
draft -> in_review
in_review -> draft
in_review -> published
published -> archived
```

如果本轮没有要求报告必须人工 Review 才能 publish，则允许：

```text
draft -> published
```

但 Publish Gate 必须通过引用验证。

默认推荐 UI 流程仍为 `draft -> in_review -> published`。

---

## 3. Signal 状态机

完全复用 Alert：

```text
open -> acknowledged -> resolved
open|acknowledged|resolved -> suppressed
```

不要在 SignalService 再定义不同转移。

---

## 4. Collection 状态机

```text
draft -> active
active -> superseded      # 仅激活另一个版本时自动发生
```

不存在：

```text
active -> draft
superseded -> active
```

---

# Part V — 原子实施工作包

下面的任务编号是执行顺序。每一个工作包均应形成可独立验证的小提交或至少独立的 working-tree checkpoint。

---

# M0 — Baseline 与安全网

## M0.1 记录当前代码版本与测试基线

### 先读取

```text
backend/pyproject.toml
frontend/package.json
frontend/e2e-smoke.cjs
frontend/e2e-interact.cjs
backend/tests/
```

### 实现步骤

1. 记录当前 Git SHA。
2. Backend：运行 `pytest`；若时间/环境受限，先专项后全量，但最终应获得全量结果。
3. Backend：运行 `ruff check app tests`（若项目既有 CI 使用不同命令，以 CI 为准）。
4. Frontend：运行：

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

5. 可用时运行两个 e2e。
6. 若有既存失败，记录测试名称与错误，不修改业务以“制造绿色基线”。

### 产出

建议新增：

```text
docs/optimization-v2-baseline.md
```

若仓库不希望提交临时 baseline 文档，可以保存在实现日志/PR 描述，但执行智能体必须保留记录。

### DoD

- [ ] 明确 SHA
- [ ] Backend baseline
- [ ] Frontend baseline
- [ ] 已知失败与本轮新失败可区分

---

## M0.2 建立兼容性测试

### 修改/新增

```text
backend/tests/test_api.py 或新 test_legacy_compatibility.py
frontend router tests
```

### 必测

- `/cases/{id}` 现有行为在 M1 redirect 前有快照；
- `/cases/{id}/messages` 可创建 Run；
- `/runs/{id}/events`；
- report artifact download；
- monitor alert state action；
- review submit/decide。

### 禁止

不要在 M0 添加任何新产品功能。

---

# M1 — Investigation 产品语言与 Global Shell

## M1.1 建立新 Router 骨架并保留 legacy redirect

### 先读取

```text
frontend/src/router/index.ts
frontend/src/App.vue
```

### 修改

```text
frontend/src/router/index.ts
```

### 新增

```text
frontend/src/views/HomeView.vue
frontend/src/views/InvestigationsView.vue
frontend/src/views/SignalsView.vue
frontend/src/views/ReportsView.vue
frontend/src/views/admin/AdminShellView.vue
```

### 实现

精确路由：

```text
/                               home
/investigations                 investigations
/investigations/:caseId         redirect -> .../overview
/investigations/:caseId/overview
/signals
/reports
/admin/approvals
/admin/reviews
/admin/memories
/admin/security
/admin/observability
/admin/resilience
```

Legacy：

```text
/cases/:caseId -> /investigations/:caseId/overview
/approvals     -> /admin/approvals
/reviews       -> /admin/reviews
/memories      -> /admin/memories
/security      -> /admin/security
/observability -> /admin/observability
/resilience    -> /admin/resilience
```

此时 `overview` 可以临时渲染旧 CaseWorkspace 包装器；M2 再拆。

### 禁止

- 删除旧 View；
- 删除旧 API；
- 同时实现 M2 全部页面。

### 测试

- 每个 redirect；
- deep link refresh；
- caseId 保留。

---

## M1.2 抽 GlobalSidebar / Topbar

### 先读取

```text
frontend/src/App.vue
```

### 新增

```text
frontend/src/components/shell/GlobalSidebar.vue
frontend/src/components/shell/GlobalTopbar.vue
frontend/src/components/shell/InvestigationList.vue
```

### 迁移职责

`GlobalSidebar`：

- Home
- Signals
- Investigations
- Reports
- Administration

`InvestigationList`：

- projects
- ungrouped cases
- search
- open/delete/create hooks

`GlobalTopbar`：

- breadcrumb
- demo/real badge
- LLM config badge

`App.vue` 最终只承担：

- capability bootstrap；
- shell composition；
- global modal if still necessary；
- RouterView。

### UI 规格

左栏一级导航不能与 Investigation 列表混成同一视觉层级。

建议顺序：

```text
Brand
Primary nav
────────
Investigations / project tree
────────
Administration (collapsed)
Runtime footer
```

### DoD

- [ ] 原 Project CRUD 仍工作
- [ ] Case 删除仍工作
- [ ] active nav 正确
- [ ] App.vue 业务代码明显下降

---

## M1.3 统一产品文案

### 修改范围

搜索 frontend 中用户可见的：

```text
会话
新建会话
对话记录
```

改为调查语义。

注意：

- 对“AI 聊天消息”的实际“对话”一词可以保留；
- 只把作为顶层 Case 对象的“会话”改为“调查”。

不要修改后端类名 `CaseRecord`。

---

## M1.4 Home 从宣传页改 Operational Home v1

### 先读取

```text
frontend/src/views/CaseDashboardView.vue
```

### 实现

Home v1 只需要已有 API 可稳定提供的数据：

- Recent Investigations；
- New Investigation CTA；
- 系统状态；
- 可选 Pending Approval count（如果已有低成本 global API）。

暂不为了首页做 N+1。

原产品介绍可压缩到一个 secondary “About this workspace”，不再作为主页面。

---

# M2 — Investigation Shell、Contextual Copilot、Activity

## M2.1 提取 useRunSubscriptions，不改变行为

### 先读取

```text
frontend/src/views/CaseWorkspaceView.vue
frontend/src/services/api.ts
frontend/src/types/api.ts
```

### 新增

```text
frontend/src/composables/useRunSubscriptions.ts
frontend/src/services/activityFormatter.ts
```

### 必须搬迁的状态/函数

- subscriptions Map
- openEventStream
- ingestRunEvent
- applyEventState 中与 run live state 有关部分
- startPolling
- disconnectRun
- disconnectAll
- finalizeRun
- trace loading helper（可在 composable 或 activity service）

### 必须保留的行为

- `Last-Event-ID/cursor` 去重；
- SSE error polling；
- polling 2s（保持现有值，除非配置已抽取）；
- terminal load trace 覆盖 live data；
- 404 clean stop；
- expert dispatch 后 workspace data 可 refresh；
- approval queue 状态不闪退。

### 实施顺序

先让旧 `CaseWorkspaceView` 改为使用 composable，所有现有测试通过；**此工作包不创建新 Shell**。

这一步是 M2 最重要的风险隔离。

---

## M2.2 后端支持结构化 UiContext

### 先读取

```text
backend/app/schemas/runs.py
backend/app/api/routes/cases.py
backend/app/application/agent_service.py
backend/app/application/context_builder.py
backend/tests/test_context_builder.py
backend/tests/test_context_integration.py
```

### Schema

新增 `UiContext`，字段严格限制：

```text
workspace: enum
selected_type: str|null max 100
selected_id: str|null max 200
selected_label: str|null max 500
filters: dict default {}
time_range: {start?, end?}|null
```

`filters` 整体应有合理序列化大小限制；若 Pydantic 无直接字节限制，在 route/service 通过 JSON 序列化限制建议 16KB，超过返回 `ui_context_too_large`。

### AgentRunService

增加：

```python
ui_context: dict[str, object] | None = None
```

metadata：

```python
if ui_context:
    metadata["ui_context"] = ui_context
```

### ContextBuilder

新增纯 helper：

```python
_ui_context_block(run) -> str
```

输出必须包含固定警告：

```text
当前界面导航上下文（仅用于理解用户正在查看的对象，不构成事实证据）：
...
若需要该对象的事实内容，必须调用允许的工具查询，并仍遵守 Evidence ID 引用规则。
```

不要把该 block 放在“用户确认的关键约束”之上。

建议位置：Case header 后、review/constraints 前后均可，但必须是独立块。

### 测试

1. metadata persistence；
2. context block；
3. no ui context；
4. oversized rejected；
5. selected id 不会自动加载跨 case 对象；
6. artifact follow-up 仍工作。

---

## M2.3 建 InvestigationShell 和嵌套路由

### 新增

```text
frontend/src/views/investigation/InvestigationShellView.vue
frontend/src/components/investigation/InvestigationHeader.vue
frontend/src/components/investigation/InvestigationNav.vue
frontend/src/composables/useInvestigationContext.ts
```

### Shell 责任

只加载：

- Case/Investigation basic record；
- capabilities（可来自 global）；
- active runs/light run summary；
- shared UI context；
- Copilot Drawer state。

禁止 Shell 首次加载所有 Evidence/Network/Timeline/Media。

### Nav

固定：

```text
Overview | Live Data | Evidence | Network | Timeline | Findings | Report | Activity
```

窄屏可横向滚动，不隐藏重要 tab 到不可发现菜单。

---

## M2.4 建 Contextual Copilot Drawer

### 新增

```text
frontend/src/components/copilot/CopilotDrawer.vue
frontend/src/components/copilot/CopilotContextBadge.vue
```

### 复用

- `ChatThread`
- `ChatInputBar`
- 现有 message/run card rendering

但默认 Run card 要逐步语义化；M2.5 完成 Activity 后，Copilot 中的 raw trace 收起。

### UI Context 行为

页面进入时：

```ts
workspace='network'
```

选择对象：

```ts
selected_type='propagation_edge'
selected_id='...'
```

用户关闭详情：

```ts
selected_* = undefined
workspace 保留
```

### 发送

每次 send message snapshot 当前 ui context；Run 创建后 context 不随用户后续切 tab 改变。

### 禁止

不要把当前 Vue object 直接 JSON.stringify 全部发送；只发定义的 UiContext DTO。

---

## M2.5 Activity 语义层

### 新增

```text
frontend/src/views/investigation/InvestigationActivityView.vue
frontend/src/components/activity/SemanticActivityList.vue
frontend/src/components/activity/AdvancedTraceDrawer.vue
```

### SemanticActivity

建议类型：

```ts
interface SemanticActivity {
  id: string
  category: 'agent'|'collection'|'analysis'|'approval'|'review'|'system'
  title: string
  detail?: string
  status: 'pending'|'running'|'success'|'warning'|'error'
  createdAt: string
  runId?: string
  refType?: string
  refId?: string
}
```

### Formatter

未知 event：

- 不在默认列表爆出 JSON；
- Advanced Trace 可显示。

### Advanced Trace

复用 `/runs/{id}/trace`，显示：

- model calls
- tool calls
- approvals
- costs
- raw events

不要新建后端 trace API。

---

## M2.6 移除 Case 顶部 Debate 一级模式（过渡）

在新 Investigation Shell 不提供 Debate Tab。

旧 `CaseWorkspaceView` 暂可保留 slider，仅 legacy route 已 redirect 后用户主流程看不到。

真正删除组件入口等 M4 challenge 完成。

---

# M3 — Collection Definition

## M3.1 Migration + Model

### 先读取

```text
backend/app/infrastructure/database/models.py
backend/migrations/versions/
backend/app/application/repositories.py delete_case()
```

### 新增

- `CollectionDefinitionRecord`
- migration A
- delete_case cleanup

### Migration 必测

SQLite dev database：upgrade。

若有 PostgreSQL test env：upgrade + partial unique index。

### 禁止

不要修改旧 migration。

---

## M3.2 Repository

推荐新建：

```text
backend/app/infrastructure/database/collection_repository.py
```

原因：`ApplicationRepository` 已非常大；本轮新增复杂版本逻辑不应继续堆入 facade。

接口：

```python
create_draft(...)
list_for_case(case_id)
get(id)
get_active(case_id)
activate(case_id, id)
```

所有 `get` 被 API 使用前需提供 scoped 版本或由 service 验证。

---

## M3.3 CollectionDefinitionService

### 新增

```text
backend/app/application/collection_service.py
```

职责：

- version allocation；
- generate draft；
- revise；
- activate；
- platform keyword projection；
- validation。

不负责 crawler。

### 输入校验

- platforms 必须是 Case 支持平台子集；
- platform_queries key 必须属于 platforms；
- 每平台 query 去空/去重；
- 每 query 长度合理（建议 <=200）；
- exclusions 去空/去重；
- active definition goal 不可为空。

---

## M3.4 Generate endpoint

### 新增

```text
backend/app/schemas/collections.py
backend/app/api/routes/collections.py
```

### generate

输入可只接受：

```json
{"goal":"optional user clarification"}
```

Service 获取 Case topic/platforms。

调用现有 `generate_platform_keywords()`。

输出保存为 draft。

若 LLM 未配置/调用失败：

- 每个平台 `[case.topic]`；
- response diagnostics 可标 `generated_by="fallback"`；
- 不返回 500，除非数据库保存失败。

---

## M3.5 Revise + Activate API

严格按 V1 URI。

`revise` 创建新 draft version，不 PATCH 历史版本。

激活只接受 draft。

错误码明确：

```text
collection_not_found
collection_scope_mismatch
collection_not_draft
collection_version_conflict
collection_activation_conflict
```

---

## M3.6 Container/Bootstrap 注入

### 先读取

```text
backend/app/bootstrap.py
backend/app/harness/tool_factory.py
```

将 repository/service 作为 container singleton 或与当前 repo 生命周期一致的对象注入。

将 `collection_service` 传入 `build_tool_registry()`。

禁止 Tool 临时 new service/database。

---

## M3.7 Crawl 接入

按 Part II-C 实现。

新增测试必须 mock：

- active definition；
- no active fallback；
- active only covers subset platform；
- output diagnostics；
- sandbox still called；
- approval contract 未变化。

---

## M3.8 Monitor snapshot

### 现有

`MonitorCreateRequest.query_spec` 已能存结构化查询。

### 修改

前端创建 Monitor 时从 active collection 预填：

```json
{
  "collection_definition_id": "...",
  "collection_definition_version": 3,
  "platform_queries": {...},
  "exclusions": [...],
  "filters": {...}
}
```

后端创建 Monitor **不动态追踪 active collection**。

Monitor 执行后仍使用自己 snapshot。

### 测试

激活 Collection v4 后，旧 Monitor query_spec 仍是 v3。

---

## M3.9 Frontend Collection Editor

### 新增

```text
frontend/src/services/api/collections.ts
frontend/src/components/collection/CollectionDefinitionCard.vue
frontend/src/components/collection/CollectionDefinitionEditor.vue
frontend/src/components/collection/CollectionVersionList.vue
```

### 页面

Overview 中固定一个 Scope/Collection 区块。

### 编辑 UX

本轮只做：

- goal
- platform enable/read-only based Case
- queries as chips/list
- exclusions
- history

不实现复杂 Boolean query language builder。

### Active version

视觉必须明确：

```text
ACTIVE · v3
```

Draft 不应被误认为当前采集规则。

---

# M4 — Findings、Evidence、Provenance 与 Challenge

## M4.1 Migration B + models + delete cascade

实现 Part III Finding 三张表。

必须更新：

```text
ApplicationRepository.delete_case()
```

并扩展 `test_case_deletion.py`。

---

## M4.2 FindingRepository

新建：

```text
backend/app/infrastructure/database/finding_repository.py
```

接口：

```python
create(...)
get(...)
get_for_case(case_id,id)
list(case_id, kind?, status?, limit?)
update_status(...)
add_evidence_link(...)
remove_evidence_link(...)
list_evidence_links(...)
get_source_link(...)
create_source_link(...)
```

不要把 Finding 数据访问放 KnowledgeRepository。

---

## M4.3 ArtifactFindingMaterializer

建议服务：

```text
backend/app/application/finding_service.py
```

其中：

```python
sync_from_artifact(artifact)
sync_case_history(case_id)
```

### Opinion mapping

仅解析：

```text
data.conclusions[]
```

每项必须至少有非空 `claim`。

source_path：

```text
conclusions[0]
```

Evidence relation：

```text
evidence_ids -> supports
```

### Verification mapping

解析：

```text
data.cards[]
```

Finding.statement = card.claim。

kind=`verification`。

supporting -> supports；contradicting -> contradicts。

verdict 不直接决定 Finding verified 状态；始终 candidate。

把 verdict 可放 Finding metadata 吗？当前模型未定义 metadata。为保持模型简单，本轮在 `title` 或 response DTO `source_summary` 中从 Artifact 动态读取，不额外添加 JSON metadata；如果 UI 确实必须展示 verdict，可在 FindingRecord 增加 `attributes_json`，但需要在 M4.1 一次确定，禁止后续随意塞字段。

**推荐 V2 固定增加：**

```text
attributes_json: JSON default {}
```

存：

```json
{"verdict":"supported"}
```

Opinion 也可存统计 category，但只存直接来源字段，不复制整份 Artifact。

### Idempotency

先查 `finding_source_links` 的 unique key。

若存在：跳过，不修改用户已 Review 的 Finding。

不要因为 Artifact 被重复 sync 而重置 Finding。

---

## M4.4 GraphWorker 自动 materialize

按 Part II-B 精确接线。

依赖注入：

- `GraphWorker.__init__` 增加可选 finding service；
- bootstrap 注入；
- tests 构造 GraphWorker 时允许 None，保持旧单测兼容。

不要在 `_finalize_expert_run()` 内直接写解析规则。

---

## M4.5 Historical sync API

```http
POST /cases/{case_id}/findings:sync
```

行为：

1. list artifacts case；
2. 只处理支持的 kinds；
3. idempotent；
4. 返回：

```json
{"created":5,"skipped":12,"unsupported":3,"errors":[]}
```

单个 malformed Artifact 不应中断全部历史同步。

---

## M4.6 Review integration

按 Part II-D。

必须修改：

```text
backend/app/services/review.py OBJECT_TYPES
ReviewService/Repository transaction path
```

新增专项测试：

- finding accepted -> verified；
- rejected -> rejected；
- more_evidence -> under_review；
- cross-case review object rejected；
- review version conflict 不改变 finding。

---

## M4.7 ProvenanceService

### 新增

```text
backend/app/application/provenance_service.py
backend/app/schemas/provenance.py
backend/app/api/routes/provenance.py
```

### 原则

不用图数据库。

用当前 relational links 聚合。

### object_type 支持

第一版固定：

```text
claim
evidence
finding
artifact
propagation_edge
```

未知类型返回 `provenance_object_type_unknown`，不要返回空图假装成功。

### Resolve 规则

Finding：直接 source/evidence links。

Artifact：Findings source link 反向查询。

Evidence：

- Claims existing evidence relation；
- Finding evidence links；
- Report citations（M7 完成后补充）。

Propagation edge：已有 edge evidence IDs + Finding source links。

### Cross-case

每种 resolver 最终必须证明对象属于 route `case_id`。

---

## M4.8 Evidence full workspace

### 先读取

```text
frontend/src/components/evidence/EvidenceSidebar.vue
frontend/src/types/api.ts EvidenceSummary
```

### 迁移策略

先抽：

```text
EvidenceExplorer.vue
```

让 Sidebar 与新 Page 暂时都可复用，然后主路由使用 full workspace；M8 删除 sidebar wrapper。

### 页面布局（桌面）

```text
┌──────── 280-320px ────────┬──────────── fluid ────────────┐
│ Claims / filters          │ Selected claim/evidence       │
│ Unassigned                │ Support / Contradict / Context│
│                           │ Source metadata               │
│                           │ Related findings/provenance   │
└───────────────────────────┴────────────────────────────────┘
```

### 默认状态

- Claims 有数据：选第一条；
- 无 Claims 但有 unassigned：展示 Evidence collection intro，不强制选第一条；
- 完全无 evidence：显示“尚未采集或核查”，提供 Start Analysis / Live Data 链接。

### 交互

Evidence ID、Finding ID 可点击导航/选中。

外部 source URL 以安全新窗口打开（沿用现有规范）。

---

## M4.9 Findings workspace

### 新增

```text
frontend/src/services/api/findings.ts
frontend/src/views/investigation/InvestigationFindingsView.vue
frontend/src/components/findings/FindingList.vue
frontend/src/components/findings/FindingDetail.vue
frontend/src/components/findings/FindingStatusBadge.vue
```

### 桌面布局

```text
280-340px list | flexible detail | Copilot global drawer
```

### Detail 顺序

```text
Statement
status/confidence/kind
source artifact
Evidence
Contradictions
Review
Provenance
Actions
```

### 状态动作

candidate：`提交审核`
under_review：展示审核中
verified/rejected：展示人工结论

不提供“验证”快捷按钮。

---

## M4.10 Finding Challenge

复用现有 Debate API/Panel。

### UI

Finding actions：

```text
挑战此结论
```

点击：

- 打开确认/说明 Modal；
- create Debate；
- add first message；
- 打开 `FindingChallengeDrawer` 或复用 DebatePanel embedded container。

### 禁止

- 恢复全 Case Debate tab；
- 自动把 Debate 结果修改 Finding status；
- 自动把 Debate 投票当 Human Review。

如果 Debate 暴露了有价值反例，应由用户手动添加 evidence 或提交新的 review。

---

# M5 — Network / Timeline / Live Data 工作区

## M5.1 Network 页面骨架

### 先读取

```text
VisualSidebar.vue
AlignmentPanel.vue
IntegrityPanel.vue
propagation API/types
```

### 新增

```text
InvestigationNetworkView.vue
NetworkGraphCanvas.vue
NetworkObjectDetail.vue
NetworkToolbar.vue
```

### 不换图表库

继续使用 ECharts。

### 布局

Toolbar 44–52px；Graph 主区；Detail 360–420px。

无 selection 时 Detail 显示：

- node count
- edge count
- observed/inferred count
- origin candidate summary
- limitations/algorithm version

---

## M5.2 Propagation mode

默认 mode=`propagation`。

Toolbar：

- observed/inferred toggle；
- min confidence；
- fit graph；
- reset selection。

选 Edge detail 必须：

```text
relation
confidence
feature_scores
reasons
evidence_ids
algorithm_version
review/human state if exists
```

Evidence ID 点击：

```text
/investigations/:caseId/evidence?evidence=<id>
```

并由 Evidence view 读取 query/select。

---

## M5.3 Alignment / Integrity mode

将现有两个 Panel 的数据逻辑提取为 page sections，保持后端 API。

三种 mode 共用：

- Toolbar shell；
- selection store；
- detail panel；
- Copilot context bridge。

不能做三个完全不同的页面交互模型。

---

## M5.4 Edge Promote to Finding

按钮：

```text
提升为调查结论
```

后端使用 Finding create manual endpoint：

```text
kind=propagation
source_type=propagation_edge
source_id=edge.id
```

statement 建议由确定性模板：

```text
“{source label} → {target label} 存在 {relation} 传播关系候选（置信度 {confidence}）”
```

用户可在创建前编辑 statement。

不调用 LLM 自动改写。

---

## M5.5 Timeline 页面

### 先读取

```text
NarrativeTimelineView.vue
platform comparison/time series components
```

### 新结构

```text
InvestigationTimelineView
  TimelineToolbar
  VolumeTimeline
  PlatformTimeline
  NarrativeTimelineSection
  TimelineDetail
```

### selection

用户 brush 时间区间时更新：

```ts
ui_context.time_range
```

点击 narrative 时：

```ts
selected_type='narrative'
selected_id=...
```

### 禁止

不要复制 Narrative Timeline 数据获取逻辑到第二份永久组件；抽 shared component/service。

---

## M5.6 Live Data 页面

子 tab：

```text
Posts | Platform Comparison | Media
```

### Posts

如果当前没有统一 list posts API，优先复用现有 social/knowledge endpoint；只有确实缺失时新增轻量 Case posts endpoint。

不要通过 Evidence Summary 反向当作完整 Raw Post 列表，因为 Evidence 是语义层。

### Media

复用 MediaPanel 内容，变全尺寸 section。

---

## M5.7 Semantics 与 Goals 迁移

- `SemanticAnnotationsView` 内容 → Evidence 子 tab；
- `GoalPlanningView` 内容 → Overview Plan section；
- 旧路由先 redirect 到对应 Investigation 页（若无 case context 的 global旧页无法确定 case，则保留 legacy 直到 M8 或显示选择 Investigation）。

---

# M6 — Global Signals 与 Operational Home

## M6.1 MonitorRepository global signal query

### 先读取

```text
backend/app/infrastructure/database/monitor_repository.py
backend/app/infrastructure/database/models.py Alert/Rule/Monitor
```

### 新增 query 方法

不要：

```python
for case in cases:
    list_alerts(case)
```

实现一个 join query，返回至少：

```text
alert
rule_type
rule.severity
monitor.name
monitor.case_id
case.title
```

建议内部 dataclass：

```python
SignalRow(...)
```

支持：status, severity, case_id, rule_type, limit。

---

## M6.2 SignalService / API

实现 V1 DTO。

### title 模板

不要调用 LLM。

例如：

```text
volume_spike: “讨论量达到告警阈值”
growth_spike: “讨论增长速度异常”
anomaly: “检测到异常活动”
key_actor: “重点账号触发监测规则”
narrative_shift: “检测到叙事变化”
```

`why_it_matters = alert.explanation`，若空则使用规则+metric deterministic fallback。

### status action

调用现有 monitor repository 的状态转移逻辑或抽公共 domain function。

不要直接 `alert.status = ...` 绕过合法转移。

---

## M6.3 Signals 页面

### 新增

```text
frontend/src/services/api/signals.ts
frontend/src/views/SignalsView.vue
frontend/src/components/signals/SignalList.vue
frontend/src/components/signals/SignalDetail.vue
```

### 布局

```text
filter rail 220-260 | feed 360-440 | detail fluid
```

移动端可折叠，但桌面三列优先。

### 默认过滤

```text
status=open,acknowledged
```

### 排序

服务端返回：

```text
critical > warning > info
then detected_at desc
```

不要只在前端排序分页结果。

---

## M6.4 Investigation Overview Monitoring card

显示：

- monitor name
- enabled
- schedule
- last run
- open signal count
- manage button

Manage 打开现有 Monitoring 编辑 UI 的 full-width drawer/modal。

不要恢复顶部 Monitoring Panel。

---

## M6.5 WorkspaceOverviewService

### 新增

```text
backend/app/application/workspace_service.py
backend/app/schemas/workspace.py
backend/app/api/routes/workspace.py
```

### 数据访问

用 count/select limit 查询。

不要 route 调 route。

### response 固定字段

```json
{
  "counts": {
    "investigations": 0,
    "open_signals": 0,
    "pending_approvals": 0,
    "running_runs": 0
  },
  "recent_investigations": [],
  "top_signals": [],
  "recent_reports": []
}
```

M6 `recent_reports` 可以来自 `report` Artifact；M7 改成 ReportDocument，但 DTO 保持。

---

## M6.6 Home v2

布局建议：

```text
Header + New Investigation
KPI row
Open/critical signals
Active investigations
Running/approval attention
Recent reports
```

禁止：

- 技术栈 chips 作为首页主要内容；
- 默认展示 raw model token metrics。

---

# M7 — Report Product Layer

## M7.1 Migration C + ReportDocumentRecord

实现 Part III schema。

必须先于 Artifact/Case 删除，因此更新 delete_case。

新增 `test_case_deletion` coverage。

---

## M7.2 ReportDocumentRepository

新建：

```text
backend/app/infrastructure/database/report_repository.py
```

接口：

```python
create_from_artifact(...)
get(...)
get_for_case(...)
list_global(...)
list_for_case(...)
update_draft(expected_lock_version,...)
change_status(...)
create_revision(...)
```

乐观锁 update 采用 SQL：

```text
WHERE id=:id AND lock_version=:expected
SET ..., lock_version=lock_version+1
```

受影响行数 0 -> `report_version_conflict`。

---

## M7.3 ReportDocumentService

### import Artifact

验证：

- kind == report；
- artifact.case_id == route case；
- data structure 有基础字段；
- 同 artifact 已存在 draft/document 时幂等返回最近对应 document，而不是无限创建。

建议 unique：

不对 source_artifact 单独 unique，因为同 artifact 可产生 revision family；Service 保证首次 import 幂等即可。

### Edit

只允许 draft/in_review（in_review 编辑时先回 draft 或接口明确 transition）。

### Publish Gate

固定验证：

1. `citation_links` 可解析；
2. 每个 ref 在当前 case 可 resolve；
3. 不允许跨 case；
4. content 安全渲染；
5. title 非空；
6. 至少 executive_summary 或 section 非空。

失败返回：

```json
{
  "code":"report_publish_validation_failed",
  "details":[...]
}
```

不要只返回字符串。

---

## M7.4 Citation Resolver

不要重新写与 Provenance/Evidence 无关的第三套 resolver。

在 `ProvenanceService` 或 shared `EvidenceReferenceResolver` 中增加：

```python
resolve(case_id, ref) -> ResolvedReference | None
```

Report Publish 与 Provenance 共用。

支持至少当前 Report Agent 实际输出的 Evidence ID 形式。

---

## M7.5 Report download

复用 `render_html_report()`。

新增 service adapter：

```python
render_report_document_html(document)
```

内部仍调用相同 renderer。

不要复制 HTML 模板形成两份。

原 Artifact download 保留。

---

## M7.6 Global Reports / Investigation Report UI

### 新增

```text
frontend/src/services/api/reports.ts
frontend/src/views/ReportsView.vue
frontend/src/views/investigation/InvestigationReportView.vue
frontend/src/components/reports/ReportEditor.vue
frontend/src/components/reports/ReportStatusBadge.vue
frontend/src/components/reports/ReportCitationPanel.vue
```

### Editor

不引入富文本依赖。

编辑：

- title
- executive summary
- section title/content
- citations
- disclaimer

Section reorder 使用简单 up/down 或现有 drag dependency（若无 dependency 则不用 drag）。

---

## M7.7 Finding 加入 Report

### 数据表示

Report `content_json` 可增加内部字段：

```json
{
  "source_finding_ids": ["..."]
}
```

Renderer 忽略此字段。

### 去重

同 Finding 已存在则不重复 section。

### section 内容

初版 deterministic：

- section title = Finding title；
- content = Finding statement；
- citation_links 加其 supports/context evidence；
- contradicted evidence 不自动当支持引用，但可在 section 中单独列“反驳证据”字段（若结构支持）。

---

# M8 — Legacy 清理、Administration、最终闭环

## M8.1 Administration routes 与 sidebar 最终化

确认只有：

```text
Approvals
Review
Memory
Security
Observability
Resilience
```

普通调查能力不得在 Administration。

---

## M8.2 旧组件迁移检查

按 Part VII 矩阵逐项核对。

只有满足 delete condition 才删除。

---

## M8.3 删除/降级 CaseWorkspaceView

如果新 Investigation Shell 已覆盖全部主要能力：

- 删除 route usage；
- 可删除文件；
- 若 legacy tests 直接 import，则先迁移 tests。

不能保留两套独立 Run subscription 实现。

---

## M8.4 前端 API 模块化收尾

新增 API 已放模块。

不要求本轮把旧 `services/api.ts` 全拆完。

但禁止新增功能继续往其顶部无限堆类型 import。

---

## M8.5 全量验证

Backend：

```bash
pytest
ruff check app tests
# 若项目已有 mypy CI：mypy app
```

Frontend：

```bash
npm run typecheck
npm run lint
npm run test
npm run build
npm run e2e:smoke
npm run e2e:interact
```

另外运行本轮新增 E2E。

---

# Part VI — API 精确契约与错误码

## 1. UiContext message

Request 示例：

```json
{
  "content": "为什么这条边被判断为 inferred？",
  "approve_crawl": false,
  "ui_context": {
    "workspace": "network",
    "selected_type": "propagation_edge",
    "selected_id": "edge-id",
    "selected_label": "A → B",
    "filters": {"relation": "inferred", "min_confidence": 0.6},
    "time_range": null
  }
}
```

旧 client 不传 `ui_context` 仍合法。

---

## 2. Collection Definition

### create manual draft

```http
POST /cases/{case_id}/collection-definitions
```

```json
{
  "goal": "研究某事件跨平台传播",
  "platforms": ["weibo", "zhihu"],
  "platform_queries": {
    "weibo": ["关键词A", "关键词B"],
    "zhihu": ["关键词A"]
  },
  "exclusions": ["广告"],
  "filters": {}
}
```

### response

```json
{
  "id": "...",
  "case_id": "...",
  "version": 2,
  "status": "draft",
  "goal": "...",
  "platforms": [],
  "platform_queries": {},
  "exclusions": [],
  "filters": {},
  "generated_by_run_id": null,
  "created_at": "...",
  "updated_at": "..."
}
```

---

## 3. Findings

List：

```http
GET /cases/{case_id}/findings?status=candidate&kind=verification&limit=100
```

Detail response 至少：

```json
{
  "id":"...",
  "case_id":"...",
  "kind":"verification",
  "title":"...",
  "statement":"...",
  "status":"candidate",
  "confidence":0.84,
  "attributes":{"verdict":"supported"},
  "source_run_id":"...",
  "sources":[],
  "evidence_links":[],
  "review":null,
  "created_at":"...",
  "updated_at":"..."
}
```

不要让前端为了一张 Finding detail 连续发 5 个请求；detail endpoint 可聚合 links + lightweight review summary。

---

## 4. Provenance

```http
GET /cases/{case_id}/provenance/finding/{finding_id}
```

固定：

```json
{
  "root": {
    "type":"finding",
    "id":"...",
    "label":"..."
  },
  "upstream": [
    {
      "type":"evidence",
      "id":"...",
      "relation":"supports",
      "label":"..."
    }
  ],
  "downstream": [],
  "warnings": []
}
```

本轮不要求任意深度递归 graph；只返回 root 的一跳上下游。

前端可点击继续加载另一个 root。

---

## 5. Signal

```http
GET /signals?status=open&status=acknowledged&severity=critical&limit=100
```

若 FastAPI query multi-value 实现麻烦，可以使用逗号或单 status；但应固定一种形式并在 frontend service 封装。不要在 route 接收任意复杂 DSL。

---

## 6. Report

### Import

```http
POST /cases/{case_id}/reports:from-artifact
```

```json
{"artifact_id":"..."}
```

### Edit

```http
PATCH /reports/{report_id}
```

```json
{
  "expected_lock_version": 3,
  "title":"...",
  "content": {...}
}
```

### Publish

```http
POST /reports/{report_id}:publish
```

Response 返回完整 ReportDocument。

---

## 7. 标准新增错误码

新增功能至少使用：

```text
ui_context_too_large
collection_not_found
collection_scope_mismatch
collection_not_draft
collection_version_conflict
collection_activation_conflict
finding_not_found
finding_scope_mismatch
finding_invalid_transition
finding_evidence_invalid
provenance_object_type_unknown
provenance_object_not_found
signal_not_found
report_not_found
report_scope_mismatch
report_invalid_transition
report_version_conflict
report_publish_validation_failed
```

HTTP mapping 遵循当前项目 ApplicationError handler；不要为新模块建立第二套 error envelope。

---

# Part VII — 页面级 UX 实施规格

## 1. Global Home

### 用户进入首页 5 秒内必须能回答

```text
有什么新问题？
哪些调查正在进行？
有什么需要我批准/审核？
最近产生了什么结果？
```

### 结构

```text
Top: page title + 新建调查
KPI: Open Signals | Active Investigations | Pending Approvals | Running Agents
Main left: Critical/Open Signals
Main right: Active/Recent Investigations
Bottom: Recent Reports
```

### 空状态

零数据时显示行动建议，而非一组值为 0 的空图：

```text
尚无调查 → 新建调查
尚无 Signal → 创建调查后配置持续监测
```

---

## 2. Investigation Overview

页面不是聊天欢迎卡。

### 第一屏

```text
Investigation identity
Scope + active Collection
Current status/attention
Investigation Plan
```

### 第二屏

```text
Latest Findings
Latest Outputs
Monitoring
Recent Activity
```

任何 “Start/Continue Analysis” 按钮打开 Copilot，并预填任务，不另起一套 analysis endpoint。

---

## 3. Copilot Drawer

### 尺寸

桌面建议 380–480px，可 resize 不是本轮要求。

关闭后只保留 40–48px launcher，不应改变主页面数据。

### Header

```text
Copilot
Context: Network · Edge A→B
[clear selection]
```

### Chat

- 用户/Assistant message；
- Run semantic status；
- Artifact quick link；
- approval inline action 可保留；
- Advanced trace link。

默认不显示大段 Tool JSON。

---

## 4. Evidence

必须支持从 query param deep link：

```text
?claim=<id>
?evidence=<id>
```

这样 Network/Report/Provenance 可以回跳。

选择不存在 ID：

- 显示轻量提示；
- 清 invalid selection；
- 页面本身仍可用。

---

## 5. Network

Graph 是主画布，不是卡片中的小图。

### 视觉编码

- observed/inferred 必须有明显不同的线型/图例；
- confidence 可通过粗细/透明度表达，但必须有数值 detail；
- 不用颜色作为唯一信息编码，需兼顾可访问性；
- origin candidates 不能看起来等同“已证实源头”。

显示明确文案：

```text
候选源头，不代表已证明首发者
```

---

## 6. Timeline

图表上必须可区分：

- volume；
- platform；
- narrative。

不要把三组序列全叠在一个无法阅读的 chart。

使用独立 sections 或切换 mode。

---

## 7. Findings

verified 与 candidate 视觉差异必须明显。

但 `confidence=0.95` 的 candidate 仍然不能看起来像 human verified。

状态优先于置信度表达。

---

## 8. Signals

Critical Signal detail 应在不用进入 Case 的情况下显示：

- why it matters；
- trigger metric；
- supporting refs；
- affected Investigation；
- first/last seen；
- count；
- state action。

点击 Investigation 再进入深入分析。

---

## 9. Report

发布状态视觉：

```text
DRAFT
IN REVIEW
PUBLISHED
ARCHIVED
```

Published 页默认只读。

点击“修改”调用 revise，不能直接切 editable。

---

# Part VIII — 旧实现迁移 / Redirect / 删除矩阵

| 旧对象 | 新对象 | 过渡策略 | 允许删除条件 |
|---|---|---|---|
| `/cases/:caseId` | `/investigations/:caseId/overview` | Router redirect | 至少保留本轮，建议长期兼容 |
| `CaseDashboardView.vue` | `HomeView.vue` + `InvestigationsView.vue` | M1 替换首页 | Home/Investigations tests 完成 |
| App 内“对话”树 | InvestigationList | 抽 component | Project/Case CRUD tests 通过 |
| `CaseWorkspaceView.vue` | InvestigationShell + 8 child views | legacy wrapper → 无 route usage | M8 全部 child view + Copilot + SSE E2E 通过 |
| `EvidenceSidebar` | EvidenceExplorer / Evidence View | 抽 shared explorer | 新 Evidence page 全覆盖 |
| `VisualSidebar` | Network View | 抽 graph canvas | Network modes 覆盖 |
| `MonitoringPanel` | Overview Monitoring + Global Signals | 复用 editor | Signals/monitor manage 完成 |
| `MediaPanel` | Live Data / Media | 页面化 | Media page section 完成 |
| `AlignmentPanel` | Network Alignment mode | 提取 data/detail | Alignment mode tests |
| `IntegrityPanel` | Network Integrity mode | 提取 data/detail | Integrity mode tests |
| Case `对话|辩论` slider | Finding Challenge | 不在新 Shell 显示 | Finding Challenge 完成后移除旧入口 |
| `NarrativeTimelineView` global | Investigation Timeline | 重用 components | Timeline route 完成 |
| `SemanticAnnotationsView` global | Evidence/Semantics | 重用 components | case scoped navigation 完成 |
| `GoalPlanningView` global | Overview/Plan | 重用 components | plan section 完成 |
| `/approvals` | `/admin/approvals` | redirect | 可长期保留 redirect |
| `/reviews` | `/admin/reviews` | redirect | 可长期保留 redirect |
| `/memories` | `/admin/memories` | redirect | 可长期保留 redirect |
| Artifact report download | ReportDocument download | 两者并存 | 本轮不删除 Artifact download |
| Monitor Alert | Signal adapter | 同一状态数据 | 永不删除 Alert，仅新增产品适配层 |

### 特别要求：Subscriptions

当前 `SubscriptionsView` 的实际能力在迁移前先阅读对应 API/数据模型。

按其“订阅什么”归类：

- Signal/monitor delivery → Signals；
- report delivery → Reports；
- 通用通知 endpoint → 可保留独立 Delivery/Notifications 管理入口。

不得在没有阅读实现前简单删除。

---

# Part IX — 文件级修改清单

这是预期清单，不要求每个文件都必须存在；若职责在当前 main 已移动则适配。

## Backend 新增

```text
app/schemas/collections.py
app/schemas/findings.py
app/schemas/provenance.py
app/schemas/signals.py
app/schemas/report_documents.py
app/schemas/workspace.py

app/application/collection_service.py
app/application/finding_service.py
app/application/provenance_service.py
app/application/signal_service.py
app/application/report_document_service.py
app/application/workspace_service.py

app/infrastructure/database/collection_repository.py
app/infrastructure/database/finding_repository.py
app/infrastructure/database/report_repository.py

app/api/routes/collections.py
app/api/routes/findings.py
app/api/routes/provenance.py
app/api/routes/signals.py
app/api/routes/reports.py
app/api/routes/workspace.py
```

## Backend 修改

```text
app/infrastructure/database/models.py
app/application/repositories.py                 # delete_case + aggregation if needed
app/application/agent_service.py                # ui_context
app/application/context_builder.py              # ui context block
app/application/graph_worker.py                 # finding sync injection
app/application/review_service.py               # finding review transaction path
app/services/review.py                          # OBJECT_TYPES
app/harness/tool_factory.py                     # active collection keywords
app/bootstrap.py                                # DI
app/api/router.py                               # new routers
app/api/routes/cases.py                         # ui_context pass-through
app/services/reports.py                         # reuse renderer, minimal adapter
```

## Frontend 新增

```text
src/views/HomeView.vue
src/views/InvestigationsView.vue
src/views/SignalsView.vue
src/views/ReportsView.vue
src/views/admin/AdminShellView.vue

src/views/investigation/InvestigationShellView.vue
src/views/investigation/InvestigationOverviewView.vue
src/views/investigation/InvestigationLiveDataView.vue
src/views/investigation/InvestigationEvidenceView.vue
src/views/investigation/InvestigationNetworkView.vue
src/views/investigation/InvestigationTimelineView.vue
src/views/investigation/InvestigationFindingsView.vue
src/views/investigation/InvestigationReportView.vue
src/views/investigation/InvestigationActivityView.vue

src/components/shell/*
src/components/investigation/*
src/components/copilot/*
src/components/activity/*
src/components/collection/*
src/components/findings/*
src/components/signals/*
src/components/reports/*

src/composables/useRunSubscriptions.ts
src/composables/useInvestigationContext.ts

src/services/api/collections.ts
src/services/api/findings.ts
src/services/api/signals.ts
src/services/api/reports.ts
src/services/api/workspace.ts
src/services/activityFormatter.ts
```

## Frontend 重点修改

```text
src/App.vue
src/router/index.ts
src/services/api.ts
src/types/api.ts
src/views/CaseWorkspaceView.vue     # 先迁移后删除
src/components/CaseComposer.vue
```

---

# Part X — 执行智能体逐工作包输出格式

若执行环境允许，执行智能体每完成一个工作包应输出或记录：

```text
Work Package: Mx.y

Changed:
- file A: what changed
- file B: what changed

Behavior:
- user-visible behavior
- backend invariant

Tests:
- command
- result

Compatibility:
- what legacy behavior remains

Known limitations:
- only real remaining limitations, not future wishlist
```

不要只报告“完成了 M3”。

---

# Part XI — 阶段 Gate（进入下一阶段的硬条件）

## Gate M1 → M2

- 新 Global routes 工作；
- Investigation 产品文案完成；
- legacy routes 可达；
- Project/Case CRUD 无退化。

## Gate M2 → M3

- old CaseWorkspace 已使用共享 Run composable；
- InvestigationShell 可达；
- Copilot message 带 ui_context；
- SSE/approval/resume/cancel 回归通过；
- Activity advanced trace 可访问。

## Gate M3 → M4

- Collection versions/active 事务通过；
- Crawl 使用 active；
- no active fallback；
- Monitor snapshot；
- sandbox/approval regression 通过。

## Gate M4 → M5

- Finding auto sync；
- historical sync；
- Review atomic mapping；
- Evidence full workspace；
- Provenance 一跳；
- Challenge 可运行。

## Gate M5 → M6

- Network full view；
- Evidence deep link；
- Timeline time range context；
- Alignment/Integrity 已迁移；
- Narrative/Semantics/Goals 不再放错误一级导航。

## Gate M6 → M7

- Global Signals 无 N+1；
- Signal actions 复用 Alert 状态机；
- Home operational overview；
- Monitor 已进入 Overview 管理。

## Gate M7 → M8

- ReportDocument draft/edit/version conflict；
- publish validation；
- published immutable；
- export；
- Finding → Report。

## Final Gate

- 全量 tests；
- 新 E2E；
- no duplicate runtime subscription；
- no primary legacy sidebar workflow；
- no broken legacy API；
- docs updated。

---

# Part XII — 最终 E2E 验收矩阵（具体断言）

## E2E-A：新 Investigation 与 Collection

```text
Given no investigation
When user creates one with topic/platform/time
Then it appears under Investigations, not Conversations
And route is /investigations/:id/overview
When user generates collection suggestion
Then draft v1 appears
When user edits a keyword and activates
Then v1 ACTIVE is visible
And subsequent crawl trace contains collection id/version
```

## E2E-B：Durable Agent + Approval 未退化

```text
When analysis starts
Then Activity shows semantic running status
And Copilot remains usable
If crawl approval is required
Then inline/global approval is visible
When approved
Then exact run resumes rather than a new unrelated run
And final trace still contains model/tool/approval/cost
```

## E2E-C：Contextual Copilot

```text
Open Network
Select inferred edge
Ask “为什么？”
Assert request payload contains workspace=network + selected edge id
Assert persisted run metadata has ui_context
Assert ContextBuilder includes non-evidence UI context warning
Assert final answer still references Tool/Evidence data when factual
```

## E2E-D：Finding 生命周期

```text
Complete Opinion/Verification expert
Assert artifact exists
Assert candidate findings exist
Open finding
Submit review
Claim/approve
Assert Review item accepted
Assert Finding verified in same completed operation
Reload page
Assert state persists
```

## E2E-E：Network → Evidence

```text
Select propagation edge
Click evidence ref
Assert Evidence route opens and target selected
Back to Network
Promote edge to Finding
Assert new candidate finding with source edge link
```

## E2E-F：Signals

```text
Seed/trigger monitor alert
Open /signals
Assert mapped signal
Acknowledge
Assert alert backend becomes acknowledged
Open investigation from signal
Assert correct case route
Home count reflects current open/ack state per definition
```

## E2E-G：Report

```text
Have report artifact + verified finding
Create product draft from artifact
Add finding
Edit summary using expected lock_version
Submit/publish
Assert invalid cross-case citation fails
Fix citation
Publish
Assert read-only
Click modify -> new draft revision
Export HTML
Assert sensitive redaction still applies
```

## E2E-H：Legacy compatibility

```text
Open old /cases/:id
Assert redirect to new overview
Old artifact download still works
Existing cases with no Collection/Finding/ReportDocument still open
Analysis still works using fallback behavior
```

---

# Part XIII — 实现时常见错误与明确处理

## 错误 1：为了 Investigation 命名全局改 case_id

处理：禁止。只改产品层。

## 错误 2：每个 Investigation Tab 都重新获取所有数据

处理：Shell 只加载共享轻量数据，tab lazy load。

## 错误 3：Copilot selection 直接注入对象正文

处理：只注入 ID/导航 context；事实由 Tool 查询。

## 错误 4：Finding Materializer 覆盖已人工审核 Finding

处理：source link 已存在即 skip；Review 状态不可被 sync 重置。

## 错误 5：Signal 复制 Alert 数据

处理：adapter/query view，不新建 table。

## 错误 6：Report 编辑 Artifact

处理：Artifact 不变；ReportDocument 是产品可编辑层。

## 错误 7：Report 发布同步调用 LLM

处理：Publish Gate deterministic；Agent validator 是可选预检查。

## 错误 8：把 Review acceptance 在前端映射 Finding verified

处理：后端事务同步。

## 错误 9：新表 Case 删除失败

处理：同步更新 explicit delete_case cascade + test。

## 错误 10：拆 CaseWorkspace 时 SSE 被复制

处理：先 M2.1 composable，再拆。

## 错误 11：新 Home 做几十个并行 API

处理：Workspace Overview aggregate endpoint。

## 错误 12：Network 只显示图没有可审计信息

处理：Edge Detail 必须展示 confidence/features/evidence/version。

---

# Part XIV — 本轮完成后的代码架构预期

完成后主要依赖方向应近似：

```text
Vue Global Shell
   │
   ├─ Home ─────────────── WorkspaceOverview API
   ├─ Signals ──────────── SignalService ── MonitorRepository/Alert
   ├─ Investigation Shell
   │    ├─ Overview ────── Case/Goals/Collection/Monitor
   │    ├─ Live Data ───── Social/Media/Comparison
   │    ├─ Evidence ────── Claim/Evidence/Semantics/Provenance
   │    ├─ Network ─────── Propagation/Alignment/Integrity
   │    ├─ Timeline ────── Narrative/Time series
   │    ├─ Findings ────── FindingService + ReviewService
   │    ├─ Report ──────── ReportDocumentService
   │    └─ Activity ────── Run Events/Trace/Case Activity
   │
   └─ Contextual Copilot ─ POST /cases/{id}/messages(ui_context)
                              │
                              ▼
                    AgentRunService / Durable GraphWorker
                              │
                   AgentRuntime / Tools / Experts
                              │
                 Artifact / Evidence / Findings materializer
```

边界应保持：

```text
LLM/Agent：提出分析与生成结构化 Artifact
Deterministic services：Collection、Finding 状态、Signal 映射、Report publish
Human Review：verified/rejected 最终责任
Evidence：事实引用底座
UI：组织工作流，不决定事实状态
```

---

# Part XV — 交付执行智能体前的最终说明

执行智能体应把本文当成**目标约束 + 实施顺序 + 验收合同**，而不是要求逐字符实现的伪代码。

当现有仓库实现细节发生轻微变化时：

- 保持本文的对象边界；
- 使用当前生产路径；
- 不重建已存在能力；
- 不引入并行旧系统；
- 保留兼容与测试。

如果必须在“最快写完”和“保持现有 Harness 安全/持久化语义”之间选择，必须选择后者。

本轮最终成功标准仍然只有一个：

> **用户进入系统后，看到和操作的是一个 Investigation Intelligence Workbench；Agent Runtime 的复杂度被正确隐藏，但它的 Durable、Evidence、HITL、Audit 与安全能力全部被保留并成为产品可信度基础。**
