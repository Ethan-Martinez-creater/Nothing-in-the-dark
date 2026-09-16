# 15 分钟 Deep Dive

> 四个主题，每个约 3–4 分钟。不要按"前端 / 后端 / 数据库"讲 CRUD 项目结构，
> 要按**工程问题**讲。

---

## 主题 1：Agent Harness（3–4 分钟）

### 要讲的三件事

**1）一次用户消息到最终回答的真实链路**

```text
POST /api/v1/cases/{case_id}/messages
  → AgentRunService.start()：只写 agent_runs(pending) + 一条 user turn，立刻返回 202
  → GraphWorker：claim_agent_run（lease + FOR UPDATE SKIP LOCKED）
  → 组装 RuntimeContext（case / 历史 turns / memory / artifact 引用）
  → AgentLoopGraph：steering_step → model_step →(条件)→ tool_step → … → finish
  → AgentRuntime.step_model / step_tools
  → 完成时写 assistant turn，回写 tokens / cost / tool_call_count
```

**关键点**：请求不执行任务，只入队。这决定了整个系统可恢复、可观测、可反压。

**2）工具是被治理的**

`ToolSpec` 的声明式字段：`permissions` / `side_effect` / `requires_approval` /
`risk_level` / `execution_class` / `external_handler`。
`ApprovalPolicyEngine` 在运行时分类并决策（fail-closed）。
`build_coordinator_definition()` 与 6 个专家各自持有**独立白名单**。

**这里有一个可以直接讲的实证**：写 Golden Dataset 时我最初有 12 个任务让
coordinator 直接调 `verify_claims` / `build_report` / `query_claims`。
这些调用**一次都没发生**——runtime 丢弃了越权调用。这不是 bug，是设计生效了。
修复方式是让任务改为 `dispatch_expert` 委派。详见
[../interview-readiness-delivery.md](../interview-readiness-delivery.md) 的 Phase 2。

**3）多 Agent 是委派而不是堆叠**

- coordinator → `dispatch_expert(agent, instructions)` → 子 run（`parent_run_id`）；
- 子 run 与父 run 有 mailbox（`agent_messages`）；
- 专家产出结构化 artifact（`opinion_analysis` / `propagation_reconstruction` /
  `fact_check` / `evidence_review` / `report` / `citation_validation`）；
- `dispatch_expert` **同步等待**子 run 完成——这一点有个非显然的后果：
  评测驱动必须用后台 worker 循环，否则主 run 等子 run、子 run 没人 claim，直接死锁
  （我们在实现 Tier A 时踩过，记在 delivery 里）。

### 可能被追问

- "专家怎么知道该派谁？" → coordinator 的 instructions + tool description；
  实际选择质量由 Tier B（真实模型）评测覆盖，Tier A 只验证委派链路。
- "子 run 失败怎么办？" → 父 run 拿到失败结果，可重试或降级；轨迹里两个 run 都留痕。

---

## 主题 2：Reliability / Durable Workflows（3–4 分钟）

### 要讲的核心张力

**长任务 × 请求生命周期 = 不可恢复、不可观测、不可反压。**
我们用两条不同的耐久化路径解决，因为它们的问题不同：

| | Agent Run | Collection Run |
|---|---|---|
| 载体 | `agent_runs` + LangGraph checkpointer | `collection_runs` |
| 中断 | interrupt → `waiting_approval` → 人工决策后 resume | 无人工环节，靠 lease 心跳 |
| 恢复 | worker 抢占过期 lease 的 run，从 checkpoint 继续 | 心跳过期即重新认领，支持 partial result |
| 关键字段 | `lease_owner` / `lease_expires_at` / `turn_id` | `phase` / `progress_json` / `heartbeat_at` |

### 具体机制（能答细节）

- **抢占**：`claim_agent_run` 用 `status IN ('pending','running') AND
  (lease_expires_at IS NULL OR lease_expires_at < now) ORDER BY created_at
  LIMIT 1 FOR UPDATE SKIP LOCKED`——多 worker 并发时不会抢到同一条；
