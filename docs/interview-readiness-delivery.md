# Interview Readiness — Delivery Log

> 主计划：`docs/Nothing-in-the-dark_Interview_Readiness_Execution_Plan.md`
> 本文档按 Phase 增量维护，禁止事后凭记忆补写。
> 记录格式：Implementation / Changed Files / Tests / Server Verification / Known Limitations / Commit

---

# Phase 0 — Baseline & Scope Freeze

## Baseline

| 项 | 值 |
|---|---|
| Baseline HEAD | `74b827d903fa1d9a83ee698800e2669d1ab80d28` |
| Branch | `main` |
| Date | 2026-09-16 |
| Working tree | 干净（无 modified/staged），仅未跟踪文件：主计划、服务器状态文档、`deploy.env`/`deploy1.env`、`data/`、`backend/uv.lock`、`home_page_screenshot.png` |
| Remote | `origin/main` 与本地一致（`git rev-list --count HEAD..origin/main` = 0，本地无未推送提交） |

## 关键实现路径定位（Phase 0 必读）

```text
Production Agent Entry Point:
  HTTP:  backend/app/api/routes/cases.py:167  create_agent_message()
         POST {api_prefix}/cases/{case_id}/messages  (202, CreateMessageRequest)
  进程内: backend/app/application/agent_service.py:46  AgentRunService.start()
         → repository.create_agent_run() (repositories.py:1288) → status="pending"
  执行者: backend/app/application/graph_worker.py:64  GraphWorker
         claim: repositories.py:2185 claim_agent_run() (lease + SKIP LOCKED)
         execute: graph_worker.py:260 _execute(run_id)
  图:     backend/app/graphs/agent_loop.py:47  AgentLoopGraph
         节点 steering_step → model_step →(条件)→ tool_step → …→ finish
  核心:   backend/app/harness/runtime.py:165  AgentRuntime
         run:187 / step_model:291 / step_tools:383 / _execute_tool:420
  Coordinator definition: backend/app/harness/agents.py:308 build_coordinator_definition()
         （agents.py:363 CoordinatorAgent 是遗留 stub，生产不用）
  run 状态: pending | running | waiting_approval | completed | failed | cancelled
         （字符串字面量；见 a2a/schemas.py:32-40 的映射表）

Agent Run Persistence:
  backend/app/infrastructure/database/models.py:181  AgentRunRecord (table: agent_runs)
    字段: id, case_id, turn_id, parent_run_id, agent, status, objective,
          model_route, input_tokens, output_tokens, tool_call_count,
          estimated_cost, error_code, error, lease_owner, metadata_json, ...
    注意: 无 prompt/response 列；最终回答写入 conversation_turns
          (models.py:107 ConversationTurnRecord) 并经 turn_id 关联
    （写入点 graph_worker.py:424 add_turn / :430 update_agent_run）

Tool Invocation Persistence:
  backend/app/infrastructure/database/models.py:294  ToolCallRecord (table: tool_calls)
    字段: id(=tool_call_id), run_id, tool_name, skill_name, status, arguments(JSON),
          result(JSON), error_code, input_summary, output_summary, retry_count,
          cached, duration_ms, estimated_cost, approval_id, rag(JSON),
          idempotency_key, started_at, finished_at
  写入: GraphWorker._persist_event (graph_worker.py:653) →
        repositories.py:1496 add_tool_call / :1570 update_tool_call
        （tool 调用记录不在 agent_runs 的 JSON 列里）

Run Event Persistence（SSE 回放源）:
  backend/app/infrastructure/database/models.py:277  RunEventRecord (table: run_events)
    字段: id(自增, 作 SSE cursor), run_id, event_type, agent, skill,
          tool_call_id, tool, status, trace_id, payload(JSON)
  写入: repositories.py:1446 add_run_event；读取 repositories.py:1474 list_run_events
  API:  backend/app/api/routes/runs.py:60 /events, :70 /events/stream

Model Call Metrics:
  持久化: backend/app/infrastructure/database/models.py:259 ModelCallRecord (table: model_calls)
    字段: input_tokens, cached_input_tokens, output_tokens, estimated_cost,
          currency, pricing_model, latency_ms, error_code, status, route
    链路: gateway → runtime.step_model (harness/runtime.py:291) 发 model_call_end
          → graph_worker.py:660 → repositories.py:1408 add_model_call
  进程内: backend/app/telemetry/metrics.py:101 MetricRegistry
    指标: llm.calls / llm.errors / llm.retries / llm.latency_ms /
          llm.tokens_input / llm.tokens_output / llm.cost_cny
    记录点: backend/app/infrastructure/llm/gateway.py:133, :163-175
  Gateway: backend/app/infrastructure/llm/gateway.py:59 LLMGateway（抽象）
           :75 OpenAICompatibleGateway → complete():109（唯一模型调用入口）
  定价: backend/app/infrastructure/llm/pricing.py:43 estimate_deepseek_cost()
  SLO:  backend/app/telemetry/slo.py:51 DEFAULT_SLOS；健康 API
        backend/app/api/routes/system.py:194 GET /system/telemetry-health

Existing Evaluation Entry:
  API:     backend/app/api/routes/evaluation.py
           POST /evaluation/datasets, POST /evaluation/runs,
           POST /evaluation/runs/{id}:gates, GET/POST /evaluation/gates, POST /evaluation/drift
  Service: backend/app/application/evaluation_service.py:26 EvaluationService
           register_dataset:37 / run_evaluation:103 / evaluate_gates:179
           容器装配: backend/app/bootstrap.py:553
  Registry: backend/app/services/evaluation.py:352 EvaluatorRegistry
            build_default_registry():396 → 4 个既有 evaluator
            （sentiment / stance / propagation_edges / claim_citations；
             签名 fn(examples, config) -> dict，是 domain evaluator，
             不涉及 agent trajectory）
  Gate:     backend/app/services/quality_gate.py:163 ReleaseGate
            evaluate(metrics, baseline, sample_sizes):177
            GATE_PASS/BLOCK/INCONCLUSIVE :17-19
            NON_EXEMPTIBLE_METRICS :22（当前 4 项：attack_success_rate /
              secret_leak_rate / sandbox_escape_rate / unauthorized_tool_rate）
            default_release_gate():260
  表:       EvaluationRunRecord (models.py:2407, table: evaluation_runs)
            ReleaseGateRecord (models.py:2433), EvaluationGateResultRecord (models.py:2454)
            DatasetManifestRecord (models.py:2346), DatasetExampleRecord (models.py:2370)
  关键:     evaluation_runs.config 是 JSON，baseline_metrics / sample_sizes
            经它传入 evaluate_gates（evaluation_service.py:215-230），
            **Agent Eval 接入无需改表结构**
  Trace:    repositories.py:2259 get_run_trace()（聚合 run + model_calls +
            tool_calls + approvals + events）；API runs.py:125 GET /runs/{id}/trace

Tool Registry:
  backend/app/harness/tools.py:80 ToolRegistry，register():185
  ToolSpec: tools.py:21（permissions:27 / side_effect:28 / requires_approval:33 /
            risk_level:44 / execution_class:45 / external_handler:53 /
            approval_scope_resolver:56）
  策略: backend/app/harness/approval_policy.py:352 ApprovalPolicyEngine，
        classify_tool():385，decide():403，只读白名单 _AUTO_APPROVE_READONLY_TOOLS:363
  装配: backend/app/harness/tool_factory.py:361 build_tool_registry()；
        bootstrap.py:276 self.tools = build_tool_registry(...)
  工具清单（当前 32 个 + 动态 MCP）:
    核心 18: load_skill, search_social_evidence, write_case_memory,
      collect_social_posts*, start_social_collection*, get_collection_run,
      dispatch_expert, get_artifact, classify_sentiment, query_claims,
      query_evidence, query_propagation, analyze_opinion,
      reconstruct_propagation, verify_claims, build_report,
      compare_platforms, submit_review_item
      （* = requires_approval=True, risk_level="high"）
    DB 只读 9 (harness/database_tools.py): get_case_data_overview,
      query_social_posts, get_social_post, query_social_comments,
      aggregate_social_data, query_findings, query_review_items,
      query_reports, query_case_activity
    情报 5 (harness/intelligence_tools.py): get_investigation_quality,
      query_related_investigations, query_workspace_entities,
      get_workspace_entity, query_signals
    动态: mcp:{server}:{tool}（tool_factory.py:296）
```

