# Tradeoffs — 关键设计取舍问答

> 面试里最容易被追问的七个"为什么不用 X"。回答都以当前实现为依据，
> 并明确指出代价，而不是只讲好处。

---

## 1. 为什么不用更多 Agent？

**现状**：1 个 coordinator + 6 个专家（opinion / propagation / verification /
evidence_critic / report / citation_validator）。

**理由**
专家不是按"显得智能"分的，而是按**职责与权限**分的：每个专家有独立的工具白名单
（例如只有 verification 能调 `verify_claims` + `query_claims` + `query_evidence`，
只有 report 能调 `build_report`）。coordinator 自己的工具集只有 22 个，**看不到**
分析类工具——这个"能力窄化"是刻意的：它能减少模型在 30+ 工具里的选择噪声，
也让"谁有权做什么"变成代码里的事实，而不是文档里的承诺。

**代价**：多一次委派就多一轮模型调用与一次子 run（延迟与成本上升）。
我们的缓解是让委派同步等待子 run 并复用同一个 gateway 与 tool registry，
而不是再叠一层消息总线。

**什么时候该加 Agent**：当出现**新的权限边界或新的产出物类型**时。
"让 Agent 再多想想"不是加 Agent 的理由，那是 prompt 或模型选择问题。

---

## 2. 为什么不用微服务？

**现状**：单个 FastAPI 进程承载 API + HTTP 路由；`GraphWorker` 在同一进程的事件循环里
以租约抢占 run；采集由独立的 durable CollectionRun 承载；ML 能力（embedding /
sentiment）通过可选 worker URL 调用，不可用时降级。

**理由**
这个系统的耦合点是**数据模型与事务边界**（一个 run 的状态、一条 Finding 的证据链接
必须原子），不是部署单元。拆成微服务会立刻引入分布式事务与跨服务一致性问题，
而当前的真实瓶颈不在这里：4 核 8G 的 ECS 上实测应用负载很轻（服务空转时内存
占用几百 MB）。

**代价**：单进程内的 CPU 密集任务（如本地 embedding）会与 API 争资源。
我们的处理是把它们放进独立子进程（沙箱执行器）或降级为可选 worker，
而不是拆服务。

**什么时候该拆**：当出现**独立的扩缩容需求**（例如采集需要 10 倍算力而 API 不需要）
或**独立的失败域**要求时。目前都没到。

---

## 3. 为什么 PR Eval 不调用真实模型？

**现状**：PR CI 跑 **Tier A contract eval**（scripted model + 真实 runtime），
真实模型评测（Tier B）只在 manual / nightly 触发。

**理由**
三条硬约束：

1. **成本与不确定性**：PR 每次提交都调 24 个任务的模型，成本不可控、结果还会因为
   模型非确定性而抖动，人就会开始忽略红灯；
2. **凭据**：PR 来自 fork 时不该接触生产 key，而 PR 门禁"必须能跑"是更重要的属性；
3. **职责分离**：PR 该拦的是**编排合同被破坏**（比如有人改了工具权限、参数 schema、
   approval 策略），这类回归用 scripted model 就能 100% 稳定复现，
   不需要模型参与。

**代价**：PR 绿灯**不代表模型回答质量没退化**。我们用两条措施补：Tier B 定时跑并
记录 baseline，以及评测里显式标注 Tier A 的 limitations（见
[benchmark.md](benchmark.md) 的诚实性声明）。

---

## 4. 为什么 Agent Judge 不能判 Tool correctness？

**现状**：`task_success` / `required_tool_coverage` / `forbidden_tool_violations` /
`tool_argument_accuracy` / `state_mutation` / `citation_validity` / `case_scope`
全部是**确定性纯函数**（`app/evaluation/agent_evaluators.py`，E1–E10），
不经过任何 LLM。

**理由**
这些问题是**可判定的**，用 LLM 判等于把确定性换成概率：

- "工具是否被调用"——查 `tool_calls` 表就能回答；
- "参数是否符合 schema"——用工具真实的 `input_model` 校验；
- "case 是否越界"——比较参数里的 `case_id` 与期望值；
- "引用是否存在"——拿 id 去库里查。