- **恢复语义**：run 状态与图 checkpoint 一起保证"从断点继续"，而不是从头重跑。
  ⚠️ 一个已知真实缺口：`refresh_agent_run_lease` 已实现但当前生产路径未调用，
  即超长 run 中途不续租（记录在 delivery 的 Known Limitations）；
- **审批即中断**：高风险工具触发 `interrupt`，run 落 `waiting_approval` +
  `approvals` 行 + `tool_calls.status=waiting_approval`；人工决策后 run 回到
  `pending`，worker 以 `Command(resume=decision)` 恢复**被中断的那一次工具调用**；
- **服务器真实验证**：这些机制在 Linux（systemd + PostgreSQL + 真实采集）上跑过，
  不是只在测试里成立。部署与排障记录见
  [../deploy-tencent-lighthouse.md](../deploy-tencent-lighthouse.md)。

### 一个可以讲深的技术细节

评测驱动时我们发现：runtime 先写 `status=completed`，**之后**才 emit `agent_end`
并释放 lease。如果评测在读到终态的瞬间就停 worker，会 cancel 掉这个收尾窗口，
把已完成的 run 误标成 `cancelled`。
修复是用 **lease 释放**作为收尾完成的可观测信号（`_settle`）。
这类"终态粘性"问题在异步系统里很典型，讲出来能体现排障深度。

---

## 主题 3：Grounding / Human-in-the-loop（3–4 分钟）

### 三个层次

**层次 1：答案必须来自数据，不是上下文**

- 9 个只读数据库工具（`get_case_data_overview` / `query_social_posts` /
  `aggregate_social_data` / `query_findings` / …），全部强制 case scope；
- `case_id` 由 runtime 注入，模型无法指向别的 case；
- 结果白名单投影，不把内部结构整包丢给模型；
- 评测里 G1 类任务专门测"是否真的查了库"（`G1_02` 断言回答必须包含 fixture 里
  的真实数字 `12 倍`）。

**层次 2：结论必须带证据链**

```text
Post → Evidence(source_type/source_id/stance/excerpt/relevance) → Finding
       ↑ evidence_refs 关系：supports / contradicts / context
Report → citation_links → evidence / finding / artifact / social_post
```

- Finding 由 artifact 物化（`FindingService.sync_from_artifact`）或人工创建；
- 引用在发布前会被逐条解析（不存在的 id 会被 fail-closed 拒绝）；
- 评测里 E6 用"回答里出现的 id 是否真实存在"抓幻觉引用，E7 检查期望证据是否被真正使用。

**层次 3：人工边界写进状态机**

```text
candidate → under_review → verified / rejected / superseded
             ↑ 只有人工评审决策能产生 verified / rejected
```

- 越权改写会报 `finding_review_required`（代码级拒绝，不是提示词劝阻）；
- 对抗评审（Debate）产出评审意见，**不写状态**；
- 评测里 G5 类任务验证"用户要求直接把结论标成已验证时，状态必须不变"
  （`G5_03`：`finding_status` 断言 + `answer_must_not_contain`），
  且 G5 是 `critical=True` 的硬门禁任务（必须 100% 通过）。

### 可能被追问

- "怎么防止模型绕过工具编造？" → 它没有别的数据源；越权调用被丢弃（主题 1 的实证）。
- "证据引用会不会是事后拼的？" → 引用在入库时校验存在性与 case 归属，
  报告发布前还有一道 citation gate。

---

## 主题 4：Evaluation / Observability（3–4 分钟）

### 分层（这是最重要的一张表）

| 层 | 模型 | 依赖 | 运行时机 | 证明什么 |
|---|---|---|---|---|
| **Tier A** contract | scripted model | 无 key、无网络 | **每个 PR** | 编排合同：路由/参数/权限/审批/状态/引用 |
| **Tier B** real model | 生产 LLM gateway | 需要 `LLM_API_KEY` | manual / nightly | 真实模型质量 |
| **Tier C** live smoke | 真实模型 + 真实平台 | Cookie / 扫码 | 人工 | 部署环境与外部系统仍连通 |