## 基线测试

基线 smoke（`uv run --extra dev pytest`，覆盖评测框架 + 生产入口 + agent loop +
durable runtime + tool registry）：

```text
命令: tests/test_production_entry.py tests/test_evaluation_framework.py
      tests/test_evaluation_gates.py tests/test_agent_loop.py
      tests/test_durable_runtime.py tests/test_tool_registry.py
结果: 65 passed, 1 warning in 804.31s (0:13:24)
```

耗时说明：本机为 Windows + SQLite 文件 I/O，`create_schema()` 单次即需数十秒
（见 `backend/tests/memory_db.py` 顶部注释），Linux CI 会显著更快。全量
128 个测试文件的 final gate 按计划留到 Phase 9 执行。

## 关键环境事实（影响后续 Phase 设计）

```text
1. demo_mode 只替换 crawler（DemoCrawlerAdapter），不是 fake LLM。
   Tier A 必须自行提供 LLMGateway 实现（测试中既有模式：ScriptedGateway）。
   参考: backend/tests/test_expert_agents.py:63

2. Tier A 可用最小装配模式（真实 production 组件）：
   Database → ApplicationRepository → build_tool_registry(...) →
   GraphWorker(repository, <scripted gateway>, tools, skills, ...,
              checkpointer=MemorySaver()) → AgentRunService(repo, worker)
   → create_case → service.start(case_id, content) → worker.tick(wait=True)
   参考: backend/tests/test_expert_agents.py:117 _build_worker

3. ReleaseGate.evaluate 含 sample_sizes 检查：size < 30 会产出
   insufficient_sample 违规（quality_gate.py:238-248）。24 任务的 suite
   必然 < 30，Phase 3 接入时必须显式处理，不能盲目传 sample_sizes。

4. NON_EXEMPTIBLE_METRICS 当前仅 4 项（attack_success_rate / secret_leak_rate /
   sandbox_escape_rate / unauthorized_tool_rate），需要新增 agent 安全指标
   （forbidden_tool_violations / invalid_citation_count / unexpected_mutation_count）
   才能使它们获得“0 容忍 + 不可豁免”语义（上限类判定）。
```

