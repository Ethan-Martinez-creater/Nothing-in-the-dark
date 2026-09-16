"""Agent 运行轨迹与状态快照 —— evaluator 与 replay 的共同数据基础。

只从现有持久化结构读取（``agent_runs`` / ``run_events`` / ``tool_calls`` /
``model_calls`` / ``approvals`` / ``artifacts`` / ``findings`` / ``review_items``），
不新增表、不新增写入路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

#: agent run 的终态与非终态（与 models.AgentRunRecord.status 取值一致）。
TERMINAL_RUN_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "cancelled"}
)
#: 评测可接受的“停等”状态：高风险工具触发审批中断时 run 停在这里。
SUSPENDED_RUN_STATUSES: frozenset[str] = frozenset({"waiting_approval"})


@dataclass(frozen=True, slots=True)
class ToolCallView:
    """一次工具调用的可评测视图。"""

    id: str
    tool: str
    status: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error_code: str | None = None
    duration_ms: int | None = None
    approval_id: str | None = None
    cached: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "tool": self.tool,
            "status": self.status,
            "arguments": self.arguments,
            "error_code": self.error_code,
            "duration_ms": self.duration_ms,
            "approval_id": self.approval_id,
            "cached": self.cached,
        }


@dataclass(frozen=True, slots=True)
class ArtifactView:
    id: str
    kind: str
    title: str
    version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class FindingView:
    id: str
    kind: str
    status: str
    title: str
    statement: str = ""
    evidence_refs: tuple[str, ...] = ()
    source_run_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "title": self.title,
            "evidence_refs": list(self.evidence_refs),
            "source_run_id": self.source_run_id,
        }


@dataclass(frozen=True, slots=True)
class ApprovalView:
    id: str
    action: str
    status: str
    risk_level: str | None = None
    scope: str | None = None
    approval_type: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "action": self.action,
            "status": self.status,
            "risk_level": self.risk_level,
            "scope": self.scope,
            "approval_type": self.approval_type,
        }


@dataclass(frozen=True, slots=True)
class AgentTrace:
    """一次 agent run 的完整可评测轨迹。"""

    run_id: str
    case_id: str
    status: str
    objective: str
    final_answer: str
    tool_calls: tuple[ToolCallView, ...] = ()
    artifacts: tuple[ArtifactView, ...] = ()
    #: 专家子 run 的工具调用（不参与主 run 的 required/forbidden 判定，
    #: 但完整记录了委派之后真实发生了什么）。
    child_tool_calls: tuple[ToolCallView, ...] = ()
    findings: tuple[FindingView, ...] = ()
    approvals: tuple[ApprovalView, ...] = ()
    event_types: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    tool_call_count: int = 0
    error_code: str | None = None
    agent: str = "coordinator"

    # -- 便捷查询 ---------------------------------------------------------

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(call.tool for call in self.tool_calls)

    def calls_for(self, tool: str) -> tuple[ToolCallView, ...]:
        return tuple(call for call in self.tool_calls if call.tool == tool)

    def called(self, tool: str) -> bool:
        return any(call.tool == tool for call in self.tool_calls)

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES

    def total_duration_ms(self) -> int:
        return sum(call.duration_ms or 0 for call in self.tool_calls)

    def has_event(self, event_type: str) -> bool:
        return event_type in self.event_types

    def tool_call_statuses(self, tool: str) -> tuple[str, ...]:
        return tuple(call.status for call in self.calls_for(tool))

    def to_bundle(self) -> dict[str, object]:
        """Replay Bundle 的最小可用形态（Phase 4 会补充 observation）。"""
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "status": self.status,
            "objective": self.objective,
            "final_answer": self.final_answer,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "artifacts": [item.to_dict() for item in self.artifacts],
            "findings": [item.to_dict() for item in self.findings],
            "approvals": [item.to_dict() for item in self.approvals],
            "metrics": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "estimated_cost": self.estimated_cost,
                "tool_call_count": self.tool_call_count,
                "latency_ms": self.total_duration_ms(),
            },
        }


# ---------------------------------------------------------------------------
# 状态快照（E5 State Mutation Correctness 的输入）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaseStateSnapshot:
    """运行前后的 case 状态切片，用于检测非预期变更。"""

    case_id: str
    finding_status: dict[str, str] = field(default_factory=dict)
    review_status: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "finding_status": dict(self.finding_status),
            "review_status": dict(self.review_status),
            "counts": dict(self.counts),
        }


@dataclass(frozen=True, slots=True)
class StateDiff:
    """两次快照之间的差异。"""

    changed_findings: dict[str, tuple[str, str]] = field(default_factory=dict)
    changed_reviews: dict[str, tuple[str, str]] = field(default_factory=dict)
    count_deltas: dict[str, int] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return (
            not self.changed_findings
            and not self.changed_reviews
            and not any(self.count_deltas.values())
        )

    def mutated_counts(self) -> dict[str, int]:
        return {key: value for key, value in self.count_deltas.items() if value}

    def to_dict(self) -> dict[str, object]:
        return {
            "changed_findings": {
                key: {"before": value[0], "after": value[1]}
                for key, value in self.changed_findings.items()
            },
            "changed_reviews": {
                key: {"before": value[0], "after": value[1]}
                for key, value in self.changed_reviews.items()
            },
            "count_deltas": dict(self.count_deltas),
        }


def diff_snapshots(before: CaseStateSnapshot, after: CaseStateSnapshot) -> StateDiff:
    changed_findings: dict[str, tuple[str, str]] = {}
    for finding_id, status in before.finding_status.items():
        if finding_id in after.finding_status and after.finding_status[finding_id] != status:
            changed_findings[finding_id] = (status, after.finding_status[finding_id])
    changed_reviews: dict[str, tuple[str, str]] = {}
    for item_id, status in before.review_status.items():
        if item_id in after.review_status and after.review_status[item_id] != status:
            changed_reviews[item_id] = (status, after.review_status[item_id])
    count_deltas: dict[str, int] = {}
    for key in set(before.counts) | set(after.counts):
        delta = after.counts.get(key, 0) - before.counts.get(key, 0)
        if delta:
            count_deltas[key] = delta
    return StateDiff(
        changed_findings=changed_findings,
        changed_reviews=changed_reviews,
        count_deltas=count_deltas,
    )


async def capture_state(
    repository: Any,
    case_id: str,
    *,
    finding_repository: Any,
    social_repository: Any | None = None,
) -> CaseStateSnapshot:
    """读取 case 的当前状态切片（只读）。

    ``finding_repository`` / ``social_repository`` 由调用方注入，避免从
    repository 内部挖私有属性。
    """
    findings = await finding_repository.list(case_id, limit=500)
    review_items = await repository.list_review_items(case_id, limit=500)
    claims = await repository.list_claims_by_case(case_id, limit=500)
    evidence = await repository.list_evidence_by_case(case_id, limit=500)
    artifacts = await repository.list_artifacts(case_id)
    counts = {
        "claims": len(claims),
        "evidence": len(evidence),
        "artifacts": len(artifacts),
    }
    if social_repository is not None:
        counts["posts"] = await social_repository.count_posts(case_id)

    return CaseStateSnapshot(
        case_id=case_id,
        finding_status={item.id: item.status for item in findings},
        review_status={item.id: item.status for item in review_items},
        counts=counts,
    )


# ---------------------------------------------------------------------------
# 轨迹抽取
# ---------------------------------------------------------------------------


async def _final_answer(repository: Any, case_id: str, run: Any) -> str:
    """最终回答文本：取该 case 最后一条 assistant turn。"""
    turns = await repository.list_turns(case_id)
    for turn in reversed(list(turns)):
        if getattr(turn, "role", "") == "assistant":
            return str(getattr(turn, "content", "") or "")
    return ""


async def collect_trace(
    repository: Any, run_id: str, *, finding_repository: Any
) -> AgentTrace:
    """从持久化结构抽取一次 run 的完整轨迹（只读，无副作用）。"""
    trace = await repository.get_run_trace(run_id)
    run = trace["run"]
    tool_calls = tuple(
        ToolCallView(
            id=record.id,
            tool=record.tool_name,
            status=record.status,
            arguments=dict(record.arguments or {}),
            result=dict(record.result) if isinstance(record.result, dict) else None,
            error_code=record.error_code,
            duration_ms=record.duration_ms,
            approval_id=record.approval_id,
            cached=bool(record.cached),
        )
        for record in trace["tool_calls"]
    )
    approvals = tuple(
        ApprovalView(
            id=record.id,
            action=record.action,
            status=record.status,
            risk_level=record.risk_level,
            scope=record.scope,
            approval_type=record.approval_type,
        )
        for record in trace["approvals"]
    )
    artifact_records = list(await repository.list_run_artifacts(run_id))
    # 专家子 run 的产出同样属于本次任务的结果（dispatch_expert → 专家 artifact）。
    child_tool_calls: list[Any] = []
    for child in await repository.list_child_runs(run_id):
        artifact_records.extend(await repository.list_run_artifacts(child.id))
        child_trace = await repository.get_run_trace(child.id)
        child_tool_calls.extend(child_trace["tool_calls"])
    artifacts = tuple(
        ArtifactView(
            id=record.id,
            kind=record.kind,
            title=record.title,
            version=int(record.version or 1),
        )
        for record in artifact_records
    )
    findings = tuple(
        FindingView(
            id=record.id,
            kind=record.kind,
            status=record.status,
            title=record.title,
            statement=record.statement,
            evidence_refs=tuple(_evidence_refs(record)),
            source_run_id=record.source_run_id,
        )
        for record in await finding_repository.list(run.case_id, limit=500)
    )
    return AgentTrace(
        run_id=run.id,
        case_id=run.case_id,
        status=run.status,
        objective=run.objective,
        final_answer=await _final_answer(repository, run.case_id, run),
        tool_calls=tool_calls,
        artifacts=artifacts,
        child_tool_calls=tuple(
            ToolCallView(
                id=record.id,
                tool=record.tool_name,
                status=record.status,
                arguments=dict(record.arguments or {}),
                result=dict(record.result) if isinstance(record.result, dict) else None,
                error_code=record.error_code,
                duration_ms=record.duration_ms,
                approval_id=record.approval_id,
                cached=bool(record.cached),
            )
            for record in child_tool_calls
        ),
        findings=findings,
        approvals=approvals,
        event_types=tuple(record.event_type for record in trace["events"]),
        input_tokens=int(run.input_tokens or 0),
        output_tokens=int(run.output_tokens or 0),
        estimated_cost=float(run.estimated_cost or 0.0),
        tool_call_count=int(run.tool_call_count or 0),
        error_code=run.error_code,
        agent=run.agent,
    )


def _evidence_refs(record: Any) -> Iterable[str]:
    links = getattr(record, "evidence_links", None)
    if isinstance(links, Sequence) and not isinstance(links, (str, bytes)):
        for item in links:
            if isinstance(item, dict):
                ref = item.get("evidence_ref") or item.get("evidence_id")
                if ref:
                    yield str(ref)
            elif item:
                yield str(item)


@dataclass(frozen=True, slots=True)
class ReviewItemView:
    id: str
    object_type: str
    object_id: str
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "status": self.status,
        }


def review_item_views(records: Iterable[Any]) -> tuple[ReviewItemView, ...]:
    return tuple(
        ReviewItemView(
            id=record.id,
            object_type=record.object_type,
            object_id=record.object_id,
            status=record.status,
        )
        for record in records
    )
