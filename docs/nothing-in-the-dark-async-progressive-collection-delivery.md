# 异步渐进式采集优化 — 交付记录

> 对应执行方案：`docs/nothing-in-the-dark-async-progressive-collection-final-plan.md`
> 执行基线：`b680255`；本交付在 `main` 分支以 4 个 commit 落地（见第 7 节）。

---

## 1. 目标与结果

把「同步长 Tool Call 的采集」改为「后台异步、渐进式、可恢复的 CollectionRun 模型」：

```text
用户请求采集 → Approval → 立即返回 collection_run_id → AgentRun 结束
后台 CollectionRunWorker 领取执行 → 平台并发采集 → 每完成一个平台立即过滤去重入库
→ Live Data 渐进出现 partial data → 全部完成（或部分失败/取消）→ 终态
```

**核心判定（INV-3 / H06+H07）**：`AgentRun.status = completed` 与
`CollectionRun.status = running` 可同时成立，ChatInput 保持可用。

## 2. 交付内容（按 AC 步骤）

| AC | 内容 | 落地文件 |
| --- | --- | --- |
| AC1 | CollectionRun domain contract（状态机 / snapshot / fingerprint / progress schema） | `models.py` `CollectionRunRecord` |
| AC2 | Migration 0050 + Repository（lease / heartbeat / fencing / recovery） | `migrations/versions/20260901_0050_collection_runs.py`、`collection_run_repository.py` |
| AC3 | CollectionRunService（exact snapshot / Discovery-Deep budget / fingerprint / 幂等） | `application/collection_run_service.py` |
| AC4 | Approval scope 扩展（start_social_collection 继承 Crawl 安全边界，含 phase/include_comments 比较） | `harness/approval_policy.py`、`harness/runtime.py`、`harness/tools.py` |
| AC5 | CrawlRequest 扩展（upstream_limit_per_platform / include_comments，legacy defaults 不变） | `ports/crawler.py`、`harness/external_tools.py` |
| AC6 | MediaCrawler Adapter：一平台一进程多关键词、aggregate cap、真正关闭评论 | `infrastructure/crawler/mediacrawler.py` |
| AC7 | Sandbox CollectionPlatformExecutor（run 内平台并发） | `harness/collection_platform_executor.py` |
| AC8 | Global CrawlCapacityLimiter（MEDIACRAWLER_GLOBAL_CONCURRENCY=2） | `application/collection_capacity.py`、`core/config.py` |
| AC9 | CollectionRunWorker（heartbeat / bounded concurrency / single-writer ingest / recovery / retry / cancel / shutdown） | `application/collection_run_worker.py` |
| AC10 | Agent Tool Migration：start_social_collection + get_collection_run、Coordinator allowlist、Skill | `harness/tool_factory.py`、`harness/agents.py`、`skills/social-crawl/SKILL.md` |
| AC11 | CollectionRun API（list / get / cancel，Case scope） | `api/routes/collection_runs.py`、`api/router.py`、`schemas/collection_runs.py` |
| AC12 | Frontend：CollectionRunCard、Overview 轮询、Live Data 渐进刷新 | `components/collection/CollectionRunCard.vue`、`views/investigation/InvestigationOverviewView.vue`、`InvestigationLiveDataView.vue`、`services/api/collectionRuns.ts` |
| — | Observability（collection.* 指标 + phase/attempt 标签） | `telemetry/metrics.py` |

## 3. 关键实现语义

- **Immutable Snapshot（INV-1）**：`request_json` 在审批后冻结 definition id/version、
  phase、platforms、time_range、keywords、exclusions、filters、budget；Worker 只读
  snapshot，不重新读取 Active Definition。`request_fingerprint` = canonical payload
  的 SHA256，Active Equivalent（同 case + 同 fingerprint + queued/running）返回已有 run。
- **Tool retry 幂等（文档 24 节）**：`idempotency_key = "tool-call:<tool_call_id>"`，
  同一个 Tool Call 重试只产生一个 CollectionRun（`UNIQUE(case_id, idempotency_key)`）。
- **Lease / fencing（INV-2）**：所有运行中写方法 `WHERE id = ... AND lease_owner =
  worker_id`；独立 heartbeat task（lease/3 间隔）续租并检查取消；丢租即
  `cancel_event` 触发，终止 MediaCrawler 子进程树。
- **Recovery（文档 29 节）**：claim_next 可领取 queued 或租约过期的 running run；
  平台 checkpoint 恢复：completed → skip，failed 且达到上限 → 保持 failed，其余重新执行。
- **Single-writer ingest（文档 39/40 节）**：平台任务只做浏览器 I/O，结果经
  asyncio.Queue 交给 coordinator 顺序完成 过滤 → 排除词 → 覆盖采样 → persist →
  checkpoint → 重算 totals；`posts_collected` 始终由 completed 平台求和，禁止 `+=`。
- **Discovery/Deep budget（文档 20/21 节）**：Discovery = 不抓评论、
  per_day_limit=30、upstream = min(max(days×10,60),150)；Deep = 显式动作、重新
  Approval、抓评论（≤10）、upstream = min(max(days×30,150),600)。
- **Cancel（文档 44 节）**：cancel_requested_at 非终态记录；已完成数据保留。

## 4. 测试结果

