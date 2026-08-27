# COIFESP Agent

COIFESP Agent 是一个面向社交事件调查的多智能体分析系统。系统能够围绕指定主题，
从多个社交平台采集和归一化数据，在可追溯证据基础上开展多轮对话、跨平台对比、
传播分析、事实核查和多角色辩论，并将运行轨迹、审批过程与分析成果持久化。

本仓库是可运行的系统发布快照，包含应用源码、数据库迁移、自动化测试、运维脚本
和必要文档；不包含本地密钥、真实采集数据、数据集构建工作区或实现过程记录。

## 已实现能力

### 社交数据采集与归一化

- 支持微博、哔哩哔哩、百度贴吧、知乎和抖音的统一采集接口。
- 默认提供无外部账号依赖的演示采集器，便于本地体验和自动化测试。
- 真实采集通过隔离的 MediaCrawler 子进程执行，支持登录方式、评论数量、超时、
  时间范围和采集额度配置。
- 对帖子、评论、互动、发布时间、平台来源和媒体元数据进行统一建模、去重和覆盖统计。
- 采集动作需要显式确认，超出授权范围或缺少秘密时按 fail-closed 方式拒绝执行。

### Harness 与多智能体协作

- 使用 LangGraph 编排可恢复的分析流程。
- Coordinator 根据任务动态调用意见研究、传播复原、事实核查、证据审查、报告生成
  和引用校验等专家 Agent。
- 支持父子 Run、租约、运行中指令、取消传播、结构化重试、预算控制和持久化事件流。
- Tool Registry、Skill Registry、Hook Bus、上下文构建器和模型网关均具有明确边界。
- 提供只读 MCP Client/Server 与本地 A2A 兼容边界。

### 证据化分析与多轮对话

- 支持主张抽取、证据链、事实核查、意见聚类、情感/立场分析和传播源头候选分析。
- PostgreSQL 模式支持全文检索与 pgvector 混合检索；ML Worker 不可用时安全降级。
- Memory 支持作用域隔离、去重、修订、冲突、衰减、失效和访问审计。
- 对话可引用运行产物继续追问，并通过 SSE 展示 Agent、模型和工具的实时运行轨迹。
- 报告支持版本化、重新生成、引用覆盖检查以及 HTML/Markdown 导出。

### 跨平台、多模态与辩论

- 提供跨平台参与度、情感分布、时间线和话题词对齐可视化。
- 支持媒体抓取、类型检测、特征提取和失败/取消语义；网络请求包含 SSRF、DNS
  rebinding 和重定向防护。
- 支持多平台角色陈述、反驳、投票和主持人总结，输出可回溯至各平台证据。
- 提供传播完整性、垃圾内容、机器人协同行为、叙事生命周期、语义标注和不确定性面板。

### 治理、安全与可靠性

- Human-in-the-loop 审批使用一次性授权，参数哈希绑定，并支持崩溃恢复后的幂等复用。
- 工具沙箱支持受限进程和容器执行器，包含环境变量白名单、输出上限、超时、取消、
  只读根文件系统、cap-drop 与强网络隔离要求。
- 不可信内容在进入模型、工具和报告链路前经过策略评估与审计。
- 提供订阅通知、签名 Webhook、分享下载配额与数据库原子限流。
- 提供健康矩阵、熔断器、队列、死信、Kill Switch、事故处置与可观测性/SLO 页面。
- 支持 OpenTelemetry HTTP 导出，并对日志、Trace、Artifact 和秘密引用进行分层脱敏。

## 技术架构

```text
Vue 3 + TypeScript 工作台
            │ HTTP / SSE
            ▼
FastAPI API 与应用服务
            │
            ▼
LangGraph Harness Runtime
  ├─ Coordinator / Expert Agents
  ├─ Tool / Skill / Hook / Approval
  ├─ Memory / Context / Hybrid RAG
  └─ Artifact / Event / Audit
            │
            ▼
PostgreSQL + pgvector / SQLite（开发）
            │
            ├─ MediaCrawler Adapter
            ├─ OpenAI-compatible LLM Gateway
            ├─ Embedding ML Worker
            └─ MCP / A2A / OTLP / Webhook
```

后端采用 API → Application → Domain/Harness → Infrastructure 的依赖方向，领域服务不依赖
FastAPI 或 Vue。SQLite 用于降低本地启动门槛；生产环境使用 PostgreSQL 与完整 Alembic
迁移链。

## 仓库内容

```text
.
├─ backend/
│  ├─ app/                 # API、应用服务、Harness、领域服务和基础设施适配器
│  ├─ migrations/          # Alembic 迁移链
│  ├─ scripts/             # 回填、验证、冒烟、迁移验证和全量回归脚本
│  ├─ skills/              # 系统内置 SKILL.md
│  ├─ tests/               # 后端单元、集成、安全和恢复测试
│  ├─ alembic.ini
│  └─ pyproject.toml
├─ frontend/
│  ├─ src/                 # Vue 工作台、治理页面和可视化组件
│  ├─ e2e-smoke.cjs        # 路由级浏览器冒烟
│  ├─ e2e-interact.cjs     # 交互级浏览器冒烟
│  ├─ package.json
│  └─ vite.config.ts
├─ ml_worker/              # 可选的本地 Embedding Worker
├─ scripts/                # Windows 安装、启动和 MediaCrawler 补丁脚本
├─ vendor/                 # 外部 MediaCrawler 接入说明与本地补丁
├─ docs/operations/        # 生产运行手册
├─ artifacts/.gitkeep      # 运行产物目录占位
├─ .env.example            # 无秘密的配置模板
├─ .editorconfig
└─ .gitignore
```

