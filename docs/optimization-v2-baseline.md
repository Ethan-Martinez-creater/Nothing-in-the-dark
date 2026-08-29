# Optimization V2 Baseline（M0.1）

> 记录时间：2026-08-29。本文件记录优化工程开始前的测试基线，用于区分"已有失败"与"本轮新增失败"。

## 代码版本

- Git SHA：`0fc244bf24d7f7d2976ce1e0700e573ede660690`（main，"发布 COIFESP Agent 完整系统快照"）
- 后端 Python：`E:\miniconda3\envs\bettafish\python.exe`（3.11.15）
- Node：v24.15.0 / npm 11.12.1
- 最新 Alembic migration：`20260824_0045_share_download_rate_limit`

## 前端基线

| 命令 | 结果 |
|---|---|
| `npm run typecheck` | ✅ 通过（0 错误） |
| `npm run lint` | ❌ **39 errors（预存基线失败，非本轮引入）** |
| `npm run test`（vitest） | ✅ 12 files / 112 tests 全部通过 |
| `npm run build` | ✅ 成功（CaseWorkspaceView chunk 636KB 超限警告，非错误） |

### lint 基线错误分布（39 个）

- `e2e-interact.cjs` / `e2e-smoke.cjs`：23 个（`no-undef` process/console/document、`no-require-imports`）——Node CJS 脚本被浏览器 TS 规则误扫
- `MonitoringPanel.vue`：1（unused `err`）
- `ApprovalInboxView.vue`：1（unused `AlertTriangle`）
- `GoalPlanningView.vue`：2（unused `busy`/`fmt`）
- `MemoryGovernanceView.vue`：2（unused icons）
- `ObservabilityView.vue`：2（unused icons）
- `ResilienceConsoleView.vue`：3（unused icons/`scope`）
- `ReviewWorkbenchView.vue`：至少 2（unused icons）

处置：本轮 M8.5 收尾时统一修复（e2e 脚本加 lint env/ignore + 清理 unused import）。

## 后端基线

- `pytest`（全量，2026-08-29 基线）：**778 passed / 0 failed**（2h30m，Windows 本机）
- `ruff check app tests`：**All checks passed**
- pytest addopts：`--basetemp=.pytest-tmp`（backend 目录内），asyncio_mode=auto

## 兼容性关键行为快照（M0.2 核对项）

- `POST /api/v1/cases/{case_id}/messages` → 202，返回 `AgentRunResponse`（`cases.create_agent_message` → `AgentRunService.start`）
- Run metadata 现有字段：`approve_crawl`、`artifact_ref`（可选）
- `GET /api/v1/runs/{id}/events/stream`（SSE）、`/trace`、`/cancel`、`/approve`、`/resume`
- alert 状态机：`open → acknowledged → resolved`，任意 → `suppressed`
- Review `OBJECT_TYPES` 当前不含 `finding`（M4 扩展点）
- Case 删除：`ApplicationRepository.delete_case()` 显式级联清理
