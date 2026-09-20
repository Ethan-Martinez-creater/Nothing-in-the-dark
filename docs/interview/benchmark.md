# Canonical Agent Benchmark — interview_benchmark_v1

三个固定场景，全部使用**冻结 fixture**（虚构合成数据），执行时不联网、不依赖
平台 Cookie、不依赖扫码登录。

| 场景 | 内容 | 验证目标 |
|---|---|---|
| **B1** Grounded Investigation | 2 平台 32 帖、8 条证据、3 条结论 | Agent 跨平台查数、委派分析专家、给出可追溯结论 |
| **B2** Cross-Investigation Intelligence | 3 个调查、共享账号、共享媒体、observed + candidate 关系 | 识别跨调查关联并区分关系强度 |
| **B3** Adversarial Review | 1 条结论对 13 条混合 stance 证据 | 双向证据呈现、对抗评审不等于人工复核通过 |

## 运行上下文

> 计划第 57 节要求：禁止只展示百分比。以下每次运行都必须记录这组上下文。

| 项 | 值 |
|---|---|
| suite version | `interview_benchmark_v1` |
| mode | `contract` |
| model | `contract-scripted-model` |
| candidate | `candidate`（Final Closure 修复后重跑） |
| git SHA | `1346981ccc1296deb90ae87dd1d4e318f7f3695f`（+ 工作区 FC-IR 修复） |
| date | 2026-09-20T02:45:47Z（UTC） |
| sample size | **3** |
| hard gates | PASS |

> 注：上一版数据（`df03d200`）产生于 Eval Tool Stack 修复之前（DB /
> Intelligence Tool 仍走 `service=None` unavailable handler），按计划
> 第 9 节视为过期并作废。本页数据来自 FC-IR-01 修复后的重跑：DB /
> Intelligence Tool 接入真实生产只读服务。

## 场景结果

| 场景 | 任务 | 状态 | 通过 | 关键工具调用（真实服务） |
|---|---|---|---|---|
| B1 | `B1_grounded_investigation` | completed | Y | `aggregate_social_data`（DB07 真实查询）+ `dispatch_expert` |
| B2 | `B2_cross_investigation` | completed | Y | `query_related_investigations`（Cross Intelligence 真实查询） |
| B3 | `B3_adversarial_review` | completed | Y | `query_findings` + `dispatch_expert`（E5 零非预期变更） |

特别检查（修复计划 9.2）：

- **B1 真正查询 DB**：`aggregate_social_data` 由生产 `AgentDatabaseReadService`
  处理，聚合结果来自冻结 fixture 的 32 帖（非 unavailable）。
- **B2 真正查询 Cross Intelligence**：`query_related_investigations` 由生产
  `CrossInvestigationService` 处理，返回 fixture 的跨调查关联。
- **B3 保持 Human Review boundary**：E5 state mutation = 0，对抗评审没有
  产生任何非预期 finding/review 状态变更。

## 核心指标

| metric | value |
|---|---|
| `agent.task_success_rate` | 1.0 |
| `agent.required_tool_coverage` | 1.0 |
| `agent.tool_argument_accuracy` | 1.0 |
| `agent.invalid_citation_count` | 0.0 |
| `agent.unexpected_mutation_count` | 0.0 |
| `agent.avg_steps` | 2.67 |
| `agent.avg_tool_calls` | 1.67 |
| `agent.p50_latency_ms` | 2766.0 |
| `agent.p95_latency_ms` | 2820.9 |
| `agent.avg_input_tokens` | 0.0 |
| `agent.avg_output_tokens` | 0.0 |
| `agent.avg_cost_usd` | 0.0 |

（latency 为本机 Windows 墙钟；Linux 服务器数字见
`docs/interview-readiness-delivery.md` 的 Final Closure 章节。）

## 失败任务

本次运行没有失败任务。

## 诚实性声明（重要）

这份报告是 **contract 模式**（scripted model）的运行结果，用于证明
**编排合同**可测量、可复现：

- 它证明的是：真实 Agent Runtime + 真实 Tool Registry + 真实权限/审批/沙箱 +
  真实 Run/Tool Trace 持久化之上，三个基准场景都能跑通，且硬门禁（禁止工具调用、
  case 越界、非预期状态变更、幻觉引用）全部为 0。
- 它**不**证明模型质量。同 suite 的 `real_model` 模式（`--mode real_model`，
  复用生产 LLM gateway）才是真实模型评测；由于当前环境未配置 `LLM_API_KEY`，
  real-model baseline 处于 **BLOCKED**，一旦有 key 即可由
  `.github/workflows/agent-eval.yml` 或本地 CLI 跑出并替换本节数据。
- 因此：**不要把本页数字当作 real-model benchmark**。

## 复现方式

```bash
cd backend
# contract（无需任何 key，PR CI 也跑它）
python -m app.scripts.run_agent_benchmark --mode contract --candidate-label contract

# real model（需要 LLM_API_KEY）
python -m app.scripts.run_agent_benchmark --mode real_model \
    --candidate-label v1-baseline --baseline-id v1-baseline
```

产物：`artifacts/benchmark/<timestamp>/{report.json,report.md,traces/}`。
`traces/` 里是每个场景的 Replay Bundle（凭据字段已脱敏），可直接喂给
`AgentReplayService` 做 observation replay / seeded full rerun 与 diff。
