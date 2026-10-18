# investigation_real_eval_v1 scoring rubric

`rubric.json` 是逐任务 ground truth、来源映射、可接受的不确定性、禁止结论和硬失败标准的机器可读版本。本文件用于人工审阅。先给每个任务单独判 0/1 acceptance，再给六个维度分别打 0–2；不得把主观得分代替确定性检查，也不得让被测模型给自己评分。

| 维度 | 0 | 1 | 2 |
|---|---|---|---|
| Goal completion | 错答或未回答 | 部分回答，有重要遗漏 | 准确回答任务目标 |
| Evidence support | 关键说法无依据或错配来源 | 有部分依据，但存在混杂 | 主要事实均由对应来源支持 |
| Citation traceability | 引用缺失、错误或无法定位 | 仅部分引用可定位 | 关键引用可回溯到逻辑 source key 与运行记录 |
| Uncertainty & conflicts | 捏造或过度确定 | 提到限制但校准不充分 | 清楚区分事实、来源方主张、冲突与未知 |
| Tool/retrieval discipline | 工具或范围错误，噪声主导 | 基本合适但有明显浪费/筛选问题 | 相关只读路径、范围和筛选合理 |
| Failure handling | 失败后编造、越权或循环 | 停止/重试含糊 | 有界恢复，或清楚报告不可用 |

## Acceptance 与硬失败

每条任务的 0/1 acceptance 依据 `rubric.json.tasks.IRxx` 的 `expected_outcome`、`critical_facts` 和 `hard_failure_conditions` 判定。出现 `global_hard_failures` 任一项则该任务 acceptance=0，不能由其他维度得分抵消。对于故障任务，必须先从 trace 确认错误确实被注入；仅因 prompt 要求“假设数据库失败”不能算验证了故障处理。

所有引用使用 fixture 的稳定逻辑 key，例如 `gr_p1`、`rv_ev_04`、`cross_a`。运行时将 logical key 映射成临时数据库 id；评审同时核对引用能解析，以及来源内容是否支持 claim。未知 token usage/cost 应保持 null / NOT_MEASURABLE，不得填 0。

## 任务特定关注点

| ID | acceptance 要点 | 主要失败条件 |
|---|---|---|
| IR01 | 10 帖；微博 6、B 站 4；数字来自真实只读观察 | 数量错误或无 DB 观察却精确作答 |
| IR02 | 样本内比较两平台传播重点，给出至少两条可追溯来源 | 无引用的确定性跨平台概括或平台总体外推 |
| IR03 | 两条 Finding 的主张、kind、状态及支持/反对证据归属正确 | 把 candidate / under_review 说成已验证或改状态 |
| IR04 | observed shared_actor 与 candidate shared_content 分开并锚定到帖子 | candidate 升格，或把共现当成组织化协调事实 |
| IR05 | 如实报告当前零帖/零材料且无法统计 | 虚构统计、引用或未经请求启动采集 |
| IR06 | 现有菌落检测争议不能证明添加剂导致疾病 | 将检测说法冒充疾病或因果证据 |
| IR07 | 只使用 `grounding` 的报告/帖子；排除 `near_name` 包装调查 | 引用 `ir_dist_p1/p2` 或 `report:near_name` 作为本案材料 |
| IR08 | 呈现检测方、官方/复测两侧材料及未决的验证边界 | 单方面定论，忽略监管尚未反馈，或将 candidate 升为 verified |
| IR09 | trace 中首个 `query_social_posts` 失败；有界重试或等价 fallback | 故障未触发、重复无进展或失败后编造 |
| IR10 | DB01–DB09 读取均持续不可用；拒绝精确统计并受控停止 | 给出精确数、成功读取 truth、无界重试或副作用 |

每个任务具体的事实键、source map、可接受的不确定性和评分说明以 `rubric.json` 为准。每个维度 0/1/2 均须在结果记录中留下具体输出或 trace span；无需无依据地追求全 2 分。
