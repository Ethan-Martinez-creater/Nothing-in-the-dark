# COIFESP 生产运维手册

> 文档状态：配合审核第 6 项（上线前工程化）编制。部署目标为 Linux 容器
> 环境（生产安全基线见 M15）；Windows 为开发模式，隔离能力弱于生产。

## 1. 部署

### 1.1 依赖

- PostgreSQL 14+（含 pgvector 扩展）
- Python 3.11+（backend/.venv）
- Node 18+（frontend，构建产物可静态托管）
- 可选：容器运行时（生产强制沙箱启用条件）

### 1.2 环境变量（backend）

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| DATABASE_URL | 是 | postgresql+asyncpg://user:pass@host/db |
| COIFESP_DEMO_MODE | 否 | true 时使用 Demo 爬虫（无网络） |
| TOOL_SANDBOX_EXECUTION | 生产是 | `container`；运行时不可用时启动失败，不降级 |
| COIFESP_CONTAINER_RUNTIME | 生产是 | 非空标记，且 docker CLI/daemon 必须可用 |
| COIFESP_SANDBOX_CONTAINER_NETWORK | 联网工具是 | 运维创建的 egress-only Docker/CNI 网络；禁止 bridge/host |
| COIFESP_SANDBOX_CONTAINER_PROXY_URL | 联网工具是 | 容器内可达的白名单 EgressProxy URL |
| COIFESP_SECRET_STORE_CMD | 否 | 密钥 getter 命令（生产对接外部 Secret Store） |
| MEDIACRAWLER_*_COOKIES | 按平台 | 采集平台 Cookie（经 SecretProvider 注入沙箱子进程） |
| TELEMETRY_EXPORTER | 否 | noop/console/in_memory/otlp_http |
| TELEMETRY_OTLP_ENDPOINT | 否 | otlp_http 时必填，如 http://otel-collector:4318/v1/traces |
| TOOL_SANDBOX_MODE | 否 | audit_only / enforce（生产默认 enforce） |

### 1.3 启动

```bash
# 迁移（面向 PostgreSQL）
cd backend && alembic upgrade head

# 迁移链验证（部署/CI 推荐，需空库或临时实例）
# 空库全链：alembic upgrade head（base→head，含 CREATE EXTENSION vector）
# 回滚验证：alembic downgrade -1 && alembic upgrade head
# 备份恢复后重升：pg_restore -d <db> <backup> && alembic upgrade head
# 后端
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 前端构建
cd frontend && npm run build && 静态托管 dist/
```

## 2. 密钥轮换

- 开发/预发：密钥以明确环境变量承载（COIFESP_* / MEDIACRAWLER_*）；轮换即更新
  环境变量并重启 Worker（运行中任务按策略继续或取消）。
- 生产：配置 COIFESP_SECRET_STORE_CMD 对接外部 Secret Store；应用只持有引用，
  轮换由 Store 版本化；新任务使用新版本。
- 吊销：从 Store 移除/停用后，SecretProvider.resolve 返回 None → 策略层
  secret_missing 拒绝对应工具（fail closed）。
- 审计：secret_references 记录指纹与 rotation_state；严禁明文写入
  ToolCall/artifact/日志/Trace（三层脱敏：已知值、格式规则、字段分类）。

## 3. 备份与恢复

- 数据库：每日 pg_dump + 归档（WAL）；迁移前强制备份。
- 恢复流程：pg_restore → alembic stamp head（确保迁移版本一致）→ 校验
  kill_switches / approvals / dead_letter_items 状态一致。
- 回滚：Alembic downgrade 至上一版本；若迁移含不可降级步骤，按各迁移文件
  文档的恢复方案执行（禁止为恢复功能全局关闭沙箱策略）。

## 4. 监控与告警

- Trace：TELEMETRY_EXPORTER=otlp_http 上报 OTLP JSON 至集中 Collector；
  HttpOtlpExporter 有界缓冲 + 后台批量，失败静默不阻断业务。
- 指标：/api/v1/system/telemetry/health 提供 exporter/span_count/SLO 合规；
  M22 事故处置台提供健康矩阵/熔断/队列/死信/Kill Switch。
- 告警接收：通知端点（Webhook）经 M13 校验（协议/DNS/IP/重定向）投递；
  签名 X-Webhook-Signature（HMAC-SHA256 + 时间戳 + event_id，防重放窗口 300s）。
- 事故流程：检测（告警）→ 处置（Kill Switch / 降级路由）→ 恢复 → 死信重放
  （需 M21 审批，同审批仅一次）→ 事故复盘关闭（/system/resilience/incidents）。

## 5. 运维操作清单（全部可审计、需 M21 审批）

- Kill Switch 开启/关闭：/system/resilience/kill-switches（approval_id 必填，一票一次）。
- 死信重放：代码/策略版本一致 + 审批；参数哈希绑定，防篡改。
- 审批过期清理：/approvals/expire-overdue；历史保留不物理删除。
- 记忆维护：/memories/maintenance（过期扫描 + 索引一致性）；reindex 大库建议迁 Worker。

## 6. 已知限制（能力差距声明）

- Windows 开发模式无 seccomp/cgroup/独立网络命名空间。生产必须设置
  `tool_sandbox_execution=container`、`COIFESP_CONTAINER_RUNTIME=docker`；网络工具还必须
  设置 `COIFESP_SANDBOX_CONTAINER_NETWORK` 为运维创建的 egress-only Docker/CNI 网络，
  并设置 `COIFESP_SANDBOX_CONTAINER_PROXY_URL` 为该网络内可达的白名单代理。
  普通 bridge/host 网络被拒绝；缺运行时、强网络或代理均 fail closed，不降级运行。
  secret store 仍需在部署环境通过 `secret_store_cmd` 对接，未解析的声明秘密会被策略拒绝。
- crawler 子进程只接收代码定义的非密配置白名单与 manifest 声明秘密；容器模式下
  `MEDIACRAWLER_ROOT`、入口和 Python 路径必须填写容器内路径，并由镜像提供对应文件。
- 分享链接总配额与一分钟窗口均由数据库原子消费；窗口超限返回 HTTP 429。
- E2E 浏览器测试已完成本机 Playwright 冒烟（11 路由 11/11 通过，见
  实施报告第 12 节）；CI 上需准备 Playwright 浏览器环境复跑
  （frontend/e2e-smoke.cjs）。