测试源码属于发布项目的一部分，用于验证数据库、授权、沙箱、恢复、前端交互和 API 契约。
测试生成的日志、截图、临时数据库和运行目录不进入仓库。

## 环境要求

- Python 3.11 或更高版本。
- Node.js 20 或更高版本。
- 本地快速体验可使用 SQLite。
- 生产环境需要 PostgreSQL，并建议安装 pgvector 扩展。
- 真实社交平台采集需要按 `vendor/README.md` 准备 MediaCrawler。
- 生产工具执行需要容器运行时、egress-only 网络和容器内可达的白名单代理。

## 本地快速启动

项目提供 Windows 脚本。默认 Python 路径可通过 `COIFESP_PYTHON` 覆盖。

```bat
scripts\setup-backend.cmd
scripts\setup-frontend.cmd
```

复制配置模板并仅在本地填写秘密：

```bat
copy .env.example backend\.env
copy frontend\.env.example frontend\.env
```

启动后端和前端：

```bat
scripts\dev-backend.cmd
scripts\dev-frontend.cmd
```

- 工作台：http://localhost:5173
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/api/v1/health

默认 `DEMO_MODE=true`，不会登录或抓取真实社交平台。

## LLM 与检索配置

在 `backend/.env` 中配置 OpenAI-compatible 模型网关：

```env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-gateway.example/v1
LLM_API_KEY=replace-locally
LLM_FAST_MODEL=your-fast-model
LLM_REASONING_MODEL=your-reasoning-model
LLM_REPORT_MODEL=your-report-model
```

可选启动本地 Embedding Worker：

```bat
E:\miniconda3\envs\bettafish\python.exe -m uvicorn ml_worker.app:app --host 127.0.0.1 --port 8010
```

并设置：

```env
EMBEDDING_WORKER_URL=http://127.0.0.1:8010
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=auto
```

Worker 未启动时，检索会降级到数据库全文/子串检索，不会伪造向量结果。

## PostgreSQL 与迁移

```env
DATABASE_URL=postgresql+asyncpg://coifesp:replace-locally@127.0.0.1:5432/coifesp
```

```bash
cd backend
alembic upgrade head
```

部署或 CI 应使用一次性测试数据库执行完整迁移链、回滚和恢复后重升验证：

```bash
python scripts/verify_postgres_migrations.py
```

该脚本要求 `COIFESP_PG_TEST_URL` 指向数据库名含 `test` 或 `ci` 的可销毁数据库，避免
误操作生产库。

## 真实社交平台采集

1. 阅读 `vendor/README.md` 并准备 MediaCrawler 源码。
2. 执行 `scripts/apply-mediacrawler-patches.cmd`。
3. 在本地 `backend/.env` 中设置 `DEMO_MODE=false`、MediaCrawler 路径和登录方式。
4. Cookie、API Key 等秘密只放入本地环境或外部 Secret Store，不得提交。
5. 在工作台中明确确认采集范围后再启动任务。

MediaCrawler 的上游许可仅允许非商业学习与研究用途；使用者必须自行遵守平台规则、
隐私要求和适用法律。

## 验证

后端：

```bat
cd backend
python -m pytest --basetemp=.pytest-tmp
python -m ruff check .
```

前端：

```bat
cd frontend
npm test
npm run build
```

全量回归编排：

```bat
cd backend
python scripts\run_full_regression.py
```

浏览器 E2E 需要先启动前后端：

```bat
cd frontend
npm run e2e:smoke
npm run e2e:interact
```

## 生产部署要求

生产部署不能直接沿用本地进程沙箱。至少需要：

- PostgreSQL、迁移备份和恢复流程；
- 容器化工具执行器、只读根文件系统、cap-drop 与资源限制；
- egress-only Docker/CNI 网络和白名单出口代理；
- 外部 Secret Store；
- OTLP Collector、SLO 和告警接收端；
- 对一次性测试数据库完成迁移链验收；
- 按组织要求完成故障注入和恢复演练。

完整环境变量、迁移、密钥轮换、备份恢复和事故处置流程见
[`docs/operations/production-runbook.md`](docs/operations/production-runbook.md)。

## 不包含的本地内容

发布快照明确排除以下内容：

- `.env`、Cookie、API Key、数据库口令和本地 Secret Store 配置；
- SQLite 数据库、真实采集结果、导出 Artifact 和运行日志；
- `evaluation-datasets/` 数据集构建与标注工作区；
- `human_check/` 人工复核工作区；
- 实现计划、验收轮次记录、修复报告和历史待办文档；
- pytest/Vite 缓存、临时目录、测试截图和本地 Agent/IDE 设置；
- MediaCrawler 上游完整源码；仓库仅保存接入说明和必要补丁。

## 安全提示

- 不要提交由 `.env.example` 复制出的真实 `.env` 文件。
- 不要把 Cookie、Token 或内部服务地址写入测试、截图、日志和提交信息。
- 不要在生产环境关闭沙箱、内容安全或审批策略以绕过配置问题。
- 分享报告或证据前，确认数据来源、授权范围和脱敏结果。
