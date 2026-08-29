# COIFESP Agent 本地环境交接说明

> 生成时间：2026-08-29。信息来源：本地原项目 `E:\Graduate_work_folder\Agent_develop\Project\COIFESP_Agent\Project` 的只读扫描。
> 用途：在 Nothing_in_the_dark 仓库（GitHub 快照的 clone）中继续开发时，还原本地测试、运行与环境信息。

## 1. 仓库版本关系（最重要）

GitHub 上的 `Nothing-in-the-dark` 仓库**不是本地开发线的下游，而是一次性打包的发布快照**：

| 线 | 位置 | 说明 |
|---|---|---|
| 开发主线 | 本地分支 `feat/agent-run-workspace`，HEAD = `f771b31`（2026-08-28） | 权威开发线，first-parent 136 个提交，含全部数据集与文档 |
| GitHub 快照 | 本地独立分支 `release/github-main` → GitHub `main`，提交 `0fc244b`（2026-08-27 23:58） | 从工作区剔除文档/数据集后打包，与开发线**不共享历史**（非祖先关系） |
| 未提交修改 | 本地工作区 20+ 个已跟踪文件修改 + 3 个未跟踪新文件 | **既不在 GitHub 上，也不在任何提交里**，是最新的一批改动 |

快照相对本地剔除的内容（代码本体基本完整，`backend/tests` 83 个测试文件都在 GitHub 上）：

- `evaluation-datasets/`（981 个 tracked 文件，标注数据集全部）
- `docs/` 下 50 个文档（implementation-plans / manual-smoke-test / crawl-test 等；只保留了 `operations/production-runbook.md`）
- `gui-test-screenshots/`（21 张冒烟截图）、`human_check/pending_items.md`（HITL 台账）
- 根目录 `plan.md`、`new_plan.md`、`remaining.md`、`unimplemented.md`
- `backend/.pytest-full.log`、`backend/.pytest-run.log`、`backend/_run_dev_pg.py`、`backend/_sel_loop.py`、`frontend/e2e-screenshot-*.png`
- `.zcode/plans/` 3 个会话计划

注意 3 个"反向差异"文件：`backend/migrations/versions/20260824_0045_share_download_rate_limit.py`、`backend/scripts/run_full_regression.py`、`backend/scripts/verify_postgres_migrations.py` 在 GitHub 快照里已收录，但在本地仍是**未跟踪**文件（本地工作区有同样的文件，只是没 git add）。

**关于那批"未提交修改"的真相（已验证）**：本地工作区与快照树做过逐文件比对——`backend/app` 14 个核心文件、`backend/scripts`、11 个测试文件、frontend 8 个文件等，**工作区内容与 GitHub 快照树完全一致**。即这批修改（sandbox、external_tools、tool_factory、config、bootstrap、application 层、notifications、media_fetch、models、App.vue 等）在 8-27 打包时已经提交进 `release/github-main`，只是一直**没有 commit 回 `feat/agent-run-workspace`**，所以在 feat 线的 git status 里始终显示为未提交。唯一例外是 `README.md`：快照版是打包时改写的发布版（278 行，定位"社交事件调查的多智能体分析系统"），feat 线保留开发版（414 行详细版），属有意差异。2026-08-29 上述修改已全部 commit 回 feat 线（提交 `18669ed`，40 文件），工作区已干净；落后的是 feat 分支的提交历史，不是代码内容。风险点已解除，最新代码现在受提交保护。

## 2. 运行环境

| 项 | 值 |
|---|---|
| Python | Conda 环境 `bettafish`：`E:\miniconda3\envs\bettafish\python.exe`，**Python 3.11.15**（满足 backend `requires-python >=3.11,<3.14`） |
| Conda 环境全列表 | agent312, agentD, bettafish, boss-mcp, fastapiProject, ppt-master, travel_plan |
| Node / npm | v24.15.0 / 11.12.1 |
| 后端进程 | `backend` 目录下 `E:\miniconda3\envs\bettafish\python.exe -m app.main` → http://localhost:8000/docs |
| 前端进程 | `frontend` 目录下 `npm run dev`（Vite）→ http://localhost:5173，node_modules 本地已装好 |
| ML Worker | 项目根下 `E:\miniconda3\envs\bettafish\python.exe -m uvicorn ml_worker.app:app --host 127.0.0.1 --port 8010`（BGE-M3，懒加载，CUDA OOM 降批/回退 CPU；需在 backend/.env 配 `EMBEDDING_WORKER_URL` 才启用向量检索） |
| 数据库 | 生产用 PostgreSQL 18 + pgvector 0.8.2；默认 SQLite 兜底。迁移：`bettafish python -m alembic upgrade head`（仅 PostgreSQL 有持久化 LangGraph Checkpointer，SQLite 用内存 Checkpointer） |
| 爬虫 | `vendor/MediaCrawler` 源码 + `scripts/apply-mediacrawler-patches.cmd` 打本地补丁；与后端共用 bettafish 环境；真实采集需 `DEMO_MODE=false` + 显式用户确认 |