## 服务器部署基线（与 Interview Readiness 相关）

| 项 | 阿里云 | 腾讯云 |
|---|---|---|
| 代码 HEAD | `74b827d`（与本地一致） | `ddbe685`（落后本地） |
| GitHub 直连 | **可达**（可 git pull） | **不可达**（需 git bundle / scp） |
| 服务状态 | 已停止（`coifesp-backend`/`coifesp-mlworker`/`postgresql` 均 disable+inactive） | 同左 |
| nginx | 仅占位站点（:80） | 仅占位站点（:80） |
| `LLM_API_KEY` | SET | SET |
| `PLATFORM_AUTH_MASTER_KEY` | SET | 未配置（旧 .env） |
| `DEMO_MODE` | 键存在 | `true` |
| DATABASE_URL | PostgreSQL (asyncpg) | PostgreSQL (asyncpg) |
| Python env | conda env `coifesp` + `mediacrawler` | `/opt/coifesp/app/backend/.venv` |
| 前端 dist | 已构建 | 已构建 |
| 浏览器测试环境 | headless-chrome CDP 127.0.0.1:9222 | 同左 |
| 代码目录 | `/opt/coifesp/app` | `/opt/coifesp/app` |
| systemd 单元 | `/etc/systemd/system/coifesp-*.service` | 同左 |

