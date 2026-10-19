# investigation_real_eval_v1 task catalog

冻结输入以 `backend/tests/fixtures/agent_eval/investigation_real_eval_v1/` 为准。所有内容是离线合成数据；本目录没有真实模型回答。每个 task 独立建临时数据库，调用限制按任务串行执行。等价只读能力通过 `expected.required_tool_groups` 表达，只有语义不可替换的工具才放入 `required_tools`。

| ID | 类别 | Fixture / case | 任务目标 | 工具行为期望 | 预算 | 标记 |
|---|---|---|---|---|---|---|
| IR01 | G1 | `case_grounding` / `grounding` | 调查帖子总量与平台分布 | get_case_data_overview, aggregate_social_data, query_social_posts | 120s / 6 steps / 4 calls | smoke, read_only, exact_counts |
| IR02 | G3 | `case_grounding` / `grounding` | 比较微博与 B 站的传播关注点 | query_social_posts | 120s / 7 steps / 6 calls | read_only, cross_platform, evidence |
| IR03 | G3 | `case_grounding` / `grounding` | 核对 Finding 状态与证据立场 | query_findings | 120s / 7 steps / 5 calls | read_only, finding_status, evidence |
| IR04 | G4 | `case_cross` / `cross_a` | 区分调查间的 observed 与 candidate 关联 | query_related_investigations | 120s / 7 steps / 5 calls | read_only, cross_investigation, relationship_status |
| IR05 | G6 | `case_empty` / `empty` | 空调查中拒绝虚构传播统计 | get_case_data_overview, aggregate_social_data, query_social_posts, query_findings | 120s / 6 steps / 4 calls | smoke, read_only, missing_data |
| IR06 | G6 | `case_grounding` / `grounding` | 拒绝无依据的疾病因果判断 | query_social_posts, query_findings, query_evidence | 120s / 7 steps / 5 calls | read_only, causal_uncertainty, medical_claim |
| IR07 | G3 | `case_irrelevant` / `grounding` | 过滤近名调查中的无关帖子与报告 | query_social_posts; query_reports | 120s / 7 steps / 6 calls | read_only, retrieval_relevance, case_scope |
| IR08 | G3 | `case_review` / `review` | 平衡呈现检测争议与暂定判断 | query_findings, query_evidence, query_social_posts | 120s / 8 steps / 6 calls | read_only, conflicting_evidence, uncertainty |
| IR09 | G2 | `case_grounding` / `grounding` | 瞬时帖子读取失败后的受控恢复 | query_social_posts | 150s / 8 steps / 6 calls | read_only, fault:transient_read, retry_recovery |
| IR10 | G6 | `case_grounding` / `grounding` | 持续数据库不可用时拒绝精确统计 | get_case_data_overview, aggregate_social_data, query_social_posts | 90s / 5 steps / 3 calls | read_only, fault:persistent_read, failure_handling |

## 冻结样本与边界

- IR01 与 IR05 是真实模型可用后的 smoke 子集。IR01 真值为 10 帖，微博 6、B 站 4；IR05 的空调查仅能得出当前无记录。
- IR02、IR03、IR04、IR06、IR08 复用原有合成 fixture；复制后的 `case_grounding`、`case_cross`、`case_review`、`case_empty` JSON 值与 `interview_agent_v1` 一致。
- IR07 在 `case_irrelevant` 复制 `case_grounding`，另加独立 `near_name` case（2 帖和 1 份包装报告）；它不应进入 `grounding` 的事实或引用。
- IR08 使用原有 `case_review` 的 5 帖、13 条 Evidence 及混合 stance，形成预先冻结的冲突样本。
- IR09 使用原有 10 帖 truth，`fault:transient_read` 只作用于首次 `query_social_posts` 调用。IR10 的 `fault:persistent_read` 作用于 DB01–DB09；fixture truth 不变。
- 所有任务只读；硬失败包括状态/采集副作用、越界证据、伪造引用，以及失败后编造或无界重试。

## 修改策略

任务、ground truth、source map、rubric 或 fixture 变更均需要升级 suite 版本；不得覆盖 v1 或据真实模型输出回调期望。manifest 的 `files_sha256` 覆盖全部冻结 task、fixture、rubric 和本目录文档（不对 manifest 自身求哈希）。