`backend/.venv` 和 `backend/.test_env`（Python 3.13.12）只是本机试验产物，**不要用**；项目脚本全部默认 bettafish。

## 3. 配置

`backend/.env` 存在（未提交，含密钥，只列键名）：`APP_ENV, APP_DEBUG, DATABASE_URL, CORS_ORIGINS, DEMO_MODE, MEDIACRAWLER_ROOT, MEDIACRAWLER_OUTPUT_ROOT, MEDIACRAWLER_PYTHON_EXECUTABLE, MEDIACRAWLER_ENTRYPOINT, MEDIACRAWLER_LOGIN_TYPE, MEDIACRAWLER_HEADLESS, MEDIACRAWLER_INCLUDE_COMMENTS, MEDIACRAWLER_MAX_COMMENTS_PER_POST, MEDIACRAWLER_TIMEOUT_SECONDS, MEDIACRAWLER_WEIBO_COOKIES, MEDIACRAWLER_BILIBILI_COOKIES, LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY, LLM_FAST_MODEL, LLM_REASONING_MODEL, LLM_REPORT_MODEL`。

未配置的可选键：`EMBEDDING_WORKER_URL`（不配则检索降级为 PG 全文）、`MCP_SERVERS`（只读 MCP 白名单 JSON）、`A2A_REMOTE_URL`（远程 A2A，未部署返回 501）。

LLM：OpenAI-compatible 网关，三路由（fast/reasoning/report），DeepSeek `deepseek-v4-flash` 统一计价（缓存命中输入 ¥0.02/百万、未命中 ¥1/百万、输出 ¥2/百万）。LLM 未配置时 Run 标记 `failed / llm_not_configured`，**不会回退硬编码模板**。配置状态查 `GET /api/v1/system/capabilities`。

根目录 `.env.example` 与上述键基本对应，可作为新环境模板。**续建时密钥需从本地 `backend/.env` 手动复制，不要提交。**

## 4. 测试与验证体系

- 83 个后端测试文件在 `backend/tests/`（覆盖 agent loop、tool system/sandbox、审批 HITL、durable runtime、对齐 alignment、监控、MCP、安全、RAG、 Memory 生命周期、领域算法、爬虫适配等）。
- pytest 配置全部在 `backend/pyproject.toml`：`asyncio_mode=auto`、`testpaths=["tests"]`、**`addopts="--basetemp=.pytest-tmp"`**（本机系统 Temp 的 `pytest-of-PC` 目录权限损坏，必须用项目内 basetemp；pytest 不带参数直接跑即可）。
- 静态检查：`bettafish python -m ruff check .`（line-length 100，py311，migrations 排除）；mypy strict 已配置。
- 前端：`npm run typecheck`（vue-tsc）、`npm run test`（vitest）、`npm run lint`、`npm run build`；E2E：`npm run e2e:smoke` / `e2e:interact`（Playwright，11 路由冒烟曾全过）。
- 一键回归：`backend/scripts/run_full_regression.py`（未跟踪新文件，一条命令跑后端+前端，E2E 需 `--e2e`）；历史运行日志在 `backend/test-results/full-regression-20260824.*.log`。
- 冒烟/验收脚本（`backend/scripts/`，多为真实 PG 环境验收）：
  - `smoke_expert_agents.py`（六专家 Agent 全链路）、`smoke_mcp_server.py`（只读 MCP 7 项）、`smoke_phase1_claims_evidence.py`、`smoke_rag_extended.py`、`smoke_real_crawler.py`（有界真实爬虫）、`smoke_platform.py`、`smoke_llm_runtime.py`、`smoke_llm_followup.py`
  - `verify_durable_recovery.py`（PG 中断恢复）、`verify_crawl_cancel.py`（取消杀死爬虫子进程）、`verify_embedding_worker.py`（BGE-M3）、`verify_postgres_migrations.py`（一次性测试库迁移链，拒绝生产库名）
  - 其他：`backfill_embeddings.py`、`backfill_fact_check_claims.py`、`backfill_model_costs.py`、`run_domain_eval.py`（领域算法回归门禁，低于阈值 exit 1）、`cleanup_test_artifacts.py`（默认只报告测试残留，`--file` 逐个删）、`formal_crawl_test.py`、`inspect_login_page.py`、`mediacrawler_entry.py`
- 本地无 `conftest.py`、无 pytest.ini，需要 fixture 时在测试文件内自建（现有测试均如此）。

## 5. 运行方式补充

- Windows 启动脚本（根 `scripts/`）：`setup-backend.cmd`（可用 `COIFESP_PYTHON` 覆盖解释器）、`setup-frontend.cmd`（npm cache 指向项目内 `.npm-cache`）、`dev-backend.cmd`、`dev-frontend.cmd`、`apply-mediacrawler-patches.cmd`。
- 其余 34 个 `r2_tmp01..34_*.py` 是数据集第 2/3 轮标注的一次性治理脚本（SHA/共识/PII 扫描/manifest 校验），非服务代码。
- 真实采集约束：每组关键词最多 600 条候选、每平台最多 3 组；JSONL 运行目录上限 `MEDIACRAWLER_MAX_OUTPUT_RUNS=100`，满了系统拒绝采集并要求人工逐个清理（不自动批量删除）。Cookie 只放未提交的 `.env`，经子进程环境变量传递。
- MediaCrawler 许可证：NON-COMMERCIAL LEARNING LICENSE 1.1，仅限非商业学习研究。