**与 Interview Readiness 相关的环境约束**：

1. 两台服务器当前**服务处于停止状态**（上一轮为部署新项目做的休眠），本轮完成后需按
   `git pull`（阿里云）或 bundle（腾讯云）→ migration → restart → smoke 的顺序重新上线。
2. Tier B real-model eval 需要真实 LLM key：**阿里云已具备**（LLM_API_KEY SET + PG + 完整
   42 项 env），可作为 real-model baseline 的执行环境；腾讯云 GitHub 不通、env 较旧，
   定位为辅助验证机。
3. 阿里云部署用户是 root（conda env），腾讯云是 `ubuntu`（venv）——**部署脚本与
   migration 命令必须区分**，不能假定同一用户。
4. 两台服务器的 PostgreSQL 与 pgvector 数据完整保留，未重装。
5. 服务器上的 `.env` 均为**服务器本地文件**，不由仓库管理；任何新配置项必须走
   `backend/app/core/config.py` 的默认值，不能假设服务器 .env 已包含新键。
6. Tier A Contract Eval 不依赖 LLM key，**两台服务器都可执行**（用于部署后 smoke）。
   Tier B 只在阿里云执行。

## Phase 0 状态判定（基于代码与文档实况）

| Phase | 状态 | 判定依据 |
|---|---|---|
| Phase 0 Baseline & Scope Freeze | **DONE** | 本节；HEAD/远端/关键入口已核验并记录 |
| Phase 1 Agent Golden Dataset | **NOT STARTED** | `backend/app/evaluation/` 不存在；`backend/tests/fixtures/` 不存在；无 `AgentGoldenTask` 定义 |
| Phase 2 End-to-End Agent Evaluators | **NOT STARTED** | 现有 `EvaluatorRegistry` 是 domain evaluator（`fn(examples, config)`），无 agent trajectory evaluator、无 `agent_evaluation_service.py` |
| Phase 3 Baseline / Candidate + Release Gate | **PARTIAL** | 基础设施已存在且可用（`EvaluationRunRecord` + `ReleaseGate` + `baseline_metrics`/`sample_sizes` 经 `config` 传入 + `evaluate_gates` API），但无任何 `agent.*` 指标接入 |
| Phase 4 Trace Replay & Regression | **NOT STARTED** | 无 `agent_replay_service.py`；但 `get_run_trace()`(repositories.py:2259) 已提供 run+tool_calls+events+model_calls 聚合，可复用为 Replay Bundle 数据源 |
| Phase 5 GitHub Actions CI | **NOT STARTED** | `.github/workflows/` 目录不存在 |
| Phase 6 Canonical Benchmark | **NOT STARTED** | 无 `run_agent_benchmark.py`、无 `artifacts/benchmark/` |
| Phase 7 Interview Demo & Narrative | **NOT STARTED** | `docs/interview/` 不存在 |
| Phase 8 Optional Production Closure | **NOT STARTED** | `backend/app/services/platform_auth.py:196 validate()` 仍返回 `validation_not_implemented` |
| Phase 9 Final Verification | **NOT STARTED** | 依赖 Phase 1–8 |

## Scope Freeze（自 Phase 0 起生效）

本轮禁止：新增业务功能 / 改业务导航 / 新增 Expert Agent / 改 Finding·Review 状态语义 /
改 Collection 算法 / 改 Cross Intelligence 算法 / 改 Debate 语义 / 改 Platform Auth 架构。

仅允许为 Eval observability、Trace replay、CI、Benchmark、Docs 所需的 **additive
instrumentation**。

---

<!-- 后续 Phase 记录追加于下方 -->

---

# Phase 1 — Agent Golden Dataset

## Implementation

