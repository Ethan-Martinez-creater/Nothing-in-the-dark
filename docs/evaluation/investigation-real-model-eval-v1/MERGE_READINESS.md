# 最终合并准备记录

日期：2026-10-19（Asia/Shanghai）。本记录仅补充交接文档，不修改工程实现、冻结数据、rubric 或已有评测结果，不触发真实模型请求。

[Issue #1](https://github.com/Ethan-Martinez-creater/Nothing-in-the-dark/issues/1) · [PR #3](https://github.com/Ethan-Martinez-creater/Nothing-in-the-dark/pull/3) · [PR #4](https://github.com/Ethan-Martinez-creater/Nothing-in-the-dark/pull/4)

## 审核与决定边界

首轮 baseline 证据已通过[审查5566266465](https://github.com/Ethan-Martinez-creater/Nothing-in-the-dark/pull/3#pullrequestreview-5566266465)。本轮工程修复与证据通过[审查5568472949](https://github.com/Ethan-Martinez-creater/Nothing-in-the-dark/pull/4#pullrequestreview-5568472949)，审查覆盖的 PR #4 头为 `d59c63f5b35b144eec3f3a856265746b8743a0cf`。两次均为 COMMENT 形式的接受意见：连接账户不能正式 approve 自己的 PR。接受的是证据交付及定向修复，不是调查质量或生产就绪验收。

检查时两个 PR 均 open/draft/未合并。用户保留实际合并和关闭 Issue 的决定。没有启用 auto-merge，也未修改 main 或提前将 PR #4 改指 main。

## 分支关系和无冲突预演

| 引用 | 核对的提交 |
|---|---|
| origin/main | `fb2a5f42a22324e44ee9f8b8c08b38925a59d2e0` |
| PR #3 head / PR #4 base | `ecbce18a263072a8a2252230db5d10695d11830c` |
| PR #4 审核头 | `d59c63f5b35b144eec3f3a856265746b8743a0cf` |

祖先关系为 `main → PR #3 → PR #4`。PR #4 相对 PR #3 只有四个原有提交：`9d09598`、`6768efd`、`0518eec`、`d59c63f`；本交接再增加一个纯文档提交，最终 SHA 和对应 CI 记录于 Issue #1。原始 PR #3 提交没有作为新的修复提交重复加入。

已执行两阶段 `git merge-tree --write-tree` 预演，均退出0、没有冲突：

- main 与 PR #3 的预测 tree：`324dad68579627cb1605a3aa5255094fe8471073`，等于 PR #3 的 tree。
- 上述集成内容再与已审核 PR #4 合并的预测 tree：`fde377f9c3f01ea2be7f8e3efc8c5fbff9e97d87`，等于 `d59c63f` 的 tree。

该命令仅创建 Git tree 对象，未移动分支或实际合并。这是当前引用的预演；不能冒充尚未发生的 main 合并或改基后的 CI。

## 执行顺序：用户决定合并后

1. 先合并 PR #3。建议使用 **merge commit** 保留原始评测提交的祖先关系和 SHA，不删除证据分支。
2. 获取最新 main，确认 `ecbce18` 已是 main 的祖先。此时将 PR #4 的 base 从 `codex/investigation-real-eval-v1` 改为 `main`。通常只需 retarget，无需重写 PR #4 的提交历史。
3. 检查 PR #4 的剩余提交/文件只包含修复、candidate 证据和本交接文档。复核冻结文件/旧结果没有 diff，并再次检查冲突、GitHub test-merge 与 CI；必要时对齐 main 后重新验证。只有新的基底 CI 成功才交回 PR #4 合并决定。
4. 若 PR #3 被 squash/rebase 合并，先核对合并 tree 及实际 merge SHA，再选择保留备份引用的 `rebase --onto` 等方式移植仅属于 PR #4 的提交；不得直接将包含全部 PR #3 历史的提交列表当作干净修复。检查文件 diff、重复 patch 和证据文件后再推送，不能无检查地强推。真实执行 SHA 继续保留为历史证据，不能改写 manifest 来匹配新 SHA。
5. Issue 是否关闭由用户决定；关闭证据交付不表示 IR02/IR08/IR09 的调查质量问题已解决。

在 PR #3 尚未合并时，步骤2–3是明确的后置检查，尚不能声称已经完成。本轮不等待或自动监控未来合并。

在独立评测工作区，可用以下 PowerShell/Git 命令复核关系；远端访问如有需要使用本机代理的 per-command Git 配置。不要在带有用户未提交工作的原始 main 工作区执行切换或 reset。

```powershell
git fetch origin main codex/investigation-real-eval-v1 codex/investigation-eval-closure
git merge-base origin/main origin/codex/investigation-real-eval-v1
git merge-base origin/codex/investigation-real-eval-v1 origin/codex/investigation-eval-closure
git log --oneline origin/codex/investigation-real-eval-v1..origin/codex/investigation-eval-closure
git diff --stat origin/codex/investigation-real-eval-v1..origin/codex/investigation-eval-closure
git merge-tree --write-tree origin/main origin/codex/investigation-real-eval-v1
git merge-tree --write-tree origin/codex/investigation-real-eval-v1 origin/codex/investigation-eval-closure
```

## CI 与可复现记录

- PR #3 `ecbce18`：[CI39186740242](https://github.com/Ethan-Martinez-creater/Nothing-in-the-dark/actions/runs/39186740242) completed/success。
- 已审核 PR #4 `d59c63f`：[CI39218144953](https://github.com/Ethan-Martinez-creater/Nothing-in-the-dark/actions/runs/39218144953) completed/success。
- 最终纯文档准备提交的 PR CI 在推送后重新检查，提交 SHA/运行链接写入 Issue #1。它仍针对当前 stacked base，不能替代 PR #3 合并后面向 main 的 CI。

CI 执行前后端检查、迁移 smoke 和脚本模型 Tier A contract；不需要真实 Key/Cookie，不调用真实模型。CI 中的 PostgreSQL 迁移通过也不等同于新预算行锁的 PostgreSQL 集成测试。

既有本机记录：70项定向整合测试通过，旧24场景的22项兼容性断言通过。详细命令和环境在 [candidate执行记录](results/candidate-closure-v1/execution-record.md)，baseline 的历史命令在[原始执行记录](results/execution-record.md)与[RUNBOOK](RUNBOOK.md)。真实 baseline 必须使用 `518267b`，真实 candidate 必须使用 `0518eec`；当前合并准备提交不能被冒称为那两次执行的版本。历史真实命令仅供复现追溯，本轮不执行 preflight 或任何真实模型任务。

如需在具备现有开发依赖的 backend 目录复核定向测试，以下全部使用脚本模型/合成库；为每次运行指定新的 E 盘临时路径，保留已有目录：

```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$checkLabel = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$env:TEMP = Join-Path (Get-Location).Path "artifacts/merge-check-temp-$checkLabel"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP | Out-Null
$evalTests = @(
    'tests/test_execution_policy_runtime.py', 'tests/test_agent_runtime.py',
    'tests/test_agent_loop.py', 'tests/test_execution_policy_api.py',
    'tests/test_investigation_diagnostics.py', 'tests/test_investigation_evaluator_audit.py',
    'tests/test_agent_eval_real_source_evaluators.py'
)
uv run python -B -m pytest @evalTests -q -o addopts= -p no:cacheprovider "--basetemp=artifacts/merge-check-$checkLabel"
```

留存 raw artifact 的离线 E6/E13 复核命令见[独立离线审计](results/candidate-closure-v1/offline-audit.md)，不创建模型 gateway。公开证据含原始哈希和节选，完整 raw/SQLite/私有配置仍在本机忽略目录，不复制或提交密钥。

## 保留结果和未解决问题

| 证据 | 执行 SHA | 自动检查 | Codex暂定人工验收 |
|---|---|---:|---:|
| 原始10条 baseline | `518267bb2202568a5a894ce5317715877a2374ed` | **4/10** | **3/10** |
| 定向5条 candidate | `0518eec1bf8fb49d3718d7916d6e6164866df31f` | **5/5** | **2/5** |

两组分母、执行行为和评测器版本不同，不能将其直接解释为通用效果提升。冻结 suite SHA256 保持 `c63ed42883d16e73ec0594b9c023fd65a34c90fa10a48f952d887bf2f21d590d`。原始结果不重算，E6/E13 修正仅作为独立审计保留。

- IR02/IR08/IR09 candidate 最终只有 runtime budget-stop 提示，没有请求的分析、冲突综合或可追溯引用，人工验收仍为0。`completed` 和自动通过不代表调查目标完成。
- IR10 达成系统受控拒答：3次实际工具调用/3次持续故障，2个超额请求被阻断，无 memory write；不能称为模型自主生成了合格分析。
- 真实 IR08 candidate 没有委派专家；跨专家只读边界由真实 GraphWorker 的脚本集成测试支持。只读策略是显式 opt-in，非全局默认。
- Embedding 服务不可用尚未解决；原始模型 API 异常、Agent 超时分别记录，未建立单一因果解释。新预算 PostgreSQL 行锁没有集成验证。
- 小样本、单次 candidate、私有 raw 未独立完整审查、费用未知等限制继续成立。[Baseline限制](results/limitations.md)、[candidate失败账本](results/candidate-closure-v1/failure-ledger.md)、[candidate总结](results/candidate-closure-v1/summary.md)继续作为验收依据。

后续调查质量改进需另行限定任务，不能通过改写冻结 v1、扩大本轮范围或追加模型批次来掩盖这些问题。