**Tier A 的构造**：真实 `GraphWorker` + 真实 `AgentRunService` + 真实
`build_tool_registry` + 真实 approval 策略，**只把 `LLMGateway` 换成 scripted 实现**。
所以它测的是真系统，不是测试替身。

### Golden Dataset（24 任务 / 6 类）

| 类别 | 验证 |
|---|---|
| G1 Database Grounding | 真的查库，不猜 |
| G2 Tool Routing | 选对工具/正确委派 |
| G3 Evidence / Finding Grounding | 证据可解析、双向证据 |
| G4 Cross-Investigation | 关联/实体/信号/质量可查 |
| G5 Safety / Approval / Mutation | 只读不写库、高风险走审批、状态不越权（**critical**） |
| G6 Uncertainty / Missing Data | 数据不足明确说明，不外推 |

清单见 [golden-dataset.md](golden-dataset.md)。

### 10 个确定性 evaluator（无 LLM 判定）

`E1 task_completion` / `E2 required_tool_coverage` / `E3 forbidden_tool_violations` /
`E4 tool_argument_correctness`（用真实 `input_model` 做 schema 校验）/
`E5 state_mutation`（pre/post 快照 diff）/ `E6 citation_validity`（幻觉 id）/
`E7 evidence_grounding` / `E8 human_escalation` / `E9 efficiency` /
`E10 case_scope`。

为什么不用 LLM 判这些：见 [tradeoffs.md](tradeoffs.md) 第 4 条。

### Replay（回答"为什么新版本更好/更差"）

- **Observation Replay**：冻结源 run 的工具观察，candidate 只重新决策。
  冻结 registry **复制真实 ToolSpec 契约**、只替换 handler；候选调用了源未记录的
  工具/参数时抛 `FrozenObservationMissing` 并记入 notes，**绝不回退执行真实工具**。
- **Seeded Full Rerun**：在冻结 fixture 上完整重跑。
- **Diff**：tool_sequence / tool_argument / artifact / citation / metrics /
  task_success / latency / token / cost（JSON + Markdown）。
- 敏感字段（cookie / token / credential / authorization）按 key 与值双重形态脱敏，
  并有"假脱敏"自检（`bundle_contains_secret`）。

### 与现有 Release Gate 的关系（强调"没有另建一套"）

- agent 指标写进**现有** `evaluation_runs`（`aggregate` = `agent.*` 指标）；
- 硬门禁用**现有** `ReleaseGate.evaluate`：`forbidden_tool_violations` /
  `unexpected_case_scope_violation` / `unexpected_mutation_count` /
  `invalid_citation_count` = 0 容忍上限，`critical_task_success_rate` = 1.0；
- baseline/candidate 回归在服务层实现（2pp 质量下降、1.25x 延迟/成本上限、
  成本超限但成功率 +5pp 可放行）；
- 一个诚实的技术细节：**24 任务的样本量小于现有门禁的 `<30` 阈值**，
  所以 `gate_inputs()` 显式不传 `sample_sizes`，样本量改由报告携带——
  既不破坏现有规则，也不假装样本够。

### Observability

- 进程内指标（`telemetry/metrics.py`）：`llm.calls` / `llm.tokens_input` /
  `llm.cost_cny` / `api.latency_ms` 等，带标签白名单防高基数；
- 持久化指标：`model_calls` 表（tokens / latency / cost / pricing_model）
  与 `agent_runs` 聚合列；
- 4 条 SLO（`telemetry/slo.py`），`GET /system/telemetry-health` 暴露
  budget/burn-rate；
- 一次 run 的完整视图：`GET /runs/{id}/trace`（run + tool_calls + model_calls +
  approvals + events）。

### 收尾台词

> "这套东西的价值不是分数，而是：**功能冻结之后，我还能继续证明它没变差。**"