建立版本化 Golden Dataset `interview_agent_v1`：24 个任务（6 类别 × 4），
冻结 fixture 数据，统一 schema 与校验器，全部数据为虚构合成内容（无真实主体、
无联网依赖）。

- 新增 `backend/app/evaluation/` 包（Agent 评测层，与 `app.services.evaluation`
  的 domain 算法评测区分）。
- `agent_dataset.py` 定义 `AgentGoldenTask` / `AgentExpectedBehavior` /
  `AgentEvalBudget` / `StateAssertion` / `AgentGoldenSuite`，提供 `load_suite()`
  与 `validate_suite()`。
- `StateAssertion` 采用 `kind + target + expected` 三元组，kind 白名单 11 种
  （`no_mutation` / `artifact_exists` / `finding_status` / `tool_call_status` 等），
  供 Phase 2 的确定性 evaluator 消费。
- 冻结 fixture 4 组：`case_grounding`（主调查，2 平台 10 帖 + claims/evidence/
  findings/report）、`case_cross`（3 调查 + 共享账号 + 2 cross link + 1 signal）、
  `case_review`（1 finding 对 13 条混合 stance evidence）、`case_empty`（零数据）。
  合计 6 个调查、21 帖、20 条 evidence、5 个 finding。

## Changed Files

```text
新增 backend/app/evaluation/__init__.py
新增 backend/app/evaluation/agent_dataset.py
新增 backend/tests/fixtures/agent_eval/interview_agent_v1/manifest.json
新增 backend/tests/fixtures/agent_eval/interview_agent_v1/fixtures.json
新增 backend/tests/fixtures/agent_eval/interview_agent_v1/tasks/G1_01..G6_04.json（24 个）
新增 backend/tests/test_agent_golden_dataset.py
新增 docs/interview/golden-dataset.md（由数据集自动生成）
修改 docs/interview-readiness-delivery.md（本文件）
```

## Tests

```text
命令: uv run --extra dev pytest tests/test_agent_golden_dataset.py -q
结果: 14 passed in 15.70s
```

覆盖：数量与类别分布冻结、id 唯一且有序、`validate_suite` 零问题、
**工具名与真实 Tool Registry 比对**（`build_tool_registry` + DB/情报工具包，
非 eval 替身）、G5 全 critical 且 critical 不外溢、只读任务必须带 `no_mutation`、
fixture 跨引用完整性（post/claim/evidence/finding/case key 全部可解析）、
fixture 不含任何真实平台 URL、schema 序列化往返、坏数据被校验器拒绝。

## Server Verification

本 Phase 为纯数据与 schema 变更，不依赖运行环境（无 DB、无模型、无外部服务），
因此未在服务器执行。数据集加载会在 Phase 2 的服务器 smoke 中一并验证。

## Known Limitations

1. `expected_citation_refs` 使用 fixture 的逻辑 key（如 `ev_rep_mismatch`），
   Phase 2 的 Citation Validity 需要把逻辑 key 映射到运行时真实 id 后再判定。
2. `answer_must_contain` / `answer_must_not_contain` 是子串匹配，不是语义匹配；
   Tier B 真实模型可能因措辞差异失败——这是评测信号，不通过放宽断言来消除。
3. G2 任务声明了 `required_artifact_types`（propagation_graph / report），
   是否真能产出取决于 runtime 的实际工具契约，Phase 2 执行时验证。
4. fixture 规模刻意保持小（24 任务），不追求统计显著性；指标必须连同
   sample size 一起展示。

## Commit

```text
feat(eval): add versioned agent golden dataset
```

---

# Phase 2 — End-to-End Agent Evaluation

## Implementation

建立 Tier A（contract）与 Tier B（real_model）两条 E2E 评测链路，全部复用
**真实 production runtime**：`GraphWorker` + `AgentRunService` + `build_tool_registry`
+ `ApprovalPolicyEngine` + run/event/tool_call 持久化。Tier A 只把 `LLMGateway`
换成 scripted 实现，不新建第二套 runtime / tool system / evaluation system。

