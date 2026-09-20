"""Trace Replay & Regression Diff（主计划 Phase 4 / 第 35–40 节）。

目标不是"视频回放"，而是回答：**为什么新版本比旧版本更好或更差？**

两种模式：

* **observation_replay** —— 冻结源 run 的工具观察，candidate 只重新决策
  （prompt / model 对比）。candidate 若调用源未记录的工具或参数，
  冻结层**显式报错**，绝不回退去执行真实工具。
* **seeded_full_rerun** —— 在冻结 fixture 上完整重跑（tool routing /
  orchestration 回归）。

实现原则：复用现有 Agent Runtime 与 Run/Tool Trace；不新建第二套 runtime 或
trace 系统。冻结 registry 只替换 handler，工具契约（schema / permissions /
side effect / approval）全部来自真实 Tool Registry。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

from app.evaluation.agent_dataset import AgentGoldenSuite
from app.evaluation.agent_eval import (
    CONTRACT_MODE,
    AgentSuiteRunner,
    _git_sha,
    build_contract_steps,
)
from app.evaluation.agent_manifest import (
    ReplayBundle,
    short_hash,
)
from app.evaluation.agent_trace import AgentTrace

#: Replay 模式。
OBSERVATION_REPLAY = "observation_replay"
SEEDED_FULL_RERUN = "seeded_full_rerun"
REPLAY_MODES: tuple[str, ...] = (OBSERVATION_REPLAY, SEEDED_FULL_RERUN)


class FrozenObservationMissing(RuntimeError):
    """candidate 调用了源轨迹未记录的 (tool, arguments) —— 必须显式失败。"""


# ---------------------------------------------------------------------------
# 冻结观察
# ---------------------------------------------------------------------------


def _arguments_signature(arguments: Mapping[str, Any]) -> str:
    """参数指纹：忽略 runtime 注入的字段，只比对模型可控的部分。"""
    filtered = {
        key: value
        for key, value in arguments.items()
        if key not in {"case_id", "run_id", "dispatch_key"}
    }
    return short_hash(filtered)


class FrozenObservations:
    """按 (tool, 参数指纹) 索引源 run 的工具观察。

    同一工具被多次调用且参数相同时，按调用顺序依次返回（保持顺序语义）。
    """

    def __init__(self, bundle: ReplayBundle) -> None:
        self._buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._cursor: dict[tuple[str, str], int] = {}
        self._all: list[dict[str, Any]] = list(bundle.tool_observations)
        #: 未命中的 (tool, arguments)。handler 抛异常会让 run 失败、tool_calls
        #: 不落库，所以必须在这里累积证据，否则"冻结缺失"会被误读成普通失败。
        self.misses: list[dict[str, Any]] = []
        for index, tool in enumerate(bundle.tool_sequence):
            arguments = (
                bundle.tool_arguments[index]
                if index < len(bundle.tool_arguments)
                else {}
            )
            observation = (
                bundle.tool_observations[index]
                if index < len(bundle.tool_observations)
                else {}
            )
            key = (tool, _arguments_signature(arguments))
            self._buckets.setdefault(key, []).append(observation)

    def lookup(
        self, tool: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        key = (tool, _arguments_signature(arguments))
        bucket = self._buckets.get(key)
        if not bucket:
            self.misses.append({"tool": tool, "arguments": dict(arguments)})
            raise FrozenObservationMissing(
                f"no frozen observation for tool={tool!r} "
                f"arguments={sorted(arguments)} (source run did not record it)"
            )
        index = self._cursor.get(key, 0)
        self._cursor[key] = index + 1
        return bucket[min(index, len(bucket) - 1)]

    @property
    def size(self) -> int:
        return len(self._all)


def build_frozen_registry(source_registry: Any, observations: FrozenObservations) -> Any:
    """构造冻结 registry：契约来自真实 registry，handler 改为返回冻结观察。

    这不是"第二套 Tool System"——ToolSpec 的 schema / permissions /
    side_effect / approval 语义全部原样复制，只有执行被替换为源 run 的观测
    （主计划第 37.1 节明确要求 candidate 不重新执行外部 Tool）。
    """
    from app.harness.tools import ToolRegistry

    registry = ToolRegistry()
    for name in sorted(source_registry.names()):
        spec = source_registry.get(name)

        async def _frozen_handler(arguments: Any, *, _name: str = name) -> dict[str, Any]:
            payload = (
                arguments.model_dump()
                if hasattr(arguments, "model_dump")
                else dict(arguments or {})
            )
            observation = observations.lookup(_name, payload)
            result = observation.get("result")
            if isinstance(result, dict):
                return result
            return {
                "ok": observation.get("status") == "succeeded",
                "frozen": True,
                "error_code": observation.get("error_code"),
            }

        registry.register(replace(spec, handler=_frozen_handler))
    return registry


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SequenceDiff:
    """工具序列差异（按位置对齐的最长公共子序列之外的增删）。"""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    reordered: bool = False
    common_prefix: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "added": list(self.added),
            "removed": list(self.removed),
            "reordered": self.reordered,
            "common_prefix": self.common_prefix,
            "changed": bool(self.added or self.removed or self.reordered),
        }


@dataclass(frozen=True, slots=True)
class ReplayDiff:
    """一次 replay 的全部差异（计划第 40 节的固定清单）。"""

    source_run_id: str
    candidate_run_id: str
    mode: str
    schema_changed: bool
    tool_sequence_diff: SequenceDiff
    tool_argument_diff: dict[str, object] = field(default_factory=dict)
    artifact_diff: dict[str, object] = field(default_factory=dict)
    citation_diff: dict[str, object] = field(default_factory=dict)
    metrics_delta: dict[str, float] = field(default_factory=dict)
    task_success_delta: float = 0.0
    latency_delta_ms: float = 0.0
    token_delta: float = 0.0
    cost_delta: float = 0.0
    judge_delta: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_run_id": self.source_run_id,
            "candidate_run_id": self.candidate_run_id,
            "mode": self.mode,
            "schema_changed": self.schema_changed,
            "tool_sequence_diff": self.tool_sequence_diff.to_dict(),
            "tool_argument_diff": self.tool_argument_diff,
            "artifact_diff": self.artifact_diff,
            "citation_diff": self.citation_diff,
            "metrics_delta": self.metrics_delta,
            "task_success_delta": self.task_success_delta,
            "latency_delta_ms": self.latency_delta_ms,
            "token_delta": self.token_delta,
            "cost_delta": self.cost_delta,
            "judge_delta": self.judge_delta,
        }

    def to_markdown(self) -> str:
        payload = self.to_dict()
        seq = self.tool_sequence_diff.to_dict()
        lines = [
            "# Replay Diff",
            "",
            f"- mode: `{self.mode}`",
            f"- source run: `{self.source_run_id}`",
            f"- candidate run: `{self.candidate_run_id}`",
            f"- tool schema changed: `{self.schema_changed}`",
            "",
            "## Tool sequence",
            "",
            f"- added: {seq['added'] or '—'}",
            f"- removed: {seq['removed'] or '—'}",
            f"- reordered: {seq['reordered']}",
            f"- common prefix length: {seq['common_prefix']}",
            "",
            "## Tool arguments",
            "",
            f"```json\n{json.dumps(payload['tool_argument_diff'], ensure_ascii=False, indent=2)}\n```",
            "",
            "## Artifacts",
            "",
            f"```json\n{json.dumps(payload['artifact_diff'], ensure_ascii=False, indent=2)}\n```",
            "",
            "## Citations",
            "",
            f"```json\n{json.dumps(payload['citation_diff'], ensure_ascii=False, indent=2)}\n```",
            "",
            "## Metrics delta",
            "",
            "| metric | delta |",
            "|---|---|",
        ]
        for key, value in sorted(payload["metrics_delta"].items()):
            lines.append(f"| `{key}` | {value:+} |")
        lines.append("")
        return "\n".join(lines)


def diff_sequences(source: Sequence[str], candidate: Sequence[str]) -> SequenceDiff:
    """工具序列差异：公共前缀 + 位置对齐后的增删 + 顺序变化标记。"""
    prefix = 0
    for left, right in zip(source, candidate):
        if left != right:
            break
        prefix += 1
    source_tail = list(source[prefix:])
    candidate_tail = list(candidate[prefix:])
    removed = tuple(item for item in source_tail if item not in candidate_tail)
    added = tuple(item for item in candidate_tail if item not in source_tail)
    reordered = (
        not added
        and not removed
        and source_tail != candidate_tail
        and sorted(source_tail) == sorted(candidate_tail)
    )
    return SequenceDiff(
        added=added, removed=removed, reordered=reordered, common_prefix=prefix
    )


def diff_tool_arguments(
    source: ReplayBundle, candidate: ReplayBundle
) -> dict[str, object]:
    """逐位置比对参数（忽略 runtime 注入字段）。"""
    changes: list[dict[str, object]] = []
    for index, (left_tool, right_tool) in enumerate(
        zip(source.tool_sequence, candidate.tool_sequence)
    ):
        if left_tool != right_tool:
            continue
        left_args = dict(source.tool_arguments[index]) if index < len(source.tool_arguments) else {}
        right_args = (
            dict(candidate.tool_arguments[index])
            if index < len(candidate.tool_arguments)
            else {}
        )
        if _arguments_signature(left_args) == _arguments_signature(right_args):
            continue
        # 括号必须显式：`|` 与 `-` 同优先级，漏括号会让 case_id 逃过排除。
        keys = sorted((set(left_args) | set(right_args)) - {"case_id"})
        changes.append(
            {
                "index": index,
                "tool": left_tool,
                "changed_keys": [
                    key
                    for key in keys
                    if left_args.get(key) != right_args.get(key)
                ],
            }
        )
    return {"changes": changes, "changed": bool(changes)}


def diff_artifacts(source: ReplayBundle, candidate: ReplayBundle) -> dict[str, object]:
    source_kinds = list(source.artifact_kinds)
    candidate_kinds = list(candidate.artifact_kinds)
    return {
        "source": source_kinds,
        "candidate": candidate_kinds,
        "added": [kind for kind in candidate_kinds if kind not in source_kinds],
        "removed": [kind for kind in source_kinds if kind not in candidate_kinds],
        "changed": source_kinds != candidate_kinds,
    }


def diff_citations(source: ReplayBundle, candidate: ReplayBundle) -> dict[str, object]:
    """引用差异：回答中出现的 id token 集合差异（幻觉检测的 replay 版）。"""
    import re

    pattern = re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
        r"|\b[0-9a-f]{32}\b"
    )
    source_ids = set(pattern.findall(source.final_response or ""))
    candidate_ids = set(pattern.findall(candidate.final_response or ""))
    return {
        "source_count": len(source_ids),
        "candidate_count": len(candidate_ids),
        "added": sorted(candidate_ids - source_ids),
        "removed": sorted(source_ids - candidate_ids),
        "changed": source_ids != candidate_ids,
    }


def diff_bundles(
    source: ReplayBundle,
    candidate: ReplayBundle,
    *,
    mode: str,
    task_success_source: float | None = None,
    task_success_candidate: float | None = None,
    judge_source: Mapping[str, float] | None = None,
    judge_candidate: Mapping[str, float] | None = None,
) -> ReplayDiff:
    """生成计划第 40 节要求的完整 diff。"""
    source_metrics = dict(source.metrics)
    candidate_metrics = dict(candidate.metrics)
    metrics_delta: dict[str, float] = {}
    for key in sorted(set(source_metrics) | set(candidate_metrics)):
        left = source_metrics.get(key)
        right = candidate_metrics.get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            metrics_delta[key] = round(float(right) - float(left), 4)

    judge_delta: dict[str, float] = {}
    for key in sorted(set(judge_source or {}) | set(judge_candidate or {})):
        left = (judge_source or {}).get(key)
        right = (judge_candidate or {}).get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            judge_delta[key] = round(float(right) - float(left), 4)

    return ReplayDiff(
        source_run_id=source.run_id,
        candidate_run_id=candidate.run_id,
        mode=mode,
        schema_changed=source.tool_schema_hash != candidate.tool_schema_hash,
        tool_sequence_diff=diff_sequences(
            source.tool_sequence, candidate.tool_sequence
        ),
        tool_argument_diff=diff_tool_arguments(source, candidate),
        artifact_diff=diff_artifacts(source, candidate),
        citation_diff=diff_citations(source, candidate),
        metrics_delta=metrics_delta,
        task_success_delta=round(
            (task_success_candidate or 0.0) - (task_success_source or 0.0), 4
        ),
        latency_delta_ms=round(
            float(candidate_metrics.get("latency_ms", 0))
            - float(source_metrics.get("latency_ms", 0)),
            1,
        ),
        token_delta=round(
            float(candidate_metrics.get("input_tokens", 0))
            + float(candidate_metrics.get("output_tokens", 0))
            - float(source_metrics.get("input_tokens", 0))
            - float(source_metrics.get("output_tokens", 0)),
            1,
        ),
        cost_delta=round(
            float(candidate_metrics.get("estimated_cost", 0))
            - float(source_metrics.get("estimated_cost", 0)),
            6,
        ),
        judge_delta=judge_delta,
    )


# ---------------------------------------------------------------------------
# Replay 服务
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReplayResult:
    mode: str
    source_bundle: ReplayBundle
    candidate_bundle: ReplayBundle
    diff: ReplayDiff
    candidate_trace: AgentTrace | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "source_bundle": self.source_bundle.to_dict(),
            "candidate_bundle": self.candidate_bundle.to_dict(),
            "diff": self.diff.to_dict(),
            "notes": list(self.notes),
        }


class AgentReplayService:
    """在现有 runtime 上做 observation replay / seeded full rerun。"""

    def __init__(
        self,
        *,
        suite: AgentGoldenSuite,
        database: Any,
        gateway: Any | None = None,
        mode: str = CONTRACT_MODE,
    ) -> None:
        self._suite = suite
        self._database = database
        self._gateway = gateway
        self._mode = mode

    async def replay_run(
        self,
        source_bundle: ReplayBundle,
        *,
        replay_mode: str = OBSERVATION_REPLAY,
        candidate_gateway: Any | None = None,
        candidate_config: Mapping[str, Any] | None = None,
    ) -> ReplayResult:
        if replay_mode not in REPLAY_MODES:
            raise ValueError(f"unknown replay mode: {replay_mode}")
        if replay_mode == OBSERVATION_REPLAY:
            return await self._observation_replay(
                source_bundle,
                candidate_gateway=candidate_gateway,
                candidate_config=candidate_config,
            )
        return await self._seeded_rerun(source_bundle, candidate_config=candidate_config)

    # -- observation replay ------------------------------------------------

    async def _observation_replay(
        self,
        source_bundle: ReplayBundle,
        *,
        candidate_gateway: Any | None,
        candidate_config: Mapping[str, Any] | None,
    ) -> ReplayResult:
        """冻结观察重放：candidate 重新决策，工具结果全部来自源观测。"""
        from app.evaluation.agent_fixture_seed import build_stack, seed_fixture

        task = self._suite.by_id(source_bundle.task_id)
        fixture = self._suite.fixtures[task.fixture_id]
        stack = build_stack(self._database)
        seeded = await seed_fixture(
            stack, fixture_id=task.fixture_id, fixture=fixture
        )
        known_layout = {
            "registry_names": set(),
            "frozen": True,
            "config": dict(candidate_config or {}),
        }
        notes: list[str] = []

        # 真实 registry 只用来提供契约；执行被冻结观察替换。
        real_tools = self._build_real_registry(stack)
        observations = FrozenObservations(source_bundle)
        frozen_tools = build_frozen_registry(real_tools, observations)
        known_layout["registry_names"] = set(real_tools.names())

        gateway = candidate_gateway or self._default_candidate_gateway(
            task, seeded, real_tools, source_bundle
        )
        trace = await self._execute(
            stack=stack,
            task=task,
            seeded=seeded,
            gateway=gateway,
            tools=frozen_tools,
        )
        candidate_bundle = self._bundle_from_trace(
            trace=trace, task=task, gateway=gateway, tools=real_tools
        )
        diff = diff_bundles(
            source_bundle, candidate_bundle, mode=OBSERVATION_REPLAY
        )
        notes.append(
            "观察被冻结：candidate 未重新执行任何外部工具；"
            "调用源未记录的工具/参数时冻结层抛 FrozenObservationMissing，"
            "runtime 将其记录为该次工具调用失败（绝不回退执行真实工具）。"
        )
        # 显式暴露"冻结缺失"，避免被误读成普通工具报错。
        if observations.misses:
            missing_tools = sorted({str(item["tool"]) for item in observations.misses})
            notes.append(
                f"candidate 有 {len(observations.misses)} 次工具调用未命中冻结观察"
                f"（{', '.join(missing_tools)}）：冻结层抛出 FrozenObservationMissing，"
                "该调用未被真实执行。"
            )
        if diff.schema_changed:
            notes.append(
                "tool schema 与源运行不同：observation replay 允许继续，"
                "但 full_rerun 必须重新做 tool contract 校验。"
            )
        return ReplayResult(
            mode=OBSERVATION_REPLAY,
            source_bundle=source_bundle,
            candidate_bundle=candidate_bundle,
            diff=diff,
            candidate_trace=trace,
            notes=tuple(notes),
        )

    # -- seeded full rerun -------------------------------------------------

    async def _seeded_rerun(
        self,
        source_bundle: ReplayBundle,
        *,
        candidate_config: Mapping[str, Any] | None,
    ) -> ReplayResult:
        """在冻结 fixture 上完整重跑（tool contract 重新校验）。"""
        runner = AgentSuiteRunner(
            suite=self._suite,
            database=self._database,
            mode=self._mode,  # type: ignore[arg-type]
            gateway=self._gateway,
            candidate_label=str((candidate_config or {}).get("label") or "replay"),
        )
        report = await runner.run(task_ids=[source_bundle.task_id])
        result = report.task_results[0]
        candidate_bundle = self._bundle_from_task_result(
            result, source_bundle, report=report
        )
        diff = diff_bundles(source_bundle, candidate_bundle, mode=SEEDED_FULL_RERUN)
        return ReplayResult(
            mode=SEEDED_FULL_RERUN,
            source_bundle=source_bundle,
            candidate_bundle=candidate_bundle,
            diff=diff,
            notes=(
                "在冻结 fixture 上完整重跑；工具契约已重新校验，"
                "不使用任何外部网络或生产数据。",
            ),
        )

    # -- 内部 --------------------------------------------------------------

    def _build_real_registry(self, stack: Any) -> Any:
        from app.evaluation.agent_eval_runtime import build_eval_read_services
        from app.harness.database_tools import register_database_tools
        from app.harness.intelligence_tools import register_intelligence_tools
        from app.harness.skills import SkillRegistry
        from app.harness.tool_factory import build_tool_registry
        from app.infrastructure.crawler.demo import DemoCrawlerAdapter
        from app.infrastructure.embeddings import EmbeddingWorkerClient

        # 与 suite runner 同一套生产只读服务装配（FC-IR-01）：契约一致，
        # 且 full rerun 场景下 DB / Intelligence 工具真实读取冻结 fixture。
        agent_database, intelligence = build_eval_read_services(stack)
        registry = build_tool_registry(
            DemoCrawlerAdapter(),
            SkillRegistry(),
            stack.knowledge,
            EmbeddingWorkerClient("http://localhost:1", dimensions=1024, timeout_seconds=1),
            stack.social,
            stack.repository,
        )
        register_database_tools(registry, agent_database)
        register_intelligence_tools(registry, intelligence)
        return registry

    def _default_candidate_gateway(
        self,
        task: Any,
        seeded: Any,
        real_tools: Any,
        source_bundle: ReplayBundle,
    ) -> Any:
        """contract 模式下的 candidate：沿用该任务的声明式脚本。"""
        from app.evaluation.agent_eval import ContractGateway

        specs = {name: real_tools.get(name) for name in real_tools.names()}
        steps = build_contract_steps(task, seeded, specs)
        return ContractGateway(steps)

    async def _execute(
        self,
        *,
        stack: Any,
        task: Any,
        seeded: Any,
        gateway: Any,
        tools: Any,
    ) -> AgentTrace:
        from app.application.agent_service import AgentRunService
        from app.application.graph_worker import GraphWorker
        from app.evaluation.agent_trace import collect_trace
        from app.harness.skills import SkillRegistry
        from langgraph.checkpoint.memory import MemorySaver

        case_id = seeded.case_id(task.case_id)
        worker = GraphWorker(
            stack.repository,
            gateway,
            tools,
            SkillRegistry(),
            worker_id=f"replay-{task.id}",
            poll_interval_seconds=0.05,
            lease_seconds=60,
            max_turns=task.budgets.max_agent_steps,
            max_tool_calls=task.budgets.max_tool_calls,
            max_cost=1.0,
            checkpointer=MemorySaver(),
            social=stack.social,
        )
        service = AgentRunService(stack.repository, worker)
        run = await service.start(
            case_id=case_id, content=task.user_prompt, approve_crawl=False
        )
        await worker.start()
        try:
            import asyncio
            import time

            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                current = await stack.repository.get_agent_run(run.id)
                if current.status in {
                    "completed",
                    "failed",
                    "cancelled",
                    "waiting_approval",
                }:
                    # 等 runtime 收尾（见 agent_eval._settle 的同类问题）。
                    for _ in range(60):
                        current = await stack.repository.get_agent_run(run.id)
                        if not current.lease_owner:
                            break
                        await asyncio.sleep(0.1)
                    await asyncio.sleep(0.3)
                    break
                await asyncio.sleep(0.1)
        finally:
            await worker.stop()
        return await collect_trace(
            stack.repository, run.id, finding_repository=stack.finding_repository
        )

    def _bundle_from_trace(
        self, *, trace: AgentTrace, task: Any, gateway: Any, tools: Any
    ) -> ReplayBundle:
        from app.evaluation.agent_manifest import (
            build_bundle,
            production_coordinator_prompt_hash,
            tool_schema_hash,
        )

        return build_bundle(
            trace=trace,
            task_id=task.id,
            suite_version=self._suite.suite_version,
            mode=self._mode,
            git_sha=_git_sha(),
            model_name=getattr(gateway, "model_name", "unknown"),
            # FC-IR-04：provenance 必须来自生产 coordinator prompt，
            # 而不是 Golden expected behavior。
            coordinator_prompt_hash=production_coordinator_prompt_hash(),
            schema_hash=tool_schema_hash(tools),
            expected_case_id=trace.case_id,
        )

    def _bundle_from_task_result(
        self, result: Any, source_bundle: ReplayBundle, *, report: Any
    ) -> ReplayBundle:
        """从 suite 运行结果构造 bundle（full rerun 用）。

        FC-IR-04：candidate 的 ``tool_schema_hash`` 与
        ``coordinator_prompt_hash`` 必须来自本次重跑的真实装配
        （runner 报告），禁止照抄 source bundle——否则 schema_changed
        永远是假阴性。
        """
        payload = result.trace_bundle
        metrics = dict(payload.get("metrics") or {})
        return ReplayBundle(
            run_id=result.run_id,
            task_id=result.task_id,
            suite_version=source_bundle.suite_version,
            mode=self._mode,
            user_prompt=source_bundle.user_prompt,
            case_id=result.case_id,
            git_sha=_git_sha(),
            model_name=source_bundle.model_name,
            coordinator_prompt_hash=(
                report.coordinator_prompt_hash
                or source_bundle.coordinator_prompt_hash
            ),
            tool_schema_hash=(
                report.tool_schema_hash or source_bundle.tool_schema_hash
            ),
            tool_sequence=tuple(
                item["tool"] for item in payload.get("tool_calls", [])
            ),
            tool_arguments=tuple(
                dict(item.get("arguments") or {})
                for item in payload.get("tool_calls", [])
            ),
            tool_observations=tuple(
                {
                    "tool": item["tool"],
                    "status": item.get("status"),
                    "result": None,
                    "error_code": item.get("error_code"),
                    "duration_ms": item.get("duration_ms"),
                    "cached": item.get("cached", False),
                    "approval_id": item.get("approval_id"),
                }
                for item in payload.get("tool_calls", [])
            ),
            final_response=str(payload.get("final_answer") or ""),
            artifact_kinds=tuple(item["kind"] for item in payload.get("artifacts", [])),
            metrics={
                "tool_call_count": metrics.get("tool_call_count", 0),
                "input_tokens": metrics.get("input_tokens", 0),
                "output_tokens": metrics.get("output_tokens", 0),
                "estimated_cost": metrics.get("estimated_cost", 0.0),
                "latency_ms": metrics.get("latency_ms", result.latency_ms),
                "run_status": result.status,
            },
            failure_categories=tuple(result.failure_categories),
        )


def render_replay_markdown(result: ReplayResult) -> str:
    lines = [result.diff.to_markdown()]
    if result.notes:
        lines.append("## Notes")
        lines.append("")
        lines.extend(f"- {note}" for note in result.notes)
        lines.append("")
    return "\n".join(lines)


def write_replay_artifacts(result: ReplayResult, directory: Any) -> dict[str, str]:
    """落地 replay JSON + Markdown（供 CI artifact 上传）。"""
    from pathlib import Path

    base = Path(directory)
    base.mkdir(parents=True, exist_ok=True)
    json_path = base / "replay_diff.json"
    md_path = base / "replay_diff.md"
    json_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(render_replay_markdown(result), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}