> 以下数据为本轮执行时实际运行结果。

| 测试批次 | 结果 |
| --- | --- |
| 新增 `test_collection_runs.py`（CR01-20/AP/CW） | 22 passed |
| 新增 `test_collection_adapter.py`（MC01/03/04/07/09/10/11） | 7 passed |
| 新增 `test_collection_harness.py`（H01-05） | 5 passed |
| 新增 `test_collection_migration.py`（upgrade/downgrade/upgrade） | 1 passed |
| 既有相邻回归（collection_definitions / collection_tool_integration / crawl_cancel / crawl_coverage / approval_hitl / analysis_jobs / tool_sandbox / tool_registry / tool_system / agent_runtime / durable_runtime / expert_agents / social_repository / posts / case_deletion / monitoring / production_entry / api） | 100+ passed（durable_runtime 3 个审批流测试按新架构更新后全绿；expert 1 个 SQLite 偶发锁重跑通过） |
| 前端 typecheck / lint / build | 通过 |
| 前端 vitest | 151 passed（25 files） |

## 5. 已知限制与说明

- **Alembic 全链在 SQLite 不可执行**：早期 migration（0002/0003/0004 等）含
  PostgreSQL 专用语句（`CREATE EXTENSION vector`）。项目生产用
  `Base.metadata.create_all` 幂等建表；migration 0050 的 upgrade/downgrade 逻辑
  已通过 Alembic Operations 单测验证（`test_collection_migration.py`）。PG 部署
  需用项目既有 PG migration verifier 校验全链。
- **阶段性分析按钮**：Card 上「分析已有数据 / 基于当前采集结果继续分析」将提示词
  复制到剪贴板并提示粘贴到 Copilot 发送（生成正常 User Turn，非后台隐式执行）；
  未做 Copilot 输入框自动填充。
- **Browser E2E（文档 67 节 E2E-A~F）** 由真实采集验收（第 6 节）在真实系统上
  覆盖核心场景。

## 6. 真实采集验收记录（AC15）

> 华为「竹知了」案例（2026-08-10 ~ 08-20 窗口）实测记录。两次触发：
> ①历史遗留 run 恢复触发的 deep 5 平台 run（已取消）；②受控 discovery
> 3 平台 run（本表主记录）。所有时间均为 Asia/Shanghai。

```text
CASE_ID:            75ebcfae-2b55-4485-918f-00397a1b6fde
COLLECTION_RUN_ID:  86fd141f（受控验收 run；c98c1fa5 deep / eab863c5 discovery 为验证 cancel 用）
T0 user request:    13:29:06
T1 approval shown:  N/A（approve_crawl=true 预批准，跳过审批中断）
T2 approved:        N/A
T3 start tool returned:    13:29 内（Agent 调用 start_social_collection 秒级创建 run）
T4 AgentRun completed:     13:29:59（实际 failed：LLM 上游 HTTP 400；但 CollectionRun 已创建
                            并持续后台运行——INV-3 生命周期解耦的强验证）
T5 first platform completed:  ~13:44（zhihu，36 条）
T6 first SourcePost visible:  ~13:44（GET /posts 立即出现 36 条 zhihu 帖子，采集未结束）
T7 CollectionRun terminal:    13:54（completed_with_errors）
posts/platform:     zhihu 36；weibo 0（failed att2）；bilibili 0（failed att2）
failed platforms:   weibo, bilibili（真实平台/登录环境问题，管线正确隔离并报告细则）
retry count:        2（weibo/bilibili 各延迟重试 1 次后仍失败）
max browser processes:  2（MEDIACRAWLER_GLOBAL_CONCURRENCY=2 生效）
```

实测同时确认的关键行为：

- **一平台一进程多关键词**：weibo 命令 `--keywords "竹知了,华为 竹知了,余承东 竹知了,鸿蒙智行 竹知了,起底竹知了事件背后黑手"` 单进程执行。
- **Discovery 评论真正关闭**：命令 `--get_comment false`、`--crawler_max_notes_count 110`（aggregate 预算）。
- **渐进式数据（INV-4 / E2E-B）**：zhihu 平台完成即入库，Live Data 立即可见，其余平台仍在后台采集。
- **平台隔离 + 部分成功保留（E2E-E）**：weibo/bilibili 失败不影响 zhihu 的 36 条数据。
- **Cancel（E2E-D）**：对运行中 run 请求取消 → cancel_requested_at 落库 → worker 心跳拾取 → 终态 cancelled；重启后 recover_expired 收敛残留。
- **Adapter 部分成功修复（MC10）**：进程非零退出但已产出数据时保留（真实采集发现 weibo 写完 456 行后异常退出，此前会整平台丢弃，已修复并补单测）。

## 7. Commit 顺序（按执行方案 74 节）

1. `feat: add durable collection run lifecycle`（model / migration / repository /
   service / lease / heartbeat / recovery / API）
2. `perf: optimize mediacrawler discovery execution`（one process/platform、
   multi keywords、aggregate budget、comments off、global capacity、incremental ingest）
3. `feat: decouple social collection from agent turns`（start tool、approval scope、
   Coordinator / Skill、CollectionRunCard、Live Data refresh、ChatInput decoupling）
4. `docs: finalize async progressive collection optimization`（本文档）