新增模块（`backend/app/evaluation/`）：

```text
agent_trace.py       轨迹层：ToolCallView / ArtifactView / FindingView / ApprovalView /
                     ReviewItemView / AgentTrace / CaseStateSnapshot / StateDiff
                     + collect_trace() / capture_state() / diff_snapshots()
agent_evaluators.py  E1–E10 确定性 evaluator（纯函数，无 LLM）
agent_fixture_seed.py fixtures.json → 真实 DB（全部走生产 repository/service）
agent_eval.py        AgentSuiteRunner（contract/real_model）+ ContractGateway +
                     报告结构 + 指标聚合 + hard gate
```

服务与入口：

```text
backend/app/application/agent_evaluation_service.py
    AgentEvaluationService.run_agent_suite / persist_report / gate_inputs
    → 结果写入现有 evaluation_runs（不新建第二套评测系统）
backend/app/scripts/run_agent_eval.py
    CLI：--mode contract|real_model，--output 生成 report.json + report.md，
        --fail-on-hard-gate 供 CI 使用
```

Evaluator 清单（计划第 26 节要求的 9 个 + case scope）：

| ID | metric | 判定方式 |
|---|---|---|
| E1 | agent.task_success | state assertions + required artifacts + 已回答（期望审批时接受 waiting_approval 停等） |
| E2 | agent.required_tool_coverage | required_called / required |
| E3 | agent.forbidden_tool_violations | 违规调用计数（必须 0） |
| E4 | agent.tool_argument_accuracy | 真实 ToolSpec.input_model 的 schema 校验 + limit 上界 |
| E5 | agent.unexpected_mutation_count | pre/post 快照 diff（artifact 增长不计） |
| E6 | agent.invalid_citation_count | 回答中的 id token 必须真实存在（幻觉检测） |
| E7 | agent.evidence_grounding | 期望 evidence 是否经 ref_map 落到回答中 |
| E8 | agent.human_escalation_correctness | 只看**本次运行**产生的审批/评审（不误判 fixture 历史数据） |
| E9 | agent.efficiency | steps / tool_calls / latency / tokens / cost |
| E10 | agent.unexpected_case_scope_violation | 工具参数中的 case_id 必须等于期望 case |

## Changed Files

```text
新增 backend/app/evaluation/agent_trace.py
新增 backend/app/evaluation/agent_evaluators.py
新增 backend/app/evaluation/agent_fixture_seed.py
新增 backend/app/evaluation/agent_eval.py
新增 backend/app/application/agent_evaluation_service.py
新增 backend/app/scripts/run_agent_eval.py
新增 backend/tests/test_agent_eval_evaluators.py（21 个单测）
新增 backend/tests/test_agent_eval_contract.py（17 个 Tier A 端到端测试）
新增 backend/tests/test_agent_fixture_seed.py（5 个 seed 测试）
修改 backend/app/evaluation/agent_dataset.py（新增 expected_tool_arguments）
修改 backend/tests/fixtures/agent_eval/interview_agent_v1/tasks/*.json（12 个任务权限边界修正，task_version → 2）
修改 docs/interview-readiness-delivery.md
```

## Tests

```text
tests/test_agent_eval_evaluators.py  21 passed in 0.32s
tests/test_agent_fixture_seed.py      5 passed in 3.72s
tests/test_agent_eval_contract.py    17 passed in 217.32s（含 24 任务全量 suite）
```

Tier A 24 任务实测结果：

```text
agent.task_success_rate                  1.0      (24/24)
agent.required_tool_coverage             1.0
agent.tool_argument_accuracy             1.0
agent.critical_task_success_rate         1.0      (G5 4/4)
agent.forbidden_tool_violations          0.0
agent.invalid_citation_count             0.0
agent.unexpected_mutation_count          0.0
agent.unexpected_case_scope_violation    0.0
agent.avg_steps                          2.12
agent.avg_tool_calls                     1.12
agent.p50_latency_ms                     742.5
agent.p95_latency_ms                     2827.8
hard gates                               [] (pass)
```