用 LLM 判这些，会引入一个**不可复现的失败源**：同一次运行换个模型或换次采样，
门禁结论就变了。而发布门禁必须可复现。

**LLM Judge 的合法位置**：`answer_relevance` / `answer_completeness` /
`uncertainty_calibration` 这类语义质量指标——它们本来就没有确定性判据。
即便如此也必须记录 `judge_model` 与 `judge_prompt_version`，
且第一版默认关闭。

---

## 5. 为什么 Debate 不自动改变 Finding？

**现状**：Finding 状态机 `candidate → under_review → verified/rejected/superseded`，
其中 `verified` / `rejected` **只能**由人工评审决策产生（`REVIEW_ONLY_STATUSES`），
代码层面拒绝其他路径（越权会报 `finding_review_required`）。对抗评审（Debate）
产出的是证据对抗与评审意见，不写状态。

**理由**
这是一个**责任边界**，不是技术能力问题。如果对抗评审能自动把结论升级为
"已验证"，那么系统对外输出的每一句"已核实"背后都没有可追责的人。
在一个面向真实事件调查的场景里，这个边界比"自动化程度看起来更高"重要得多。

**代价**：人工评审成为吞吐瓶颈。我们的处理是把评审做得轻量
（`submit_review_item` 一键提交 + 队列 + 状态回写），而不是取消它。

**补充**：Debate 的价值因此被重新定位——它不是"自动判决器"，
而是**给评审人准备对抗材料**的机器（见 failure-stories Story 5）。

---

## 6. 为什么实时社交数据不进入 Golden Dataset？

**现状**：Golden Dataset 与 Benchmark 全部使用**冻结的合成 fixture**
（虚构品牌/账号/数字），`test_fixture_has_no_live_network_dependency`
断言 fixture 里不出现任何真实平台 URL。真实平台只作为 manual live smoke。

**理由**
如果把实时采集放进评测输入，评测会同时依赖：平台风控状态、Cookie 有效期、
IP 归属地、目标内容是否还在线、网络抖动。这些都不是我们代码的属性，
却会让分数每天变。**评测要测的是我们的系统，不是平台的脸色。**

另外，真实数据往往涉及真实主体，把它固化进仓库也不合适。

**代价**：fixture 的分布与真实数据有差距（我们只有 2–3 个平台、几十条帖子）。
结论是：Benchmark 能证明**编排与 grounding 行为**，不能证明**线上真实分布下的效果**。
后者由 manual live smoke 与真实使用来覆盖，并且在文档里明确标注。

---

## 7. 为什么工具权限比 prompt 约束更可靠？

**现状**：工具的 `permissions` / `side_effect` / `requires_approval` /
`risk_level` / `execution_class` 是 `ToolSpec` 的**声明式字段**，
由 `ApprovalPolicyEngine` 在运行时判定；coordinator 与各专家的 `allowed_tools`
是硬编码白名单，**越权调用在 runtime 层被丢弃**——不是靠提示词劝阻。

**理由**
这是评测里被真实印证的一次：我们在写 Golden Dataset 时，最初有 12 个任务让
coordinator 直接调用 `verify_claims` / `build_report` / `query_claims` 等工具。
结果这些调用**根本没有发生**——runtime 把它们丢掉了，因为 coordinator 没有权限。
（修复过程见 [interview-readiness-delivery.md](../interview-readiness-delivery.md)
的 Phase 2"实施中发现的真实架构约束"。）

换句话说：**如果只写在 prompt 里，评测根本抓不到这种越权**；
正因为它是代码里的权限，评测一跑就暴露了。这就是"权限 > prompt"的实证。

**代价**：新增工具时要同时维护白名单与权限声明，开发体验更啰嗦；
有些"本可以顺手做"的能力会被拒绝。我们认为这是应该付的代价——
安全属性不该依赖模型的自觉。

---

## 附：一句话总结这些取舍的共同逻辑

> **复杂度必须服务于实际可靠性，而不是服务于 Agent 的数量或架构的时髦程度。**
