"""interview_agent_v1 —— 版本化 Agent Golden Dataset 的 schema、加载与校验。

设计约束（来自 Interview Readiness 主计划第 10–18 节）：

* 固定 24 个任务、6 个类别各 4 个，不追求大而全；
* 每个任务声明 required / optional / forbidden tools、artifact 期望、
  state assertion、citation 期望、answer 约束与预算；
* fixture 必须冻结，评测时不得访问公网；
* 期望行为变更必须升级 task 或 suite 版本，不得静默改 baseline。

本模块只负责**数据契约**：不连数据库、不调模型、不执行 agent。
真正的执行与评分在 ``app.evaluation.agent_eval`` / ``agent_evaluators``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

SUITE_VERSION = "interview_agent_v1"

#: 6 个固定类别，每类 4 个任务。
CATEGORY_DATABASE_GROUNDING = "G1"
CATEGORY_TOOL_ROUTING = "G2"
CATEGORY_EVIDENCE_GROUNDING = "G3"
CATEGORY_CROSS_INVESTIGATION = "G4"
CATEGORY_SAFETY_APPROVAL = "G5"
CATEGORY_UNCERTAINTY = "G6"

CATEGORY_TITLES: dict[str, str] = {
    CATEGORY_DATABASE_GROUNDING: "Database Grounding",
    CATEGORY_TOOL_ROUTING: "Tool Routing",
    CATEGORY_EVIDENCE_GROUNDING: "Evidence / Finding Grounding",
    CATEGORY_CROSS_INVESTIGATION: "Cross-Investigation Intelligence",
    CATEGORY_SAFETY_APPROVAL: "Safety / Approval / Mutation",
    CATEGORY_UNCERTAINTY: "Uncertainty / Missing Data",
}

CATEGORIES: tuple[str, ...] = tuple(CATEGORY_TITLES)

#: 每个类别的任务数（计划固定 4）。
TASKS_PER_CATEGORY = 4
EXPECTED_TASK_COUNT = TASKS_PER_CATEGORY * len(CATEGORIES)

#: state assertion 允许的 kind（deterministic evaluator 消费）。
ASSERTION_KINDS: frozenset[str] = frozenset(
    {
        # 只读任务：整个 case 不得产生任何写操作
        "no_mutation",
        # artifact 是否存在（target = artifact kind）
        "artifact_exists",
        "artifact_absent",
        # finding 是否存在（target = finding kind，如 opinion/verification）
        "finding_exists",
        "finding_absent",
        # finding 状态（target = finding kind，expected = 状态字符串）
        "finding_status",
        # review item 是否存在（target = object_type）
        "review_item_exists",
        # 具体 tool 是否被调用（target = tool name）
        "tool_called",
        "tool_not_called",
        # 工具调用最终状态（target = tool name，expected = succeeded/failed/...）
        "tool_call_status",
        # run 终态（target 固定 "run"）
        "run_status",
    }
)


class AgentDatasetError(ValueError):
    """数据集不合法（schema、版本、引用或分布问题）。"""


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _str_tuple(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in _as_tuple(value) if str(item))


# ---------------------------------------------------------------------------
# 期望行为
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StateAssertion:
    """一条对运行前后状态的确定性断言。

    ``kind`` 取自 :data:`ASSERTION_KINDS`；``target`` 是作用对象（artifact kind、
    finding kind、tool 名等）；``expected`` 是期望值（多数情况是布尔或状态串）。
    """

    kind: str
    target: str = ""
    expected: object = True
    description: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "target": self.target,
            "expected": self.expected,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StateAssertion:
        return cls(
            kind=str(payload.get("kind") or ""),
            target=str(payload.get("target") or ""),
            expected=payload.get("expected", True),
            description=str(payload.get("description") or ""),
        )


@dataclass(frozen=True, slots=True)
class AgentExpectedBehavior:
    """任务期望行为（deterministic evaluator 的判定依据）。"""

    required_tools: tuple[str, ...] = ()
    optional_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    required_artifact_types: tuple[str, ...] = ()
    expected_case_scope: str | None = None
    required_state_assertions: tuple[StateAssertion, ...] = ()
    expected_citation_refs: tuple[str, ...] = ()
    answer_must_contain: tuple[str, ...] = ()
    answer_must_not_contain: tuple[str, ...] = ()
    requires_human_escalation: bool | None = None
    #: 任务级的工具参数声明（如 dispatch_expert 的 agent/instructions）。
    #: Tier A 用它生成 scripted 调用；E4 用它校验参数是否符合期望。
    expected_tool_arguments: dict[str, dict[str, object]] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "required_tools": list(self.required_tools),
            "optional_tools": list(self.optional_tools),
            "forbidden_tools": list(self.forbidden_tools),
            "required_artifact_types": list(self.required_artifact_types),
            "expected_case_scope": self.expected_case_scope,
            "required_state_assertions": [
                item.to_dict() for item in self.required_state_assertions
            ],
            "expected_citation_refs": list(self.expected_citation_refs),
            "answer_must_contain": list(self.answer_must_contain),
            "answer_must_not_contain": list(self.answer_must_not_contain),
            "requires_human_escalation": self.requires_human_escalation,
            "expected_tool_arguments": {
                key: dict(value) for key, value in self.expected_tool_arguments.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AgentExpectedBehavior:
        return cls(
            required_tools=_str_tuple(payload.get("required_tools")),
            optional_tools=_str_tuple(payload.get("optional_tools")),
            forbidden_tools=_str_tuple(payload.get("forbidden_tools")),
            required_artifact_types=_str_tuple(payload.get("required_artifact_types")),
            expected_case_scope=(
                str(payload["expected_case_scope"])
                if payload.get("expected_case_scope")
                else None
            ),
            required_state_assertions=tuple(
                StateAssertion.from_dict(item)
                for item in _as_tuple(payload.get("required_state_assertions"))
                if isinstance(item, Mapping)
            ),
            expected_citation_refs=_str_tuple(payload.get("expected_citation_refs")),
            answer_must_contain=_str_tuple(payload.get("answer_must_contain")),
            answer_must_not_contain=_str_tuple(payload.get("answer_must_not_contain")),
            requires_human_escalation=(
                bool(payload["requires_human_escalation"])
                if payload.get("requires_human_escalation") is not None
                else None
            ),
            expected_tool_arguments={
                str(key): dict(value)
                for key, value in (payload.get("expected_tool_arguments") or {}).items()
                if isinstance(value, Mapping)
            },
        )


@dataclass(frozen=True, slots=True)
class AgentEvalBudget:
    """任务预算。Contract Eval 不校验 cost/token（不调真实模型）。"""

    max_agent_steps: int = 8
    max_tool_calls: int = 12
    max_latency_ms: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost_usd: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "max_agent_steps": self.max_agent_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_latency_ms": self.max_latency_ms,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_cost_usd": self.max_cost_usd,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> AgentEvalBudget:
        data = dict(payload or {})
        return cls(
            max_agent_steps=int(data.get("max_agent_steps") or 8),
            max_tool_calls=int(data.get("max_tool_calls") or 12),
            max_latency_ms=(
                int(data["max_latency_ms"]) if data.get("max_latency_ms") else None
            ),
            max_input_tokens=(
                int(data["max_input_tokens"]) if data.get("max_input_tokens") else None
            ),
            max_output_tokens=(
                int(data["max_output_tokens"]) if data.get("max_output_tokens") else None
            ),
            max_cost_usd=(
                float(data["max_cost_usd"]) if data.get("max_cost_usd") else None
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentGoldenTask:
    """一个 Golden Task。"""

    id: str
    category: str
    title: str
    user_prompt: str
    fixture_id: str
    expected: AgentExpectedBehavior
    budgets: AgentEvalBudget = field(default_factory=AgentEvalBudget)
    suite_version: str = SUITE_VERSION
    case_id: str | None = None
    critical: bool = False
    tags: tuple[str, ...] = ()
    what_it_validates: str = ""
    task_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "suite_version": self.suite_version,
            "task_version": self.task_version,
            "category": self.category,
            "title": self.title,
            "user_prompt": self.user_prompt,
            "fixture_id": self.fixture_id,
            "case_id": self.case_id,
            "critical": self.critical,
            "tags": list(self.tags),
            "what_it_validates": self.what_it_validates,
            "expected": self.expected.to_dict(),
            "budgets": self.budgets.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AgentGoldenTask:
        return cls(
            id=str(payload.get("id") or ""),
            category=str(payload.get("category") or ""),
            title=str(payload.get("title") or ""),
            user_prompt=str(payload.get("user_prompt") or ""),
            fixture_id=str(payload.get("fixture_id") or ""),
            expected=AgentExpectedBehavior.from_dict(
                payload.get("expected") or {}
            ),
            budgets=AgentEvalBudget.from_dict(payload.get("budgets")),
            suite_version=str(payload.get("suite_version") or SUITE_VERSION),
            case_id=str(payload["case_id"]) if payload.get("case_id") else None,
            critical=bool(payload.get("critical") or False),
            tags=_str_tuple(payload.get("tags")),
            what_it_validates=str(payload.get("what_it_validates") or ""),
            task_version=int(payload.get("task_version") or 1),
        )


@dataclass(frozen=True, slots=True)
class AgentGoldenSuite:
    """加载后的完整数据集：manifest + tasks + 冻结 fixture 定义。"""

    suite_version: str
    manifest: dict[str, Any]
    tasks: tuple[AgentGoldenTask, ...]
    fixtures: dict[str, Any]
    root: Path

    def by_id(self, task_id: str) -> AgentGoldenTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise AgentDatasetError(f"Unknown task id: {task_id}")

    def by_category(self, category: str) -> tuple[AgentGoldenTask, ...]:
        return tuple(t for t in self.tasks if t.category == category)

    def critical_tasks(self) -> tuple[AgentGoldenTask, ...]:
        return tuple(t for t in self.tasks if t.critical)

    def category_counts(self) -> dict[str, int]:
        counts = {category: 0 for category in CATEGORIES}
        for task in self.tasks:
            counts[task.category] = counts.get(task.category, 0) + 1
        return counts

    def summary_rows(self) -> list[dict[str, object]]:
        """给 docs/interview/golden-dataset.md 用的精简表格数据。"""
        rows: list[dict[str, object]] = []
        for task in self.tasks:
            rows.append(
                {
                    "id": task.id,
                    "category": task.category,
                    "category_title": CATEGORY_TITLES.get(task.category, task.category),
                    "title": task.title,
                    "prompt": task.user_prompt,
                    "validates": task.what_it_validates,
                    "required_tools": list(task.expected.required_tools),
                    "forbidden_tools": list(task.expected.forbidden_tools),
                    "critical": task.critical,
                    "tags": list(task.tags),
                }
            )
        return rows


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_suite(
    suite: AgentGoldenSuite,
    *,
    known_tools: Iterable[str] | None = None,
    enforce_counts: bool = True,
) -> list[str]:
    """返回问题列表（空 = 通过）。不抛异常，便于测试输出全部问题。"""
    problems: list[str] = []
    known = set(known_tools) if known_tools is not None else None

    if suite.suite_version != SUITE_VERSION:
        problems.append(
            f"suite_version mismatch: {suite.suite_version} != {SUITE_VERSION}"
        )

    seen_ids: set[str] = set()
    for task in suite.tasks:
        if not task.id:
            problems.append("task with empty id")
            continue
        if task.id in seen_ids:
            problems.append(f"duplicate task id: {task.id}")
        seen_ids.add(task.id)
        if task.category not in CATEGORIES:
            problems.append(f"{task.id}: unknown category {task.category!r}")
        if not task.user_prompt.strip():
            problems.append(f"{task.id}: empty user_prompt")
        if not task.fixture_id:
            problems.append(f"{task.id}: empty fixture_id")
        elif task.fixture_id not in suite.fixtures:
            problems.append(
                f"{task.id}: fixture_id {task.fixture_id!r} not declared in fixtures"
            )
        expected = task.expected
        required = set(expected.required_tools)
        optional = set(expected.optional_tools)
        forbidden = set(expected.forbidden_tools)
        overlap = required & forbidden
        if overlap:
            problems.append(
                f"{task.id}: tools both required and forbidden: {sorted(overlap)}"
            )
        both = (required | optional) & forbidden
        if both:
            problems.append(
                f"{task.id}: tools both allowed and forbidden: {sorted(both)}"
            )
        if not required and not expected.required_artifact_types and not expected.required_state_assertions:
            problems.append(
                f"{task.id}: no required tools, artifacts or state assertions"
            )
        for assertion in expected.required_state_assertions:
            if assertion.kind not in ASSERTION_KINDS:
                problems.append(
                    f"{task.id}: unknown state assertion kind {assertion.kind!r}"
                )
        if known is not None:
            for name in sorted(required | optional | forbidden):
                if name not in known:
                    problems.append(f"{task.id}: tool {name!r} not in registry")
        if task.budgets.max_agent_steps <= 0:
            problems.append(f"{task.id}: max_agent_steps must be positive")
        if task.budgets.max_tool_calls <= 0:
            problems.append(f"{task.id}: max_tool_calls must be positive")

    if enforce_counts:
        if len(suite.tasks) != EXPECTED_TASK_COUNT:
            problems.append(
                f"expected {EXPECTED_TASK_COUNT} tasks, found {len(suite.tasks)}"
            )
        for category, count in suite.category_counts().items():
            if count != TASKS_PER_CATEGORY:
                problems.append(
                    f"category {category} has {count} tasks (expected {TASKS_PER_CATEGORY})"
                )
    return problems


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------


def fixtures_root() -> Path:
    """冻结 fixture 根目录：backend/tests/fixtures/agent_eval。"""
    return Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "agent_eval"


def load_suite(
    root: Path | None = None,
    *,
    suite_version: str = SUITE_VERSION,
) -> AgentGoldenSuite:
    """从磁盘加载一个冻结 suite（manifest.json + tasks/*.json + fixtures.json）。"""
    base = (root or fixtures_root()) / suite_version
    manifest_path = base / "manifest.json"
    if not manifest_path.exists():
        raise AgentDatasetError(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    fixtures: dict[str, Any] = {}
    fixtures_path = base / "fixtures.json"
    if fixtures_path.exists():
        fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))

    tasks: list[AgentGoldenTask] = []
    tasks_dir = base / "tasks"
    if not tasks_dir.exists():
        raise AgentDatasetError(f"tasks dir not found: {tasks_dir}")
    for path in sorted(tasks_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        task = AgentGoldenTask.from_dict(payload)
        if not task.fixture_id:
            task = replace(task, fixture_id=str(payload.get("fixture_id") or ""))
        tasks.append(task)

    return AgentGoldenSuite(
        suite_version=str(manifest.get("suite_version") or suite_version),
        manifest=manifest,
        tasks=tuple(tasks),
        fixtures=fixtures,
        root=base,
    )