## 6. 项目当前真实进度（截至 2026-08-28）

四份历史账本文档（本地根目录，均在 GitHub 快照之外）**已全部清零**：

- `plan.md`：初版总体设计方案 v0.1（设计基线，非待办）。
- `new_plan.md`：M1–M11 冲刺全部 ✅（2026-08-08），含 T0–T12 手动端到端冒烟。
- `remaining.md`：差距审计，条目基本完成，剩余移交下一份。
- `unimplemented.md`：P0-1.1~P0-1.5、P1-2.1~2.2、F-3.1~3.3、E-4 共 11 大项全部 `[x]`；"明确不做"清单含远程 A2A、7×24 监控、自动发帖、多租户等。

**真正进行中的两条线**（未体现在上述文档，是续建的出发点）：

1. **17 项必备能力实施**（2026-08-20 起）：计划在本地 `docs/implementation-plans/2026-08-20-required-capabilities/`（01–23 编号文档：持续监控预警、多模态管线、跨平台对齐、垃圾/水军协同检测、不确定性、HITL 工作台、叙事生命周期、中文跨语际语义等），已有实现报告（`2026-08-20-implementation-report/`）、两轮验收（round-01 存在 unfixable items 清单、round-02 进度）与安全审计整改记录。
2. **评测数据集构建**（`evaluation-datasets/social-investigation-2026-03_to_2026-08-v1/`，1129 个文件）：真实采集 36 事件 6,147 条内容，双盲 A/B 标注→仲裁→共识→SHA-256 校验链；批次 1–10 共 2,500 条正式标注完成；**当前卡点：批次 10 R3 工程整改已完成待用户复审**（`human_check/pending_items.md` 最后一节；R3 报告 SHA 61DD12BE；批次 11 盲包已冻结、dry-run 13/13 通过；批次 11 与十万合成数据被硬性暂停，须复审通过才继续）。

人工确认台账：`human_check/pending_items.md`（平台授权、标注规范、批次 2–9 复审均已闭环）。

验收工件：`artifacts/validation/` 9 份平台冒烟记录（weibo/bilibili/tieba/zhihu/douyin/llm 20260730 + tieba/zhihu/douyin 复测 20260817）；`gui-test-screenshots/` 21 张 T0–T12 冒烟证据；`docs/manual-smoke-test.md` 手工用例清单。

## 7. 本地可清理项（测试残留，与业务无关）

- 根目录 16 个 `coifesp-job-*` / `coifesp-align-*` / `coifesp-hitl-*` / `coifesp-monitor-*` 目录：是 pytest 的 `test.db`（SQLite）残留——`test_analysis_jobs.py` 等测试用 atexit 清理，进程被强杀时留下。新目录无需迁移。
- `.tmp_batch10_expanded.txt`、`_tmp_batch10_compact.jsonl`、`_tmp_batch10_pretty.json`：批次 10 盲样预览中间产物（已脱敏）。
- 空目录：`.annotator_b_tmp`、`.pytest-basetemp-m1`、`tmpls81n7er`。
- `.claude/settings.local.json`（工具权限白名单）、`.zcode/plans/`、`.superpowers/`：AI 工具会话残留。

## 8. 在 Nothing_in_the_dark 目录继续开发的注意事项

1. **代码基线已是最新**：当前 clone 的代码内容 = 本地最新工作区状态（已验证一致），不存在代码落后；缺的只是数据集/文档/HITL 台账等资产（留在本地）。可放心以本目录为开发现场，或维持"本地开发 + 此仓库发布镜像"流程；若继续本地开发，应先把工作区修改 commit 回 `feat/agent-run-workspace`（目前最新代码未受提交保护）。
2. **密钥不迁移**：`backend/.env` 手动复制，禁止提交。
3. **环境固定**：一律用 bettafish 解释器；pytest 已固定 basetemp，直接 `python -m pytest` 即可；不要往 `agentD` 环境装本项目依赖（agentD 是学习工程环境，本项目独立用 bettafish）。
4. **vendor 补丁**：MediaCrawler 需先执行 `scripts/apply-mediacrawler-patches.cmd`，否则上游"低于分页上限自动扩量"的缺陷会回来。
5. **删除纪律**：本地约定禁止批量删除（`rm -rf` 等），测试残留用 `cleanup_test_artifacts.py --file` 逐个处理。
6. **数据集与文档资产**：全部留在本地 `COIFESP_Agent/Project`，GitHub 仓库刻意不含；续建时如需读取验收记录、HITL 台账、标注规范，回本地目录查。
