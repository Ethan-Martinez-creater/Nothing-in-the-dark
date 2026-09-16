# Interview Package — Nothing in the Dark

> 面向 Agent 应用开发 / Applied AI / Agent Platform 岗位的工程证明材料。
> 本目录所有内容都以**当前仓库真实实现**为准；数字都带样本量与运行上下文。

## 一句话定位

**Nothing in the Dark 是一个面向真实社交事件调查的 production-oriented Agent 应用：
它的核心工作不是调用大模型，而是让多步骤、多工具、多 Agent 的系统在真实数据、
异步任务、权限、安全、Memory、Evidence、评测与 Linux 部署环境下可靠运行。**

英文版：

> Nothing-in-the-dark is a production-oriented agentic investigation workbench for
> social-event intelligence. Its engineering focus is not LLM invocation but reliable
> multi-step agent execution across tools, durable workflows, memory, evidence
> grounding, human review, adversarial reasoning, evaluation, observability, and a
> real Linux deployment.

## 为什么这是一个 Agent Engineering 项目（而不是 CRUD + LLM 调用）

因为下面每一件事都有真实实现、真实测试和真实故障史可讲：

1. **Agent 运行是可持久化、可恢复的**：`agent_runs` + `run_events` + `tool_calls` +
   `model_calls` 四张表记录了完整的运行轨迹；worker 用 lease + `SKIP LOCKED` 抢占，
   崩溃后能重新认领未完成的 run；高危动作通过 interrupt 停在 `waiting_approval`。
2. **工具是被治理的，不是随便调的**：32 个工具（18 核心 + 9 数据库只读 + 5 情报，
   另有动态 MCP 工具）各自声明 `permissions` / `side_effect` / `requires_approval` /
   `risk_level` / `execution_class`；coordinator 只有 22 个工具的权限，分析类工具必须
   委派给 6 个专家 agent。**越权调用会被 runtime 丢弃，而不是被 prompt 劝阻。**
3. **多 Agent 是分层委派而不是数量堆叠**：coordinator → expert（opinion /
   propagation / verification / evidence_critic / report / citation_validator），
   专家产出结构化 artifact，再物化为 Finding；子 run 与父 run 之间有 mailbox。
4. **结论有证据链和人工边界**：Finding 状态机（candidate → under_review →
   verified/rejected/superseded）里，`verified` **只能**由人工评审产生；
   Finding 级对抗评审（Debate）产出的是评审意见，不自动改写状态。
5. **Agent 系统本身是可评测的**：版本化 Golden Dataset（24 任务 / 6 类）+
   Tier A contract eval（scripted model 跑真实 runtime）+ Tier B real-model eval +
   Replay（冻结观察重放 / 种子重跑 + 轨迹 diff）+ 接入现有 Release Gate。

## 五个核心工程能力

| 能力 | 一句话 | 关键证据 |
|---|---|---|
| Agent Harness | 多步骤循环 + 工具治理 + 委派 | `harness/runtime.py`、`harness/tool_factory.py`、`harness/agents.py` |
| Reliability / Durable Workflow | run 可恢复、长任务不阻塞、租约与重试 | `application/graph_worker.py`、`collection_run_worker.py` |
| Grounding / Human-in-the-loop | 数据库工具、证据引用、审批与人工评审边界 | `harness/database_tools.py`、`harness/approval_policy.py` |
| Evaluation / Observability | Golden Dataset、10 个确定性 evaluator、Replay、SLO | `app/evaluation/`、`telemetry/` |
| Deployment / Operations | Linux systemd 部署、平台凭据加密、真实环境排障 | `docs/deploy-tencent-lighthouse.md`、`services/platform_auth.py` |

## 推荐阅读顺序

```text
1. README.md（本文件）        —— 定位与能力地图
2. architecture.md            —— 一张主图看懂系统边界
3. benchmark.md               —— 可复现的评测结果（含诚实性声明）
4. deep-dive-15min.md         —— 四个工程主题的深挖
5. failure-stories.md         —— 5 个真实故障与修复
6. tradeoffs.md               —— 关键设计取舍的问答
7. golden-dataset.md          —— 24 个评测任务清单
8. demo-5min.md               —— 5 分钟演示脚本
```

## 最新评测结果（摘要）

| 套件 | 模式 | 样本量 | 结果 | 上下文 |
|---|---|---|---|---|
| `interview_agent_v1` | contract（scripted） | **24 / 24** | 任务成功率 100%，硬门禁 0 违规 | git SHA 见 [benchmark.md](benchmark.md) |
| `interview_benchmark_v1` | contract（scripted） | **3 / 3** | B1/B2/B3 全通过 | 同上 |

**必读的诚实性声明**：上表全部是 **contract 模式**（scripted model）结果，证明的是
"真实 runtime 的编排合同可测量、可复现、可回归"，**不是** real-model benchmark。
real-model 评测入口已就绪（`--mode real_model`，复用生产 LLM gateway），
但因为当前环境未配置 `LLM_API_KEY`，其 baseline 处于 **BLOCKED**。
详见 [benchmark.md](benchmark.md) 的"诚实性声明"一节。

## 5 分钟 Demo

见 [demo-5min.md](demo-5min.md)（固定脚本，含时间轴与每段要展示的界面/命令）。

## 目录

```text
docs/interview/
├── README.md            本文件：定位、能力地图、阅读顺序
├── architecture.md      Mermaid 主图 + 边界说明
├── benchmark.md         可复现评测结果 + 诚实性声明
├── golden-dataset.md    24 个 Golden Task 清单（自动生成）
├── demo-5min.md         5 分钟演示脚本
├── deep-dive-15min.md   15 分钟深挖（四个主题）
├── failure-stories.md   5 个真实故障与修复
└── tradeoffs.md         关键取舍问答
```