满足计划第 75 节的 Tier A Eval Gate：24/24 可执行、critical 100%、
forbidden/scope/citation 违规全 0。

## 实施中发现并修正的真实架构约束（重要）

1. **coordinator 的工具白名单是硬边界**：`build_coordinator_definition()`
   只允许 22 个工具；`classify_sentiment` / `reconstruct_propagation` /
   `verify_claims` / `build_report` / `query_claims` / `query_evidence` 都**不在**
   coordinator 手里，它们属于 6 个专家 agent。首轮 12 个任务因越权调用失败——
   runtime 正确地丢弃了越权工具调用（这是安全行为，不是 bug）。
   修正方式：任务改为声明 `dispatch_expert`（专家层工具进入 optional 或由子 run
   覆盖），并新增 `expected_tool_arguments` 字段声明委派目标。
2. **`dispatch_expert` 同步等待子 run**（`await _wait_for_child`），
   因此 runner 必须用后台 `worker.start()` 循环驱动；用 `tick(wait=True)`
   会在主 run 等待子 run 时死锁（子 run 永远无法被 claim）。
3. **专家子 run 的产物要合并进 trace**：`propagation_reconstruction` /
   `report` 等 artifact 挂在子 run 上，`collect_trace` 需要遍历
   `list_child_runs` 才能让 artifact 断言生效。
4. **子 run 与主 run 共用 gateway 实例**：Tier A 的 scripted gateway 必须按
   system prompt 做角色隔离，否则专家调用会吃掉主 run 的脚本步骤
   （表现为最终回答退化成 `{"done": true}`）。
5. **runtime 先写终态、后收尾**：`status=completed` 之后还有 emit `agent_end`
   与释放 lease，此时 `worker.stop()` 会 cancel 该 task，触发 CancelledError
   分支把已完成 run 误标成 `cancelled`。修复：用 lease 释放作为收尾信号。
6. **`no_mutation` 与 E5 必须同口径**：artifact 是工具正常产物，两处都排除。

## Server Verification

本 Phase 未在服务器执行：Tier A 需要运行完整 backend（SQLAlchemy + 真实
Tool Registry + LangGraph），服务器当前处于休眠状态（服务 disable + nginx 占位页）。
计划在 Phase 5 完成后、Phase 9 期间统一做服务器最小真实验证
（`git pull` → migration → restart → 在服务器上跑 Tier A contract suite）。

## Known Limitations

1. **Tier A 的回答由脚本构造**：E7 在 Tier A 验证的是"引用链路可解析"，
   真正的 grounding 质量必须由 Tier B（真实模型）证明。README/Benchmark 不得
   把 Tier A 说成 real-model 结果。
2. **Tier B 需要真实 LLM key**：入口已就绪（`--mode real_model`，复用生产
   `OpenAICompatibleGateway`），但本机与 CI 尚未配置 key，属 BLOCKED，
   将在有 key 的环境（阿里云服务器）执行并记录 baseline。
3. **专家子 run 的工具调用**不参与 E2/E3/E4/E10 判定（那些判定针对 coordinator
   层）；子 run 的工具序列完整记录在 `trace.child_tool_calls`，供 Phase 4 的
   Replay diff 使用。
4. **E9 的 token/cost 在 Tier A 恒为 0**（scripted 模型无真实用量）；
   Tier B 才有意义。
5. 24 任务是小样本，指标必须连同 sample size 展示（计划第 57 节）；
   `AgentEvaluationService.gate_inputs` 显式不传 sample_sizes，避免现有门禁的
   `<30` 样本下限规则误报。

## Commit

```text
feat(eval): run production agent runtime against golden tasks
feat(eval): add deterministic trajectory evaluators
```
