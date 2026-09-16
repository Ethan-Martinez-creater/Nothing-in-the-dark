# 5 分钟 Demo 脚本

> 目标：5 分钟内让面试官看到"这是一个能被证明的 Agent 系统"，而不是一堆界面。
> 每段都给出**具体要打开的东西**与**要看的那一行数据**。若现场网络不可用，
> 用下面的离线替代方案（跑本地 Tier A + Benchmark，不需要任何 key）。

## 时间轴

### 0:00 – 0:30 问题定义（PPT 或口头，一页）

讲清两件事：

1. **场景**：把散落在多个社交平台上的信息，变成一个**有证据、可追责**的调查结论。
2. **工程问题**：这不是"调用一次大模型"，而是让多步骤 Agent 在真实数据、
   异步任务、权限、证据链和人工边界之间可靠运行。

一句话台词：

> "接下来我展示的不是模型多聪明，而是这套 Agent 系统怎么做到可执行、可追溯、
> 可回归——包括它的评测数字。"

### 0:30 – 1:30 真实 Investigation（前端）

打开：调查列表 → 任一已有调查的**概览页**。

要展示的：

- 调查的采集范围与平台分布（数据来自真实采集或 demo 数据源）；
- **Live Data** 列表：按平台/时间排列的帖子与评论；
- 点开单条帖子：原文、作者、时间、互动量。

台词：

> "这些数据来自采集流水线。注意它是一条异步任务链——采集不会阻塞对话，
> 任务有自己的状态和进度。"

### 1:30 – 2:20 Agent 查询 DB / Tool Trace

打开：该调查的对话，问一句**必须查库**的问题：

```text
这个调查目前有多少条数据？分别来自哪些平台？
```

然后打开**活动 / Run 详情**，展示：

- 这次 run 的工具调用轨迹（`get_case_data_overview` 或 `query_social_posts`）；
- 每一步的参数与耗时；
- 模型调用记录（tokens / 延迟 / 成本——若为 demo 模式则说明）。

台词：

> "它的答案来自数据库工具，不是从上下文里猜的。轨迹是持久化的——
> 这就是评测用的那份数据。"

（接口级替代：`GET /api/v1/runs/{run_id}/trace` 返回 run + tool_calls +
model_calls + approvals + events 的聚合。）

### 2:20 – 3:10 Evidence / Finding

打开：**证据与结论**区域。

要展示的：

- 一条 claim（待核实主张）与挂在它下面的 evidence（支持 / 反驳 / 背景）；
- 一条 Finding 及其状态：`candidate` / `under_review`（**此时还**不是"已验证"）；
- 点开 Finding 的证据链接，说明每条结论都能回溯到具体证据。

台词：

> "结论带着证据链，而且状态是分层的：候选、评审中、已验证。
> **已验证只能由人给**——系统没有别的路径能把它改成 verified。"

### 3:10 – 4:00 Adversarial Debate / Human Review

打开：Finding 详情里的**对抗评审**入口。

要展示的：

- 触发一次对抗评审：系统给出支持与反对这条结论的证据；
- 回到 Review 队列，展示人工评审入口（提交 / 决策 / 状态回写）。

台词：

> "对抗评审不是自动判决器，它是给评审人准备对抗材料的。
> 它不会把 Finding 改成 verified——这是责任边界，不是技术限制。"

（若时间紧，此段可压缩为只展示 Review 队列与状态机说明。）

### 4:00 – 4:40 Agent Eval / Benchmark（本段是差异点，别省）

不打开界面，直接跑命令（提前跑好，现场展示报告）：

```bash
cd backend
uv run python -m app.scripts.run_agent_eval --mode contract --max-tasks 24
uv run python -m app.scripts.run_agent_benchmark --mode contract
```

展示 `report.md` 里的三行：

- `sample size = 24` / `3`（**先亮样本量，再亮百分比**）
- `hard gates PASS`（forbidden tool / case scope / mutation / citation 全 0）
- `git SHA` 与 date

台词：

> "这是 contract 模式：用脚本模型跑**真实 runtime**，所以它验证的是编排合同，
> 每次都一样、不需要 API key，可以放进 PR 门禁。真实模型评测是另一条链路，
> 入口也在，但没有 key 的时候我不会拿 contract 的结果冒充 benchmark。"

### 4:40 – 5:00 关键工程 Tradeoff（收尾）

只讲一个，选最贴合岗位的：

- **权限 vs prompt**：我们评测时发现 12 个任务让 coordinator 调它没权限的工具，
  调用**根本没发生**——因为权限在 runtime，不在提示词里。
- **Debate 不改状态**：因为"已验证"必须有人负责。
- **PR 不调真实模型**：因为门禁必须可复现。

台词收尾：

> "所以这个项目让我真正做的，是把 Agent 从'能跑'推到'可测量、可复现、可回归、
> 可部署'。功能已经冻结，接下来是证明。"

---

## 离线/无网络替代方案

若现场无法访问部署环境：

1. 提前跑好 `run_agent_eval --mode contract` 与 `run_agent_benchmark --mode contract`，
   把 `report.md` 与 `traces/` 一起展示；
2. 用 `backend/tests/` 里的契约测试作为"可执行证据"现场跑一个快的：

```bash
uv run pytest tests/test_agent_eval_evaluators.py -q      # 秒级
uv run pytest tests/test_agent_eval_contract.py -q        # 24 任务全链路（分钟级）
```

3. Replay 演示（不需要模型）：

```bash
uv run pytest tests/test_agent_replay.py -q               # 冻结观察重放 + diff
```

## 常见追问的准备位置

| 追问 | 去看 |
|---|---|
| 怎么知道新版本更好？ | [benchmark.md](benchmark.md) + `AgentEvaluationService.compare_to_baseline` |
| 幻觉怎么管？ | [tradeoffs.md](tradeoffs.md) 第 4 条 + Story 2 |
| 权限怎么保证？ | [architecture.md](architecture.md) 边界一节 + Story 2/3 |
| 为什么不是微服务？ | [tradeoffs.md](tradeoffs.md) 第 2 条 |
| 真实踩过什么坑？ | [failure-stories.md](failure-stories.md) |
