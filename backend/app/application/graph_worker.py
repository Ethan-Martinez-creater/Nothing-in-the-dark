"""Durable Graph Worker.

The worker polls ``agent_runs``, atomically claims runs with a short lease,
and executes them through the checkpointed :class:`AgentLoopGraph`. Because
the graph persists its state in PostgreSQL, a service restart simply re-claims
the run and resumes from the last checkpoint instead of re-running the whole
agent loop.

Runs that pause for user approval flip to ``waiting_approval``; the
``approve``/``resume`` API flips them back to ``pending`` so this worker picks
them up again and resumes the interrupted tool call with the decision.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langgraph.types import Command

from app.application.context_builder import ContextBuilder
from app.application.conversation_summary import ConversationSummarizer
from app.application.memory_extraction import CaseMemoryExtractor
from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.graphs.agent_loop import AgentLoopGraph
from app.harness.agents import build_coordinator_definition, build_definition_for
from app.harness.hooks import HookBus
from app.harness.runtime import AgentDefinition, RuntimeContext
from app.harness.skills import SkillRegistry
from app.harness.structured_output import repair_json_content
from app.harness.tool_factory import _persist_propagation_edges
from app.harness.tools import ToolRegistry
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.llm import LLMGateway, LLMMessage
from app.telemetry import (
    TraceContext,
    current_trace,
    reset_trace,
    root_context,
    set_trace,
)

logger = logging.getLogger(__name__)

_RECOVERABLE_STATUSES = frozenset({"pending", "running", "waiting_approval"})

# Cap for the artifact data serialized into the follow-up system context.
_ARTIFACT_CONTEXT_CHARS = 4_000

# Expert run -> artifact kind persisted by the worker on completion.
_EXPERT_ARTIFACT_KINDS: dict[str, str] = {
    "opinion": "opinion_analysis",
    "propagation": "propagation_reconstruction",
    "verification": "fact_check",
    "evidence_critic": "evidence_review",
    "report": "report",
    "citation_validator": "citation_validation",
}


class GraphWorker:
    def __init__(
        self,
        repository: ApplicationRepository,
        gateway: LLMGateway,
        tools: ToolRegistry,
        skills: SkillRegistry,
        *,
        worker_id: str,
        poll_interval_seconds: float,
        lease_seconds: int,
        max_turns: int,
        max_tool_calls: int,
        max_cost: float,
        checkpointer: Any,
        context_builder: ContextBuilder | None = None,
        summarizer: ConversationSummarizer | None = None,
        extractor: CaseMemoryExtractor | None = None,
        social: SocialRepository | None = None,
        telemetry: Any = None,
        authorization: Any = None,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._tools = tools
        self._skills = skills
        self._social = social
        self._telemetry = telemetry
        # M21/M22: 一次性授权消费服务（resume 后工具执行前消费）。
        self._authorization = authorization
        self._worker_id = worker_id
        self._poll_interval = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._max_turns = max_turns
        self._max_tool_calls = max_tool_calls
        self._max_cost = max_cost
        self._checkpointer = checkpointer
        self._context_builder = context_builder
        self._summarizer = summarizer
        self._extractor = extractor
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        self._run_tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    def set_checkpointer(self, checkpointer: Any) -> None:
        self._checkpointer = checkpointer

    async def start(self) -> None:
        setup = getattr(self._checkpointer, "setup", None)
        if setup is not None:
            await setup()
        self._task = asyncio.create_task(
            self._loop(),
            name=f"graph-worker:{self._worker_id}",
        )

    async def stop(self) -> None:
        self._stopping = True
        for task in list(self._run_tasks.values()):
            task.cancel()
        if self._run_tasks:
            await asyncio.gather(*self._run_tasks.values(), return_exceptions=True)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def recover(self) -> None:
        """Pick up runs left by a previous process (crash or restart)."""
        await self.tick()

    async def tick(self, *, wait: bool = False) -> str | None:
        run = await self._repository.claim_agent_run(
            self._worker_id,
            self._lease_seconds,
        )
        if run is None:
            return None
        self._cancel_events[run.id] = asyncio.Event()
        task = asyncio.create_task(
            self._execute_with_cleanup(run.id),
            name=f"agent-run:{run.id}",
        )
        self._run_tasks[run.id] = task

        def _cleanup(_: asyncio.Task[None], run_id: str = run.id) -> None:
            self._run_tasks.pop(run_id, None)
            self._cancel_events.pop(run_id, None)

        task.add_done_callback(_cleanup)
        if wait:
            await task
        return run.id

    async def cancel(self, run_id: str) -> None:
        event = self._cancel_events.get(run_id)
        if event is not None:
            event.set()
        task = self._run_tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()

    async def _execute_with_cleanup(self, run_id: str) -> None:
        trace_token = None
        span = None
        span_error: str | None = None
        try:
            if self._telemetry is not None:
                run = await self._repository.get_agent_run(run_id)
                context = root_context(
                    attributes={
                        "run_id": run.id,
                        "case_id": run.case_id,
                        "agent": run.agent,
                        "parent_run_id": run.parent_run_id or "",
                    }
                )
                trace_token = set_trace(context)
                span = self._telemetry.tracer.start_span(
                    "agent.run",
                    attributes={
                        "run_id": run.id,
                        "case_id": run.case_id,
                        "agent": run.agent,
                    },
                    parent=context,
                )
                trace_token = set_trace(
                    TraceContext(
                        trace_id=span.trace_id,
                        span_id=span.span_id,
                        parent_span_id=span.parent_span_id,
                        attributes=dict(context.attributes),
                    )
                )
                self._telemetry.metrics.increment("agent.runs")
            await self._execute(run_id)
        except asyncio.CancelledError:
            span_error = "agent_run_cancelled"
            if self._telemetry is not None:
                self._telemetry.metrics.increment("agent.runs_cancelled")
            try:
                await self._repository.update_agent_run(run_id, status="cancelled")
                await self._repository.add_run_event(
                    run_id,
                    {
                        "event_type": "agent_cancelled",
                        "agent": "coordinator",
                        "status": "cancelled",
                    },
                )
            except Exception:
                logger.exception("could not mark run %s cancelled", run_id)
            raise
        except Exception as exc:
            span_error = (
                exc.code if isinstance(exc, ApplicationError) else "agent_run_failed"
            )
            if self._telemetry is not None:
                self._telemetry.metrics.increment("agent.runs_failed")
            logger.exception("agent run %s failed", run_id)
            await self._fail_run(run_id, exc)
        finally:
            try:
                if self._telemetry is not None and span is not None:
                    final_status = "unknown"
                    try:
                        current = await self._repository.get_agent_run(run_id)
                        final_status = current.status
                    except Exception:
                        logger.exception("could not load final run status for telemetry")
                    self._telemetry.tracer.end_span(
                        span,
                        error_code=span_error,
                        attributes={"final_status": final_status},
                    )
            finally:
                if trace_token is not None:
                    reset_trace(trace_token)
                await self._repository.release_agent_run(run_id, self._worker_id)

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("graph worker tick failed")
            await asyncio.sleep(self._poll_interval)

    async def _execute(self, run_id: str) -> None:
        run = await self._repository.get_agent_run(run_id)
        case = await self._repository.get_case(run.case_id)
        turns = await self._repository.list_turns(case.id)
        history = [
            LLMMessage(
                role="user" if turn.role == "user" else "assistant",
                content=turn.content,
            )
            for turn in turns
            if turn.id != run.turn_id and turn.role in {"user", "assistant"}
        ]

        async def emit(payload: dict[str, Any]) -> None:
            await self._persist_event(run_id, payload)

        async def steering_loader() -> list[str]:
            """Fold unconsumed steering instructions into the next model step.

            Loaded instructions are marked consumed *after* they have been
            read, so a worker crash between load and apply never double-
            applies them (consumed_at stays NULL until this coroutine runs).
            """
            records = await self._repository.list_unconsumed_steerings(run_id)
            if not records:
                return []
            await emit(
                {
                    "event_type": "steering_applied",
                    "agent": run.agent,
                    "status": "completed",
                    "steering_ids": [record.id for record in records],
                }
            )
            await self._repository.mark_steerings_consumed(run_id)
            return [record.content for record in records]

        definition = self._definition_for(run)
        metadata = run.metadata_json or {}
        approved_tools = (
            {"collect_social_posts"} if metadata.get("approve_crawl") else set()
        )
        if metadata.get("budget_approved"):
            approved_tools.add("budget_exceeded")
        context = RuntimeContext(
            run_id=run.id,
            case_id=case.id,
            turn_id=run.turn_id or "",
            approved_tools=approved_tools,
            metadata=dict(metadata),
        )
        if run.parent_run_id:
            # Typed message from the parent: the dispatch payload plus the
            # parent's own context so the expert works in isolation.
            dispatch_context = metadata.get("dispatch") or {}
        else:
            dispatch_context = None
        if self._context_builder is not None:
            built = await self._context_builder.build(
                case=case,
                run=run,
                history=history,
                skill_catalog=self._skills.describe(),
            )
            system_context = built.system_context
            history = built.history_window
            await emit(
                {
                    "event_type": "context_built",
                    "agent": run.agent,
                    "status": "completed",
                    "stats": built.stats,
                }
            )
        else:
            system_context = (
                f"案例 ID：{case.id}\n案例：{case.title}\n主题：{case.topic}\n"
                f"平台：{case.platforms}\n时间范围：{case.time_range}\n"
                + (
                    f"父 Agent 委派上下文："
                    f"{json.dumps(dispatch_context, ensure_ascii=False)}\n"
                    if dispatch_context is not None
                    else ""
                )
                + f"可按需加载的 Skill 目录：{self._skills.describe()}"
            )
        # M2 Artifact 追问：把目标 Artifact 的完整数据注入上下文，让模型
        # 基于该产出回答（跨 case 引用被忽略，不注入）。
        artifact_ref = (metadata or {}).get("artifact_ref")
        if artifact_ref:
            system_context += await self._artifact_followup_context(
                metadata, case.id
            )
        graph = AgentLoopGraph(
            gateway=self._gateway,
            tools=self._tools,
            hooks=HookBus(),
            event_sink=emit,
            definition=definition,
            context=context,
            checkpointer=self._checkpointer,
            # Steering targets the coordinator conversation; expert runs
            # produce fixed structured artifacts and never load steerings.
            steering_loader=None if run.parent_run_id else steering_loader,
            cancel_event=self._cancel_events.get(run_id),
            authorization=self._authorization,
        )
        config: dict[str, Any] = {"configurable": {"thread_id": run_id}}
        await self._repository.update_agent_run(run_id, status="running")

        snapshot = await graph.aget_state(config)
        if snapshot.interrupts:
            decided = await self._repository.get_latest_decided_approval(run_id)
            if decided is None:
                await self._persist_approval(run_id, snapshot.interrupts[0], agent=run.agent)
                await self._repository.update_agent_run(
                    run_id, status="waiting_approval"
                )
                return
            # M21/M22: 恢复执行时注入审批 id，工具执行前原子消费一次性授权。
            context.approval_id = decided.id
            decision = {
                "approved": decided.status in {"approved", "approved_with_edits"},
                "approval_id": decided.id,
                "reason": decided.reason,
                "edited_action": decided.edited_action,
            }
            state = await graph.ainvoke(Command(resume=decision), config)
        elif snapshot.values:
            state = await graph.ainvoke(None, config)
        else:
            initial = self._build_initial_state(
                case=case,
                run=run,
                history=history,
                definition=definition,
                system_context=system_context,
            )
            state = await graph.ainvoke(initial, config)

        interrupts = state.get("__interrupt__")
        if interrupts:
            await self._persist_approval(run_id, interrupts[0], agent=run.agent)
            await self._repository.update_agent_run(
                run_id, status="waiting_approval"
            )
            return

        if state.get("status") != "completed":
            raise ApplicationError(
                "Agent loop ended without completing",
                code="agent_loop_aborted",
            )
        current = await self._repository.get_agent_run(run_id)
        if current.status == "cancelled":
            return
        content = str(state.get("content") or "")
        if run.parent_run_id:
            # Expert runs still persist their structured artifact (and echo
            # it to the parent), but they also get an assistant turn so the
            # final answer shows inline in the conversation like the parent.
            await self._finalize_expert_run(run, case, content)
        answer_turn = await self._repository.add_turn(
            case.id, role="assistant", content=content
        )
        if run.parent_run_id:
            # 专家子 run 没有用户指令 turn：用最终回答 turn 关联，
            # 前端据此把 run 卡片与 finalContent 合并。
            await self._repository.update_agent_run(run.id, turn_id=answer_turn.id)
        else:
            if self._summarizer is not None:
                await self._summarizer.summarize(case_id=case.id, run_id=run.id)
            if self._extractor is not None:
                await self._extractor.extract(case_id=case.id, run_id=run.id)
        await self._repository.update_agent_run(
            run_id,
            status="completed",
            input_tokens=int(state.get("total_input_tokens") or 0),
            output_tokens=int(state.get("total_output_tokens") or 0),
            tool_call_count=int(state.get("tool_call_count") or 0),
            estimated_cost=float(state.get("total_cost") or 0),
        )
        if self._telemetry is not None:
            self._telemetry.metrics.increment("agent.runs_ok")
        try:
            from app.application.domain_ingest import persist_run_cost_summary

            await persist_run_cost_summary(self._repository, run_id, case.id)
        except Exception:
            logger.exception("cost summary persist failed for run %s", run_id)
        await emit(
            {
                "event_type": "agent_end",
                "agent": run.agent,
                "status": "completed",
                "model": state.get("model") or "",
                "usage": {
                    "input_tokens": int(state.get("total_input_tokens") or 0),
                    "output_tokens": int(state.get("total_output_tokens") or 0),
                    "estimated_cost": float(state.get("total_cost") or 0),
                    "currency": "CNY",
                },
            }
        )

    async def _artifact_followup_context(
        self,
        metadata: dict[str, Any],
        case_id: str,
    ) -> str:
        """Serialize the follow-up artifact's data for the system context.

        Returns an empty string when there is no artifact reference, the
        artifact is missing, or it belongs to another case (never inject
        another case's data).
        """
        artifact_ref = (metadata or {}).get("artifact_ref")
        if not artifact_ref:
            return ""
        try:
            artifact = await self._repository.get_artifact(
                str(artifact_ref["artifact_id"])
            )
        except Exception:
            logger.exception("artifact ref %s not found", artifact_ref)
            return ""
        if artifact.case_id != case_id:
            return ""
        serialized = json.dumps(dict(artifact.data), ensure_ascii=False)
        if len(serialized) > _ARTIFACT_CONTEXT_CHARS:
            serialized = serialized[:_ARTIFACT_CONTEXT_CHARS] + "…（已截断）"
        return (
            f"\n\n追问目标 Artifact（{artifact.kind} v{artifact.version}）：\n"
            f"{serialized}"
        )

    def _definition_for(self, run: Any) -> AgentDefinition:
        """Route an agent run to its own definition (expert or coordinator)."""
        if run.parent_run_id:
            return build_definition_for(
                run.agent,
                max_turns=self._max_turns,
                max_tool_calls=self._max_tool_calls,
                max_cost=self._max_cost,
            )
        return build_coordinator_definition(
            max_turns=self._max_turns,
            max_tool_calls=self._max_tool_calls,
            max_cost=self._max_cost,
        )

    async def _finalize_expert_run(
        self,
        run: Any,
        case: Any,
        content: str,
    ) -> None:
        """Persist an expert run's output as an artifact and mail the parent."""
        data = self._parse_json_content(content)
        kind = _EXPERT_ARTIFACT_KINDS.get(run.agent, run.agent)
        # 传播专家直接在子 run 里生成图并落 artifact（不经 coordinator 的
        # propagation 工具），必须走同一持久化路径回填 edge_id，否则前端
        # 传播边确认按钮无 id 可用（2026-08-08 冒烟发现）。
        if kind == "propagation_reconstruction" and self._social is not None:
            try:
                await _persist_propagation_edges(
                    data,
                    repository=self._repository,
                    social=self._social,
                    case_id=case.id,
                )
            except Exception:
                logger.exception(
                    "propagation edges persist failed for run %s", run.id
                )
        # 核查专家同样绕过 verify_claims 工具：核查卡只进 artifact 不落
        # claims/evidence 表，证据侧栏因此显示 0（2026-08-10 反馈）。
        if kind == "fact_check" and self._social is not None:
            try:
                await _persist_fact_check_cards(
                    data,
                    repository=self._repository,
                    social=self._social,
                    case_id=case.id,
                    run_id=run.id,
                )
            except Exception:
                logger.exception(
                    "fact check persist failed for run %s", run.id
                )
        from app.application.domain_ingest import artifact_references

        if isinstance(data, dict) and "references" not in data:
            data = {**data, "references": artifact_references(data)}
        artifact = await self._repository.create_artifact(
            case_id=case.id,
            run_id=run.id,
            kind=kind,
            title=f"案例「{case.title}」的 {run.agent} 分析结果",
            data=data,
        )
        await self._repository.add_agent_message(
            sender_run_id=run.id,
            receiver_run_id=run.parent_run_id,
            message_type="expert_completed",
            payload={
                "artifact_id": artifact.id,
                "artifact_kind": kind,
                "version": artifact.version,
                "status": "completed",
                "content_summary": content[:500],
            },
        )
        await self._repository.add_run_event(
            run.id,
            {
                "event_type": "expert_artifact_created",
                "agent": run.agent,
                "status": "completed",
                "artifact_id": artifact.id,
                "artifact_kind": kind,
                "version": artifact.version,
            },
        )
        if run.parent_run_id:
            await self._repository.add_run_event(
                run.parent_run_id,
                {
                    "event_type": "expert_completed",
                    "agent": run.agent,
                    "status": "completed",
                    "child_run_id": run.id,
                    "artifact_id": artifact.id,
                    "artifact_kind": kind,
                    "version": artifact.version,
                },
            )



    @staticmethod
    def _parse_json_content(content: str) -> dict[str, object]:
        """Parse an expert's structured JSON output with automatic repair.

        Lenient extraction (fenced blocks, prose-wrapped objects) is
        delegated to ``repair_json_content``; unparseable text falls back
        to the raw content flagged ``parsed: false`` — never fabricated.
        """
        repaired = repair_json_content(content)
        if repaired is not None:
            return repaired
        return {"raw_content": content, "parsed": False}

    @staticmethod
    def _build_initial_state(
        *,
        case: Any,
        run: Any,
        history: list[LLMMessage],
        definition: Any,
        system_context: str,
    ) -> dict[str, Any]:
        messages = list(history)
        messages.insert(
            0,
            LLMMessage(
                role="system",
                content=(
                    f"{definition.instructions}\n\n{system_context}"
                ).strip(),
            ),
        )
        messages.append(LLMMessage(role="user", content=run.objective))
        return {
            "messages": [message.model_dump() for message in messages],
            "turn": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost": 0.0,
            "tool_call_count": 0,
            "status": "running",
        }

    async def _persist_event(self, run_id: str, payload: dict[str, Any]) -> None:
        # M19: 事件关联当前 trace（不改变 SSE 语义）。
        trace = current_trace()
        if trace is not None and not payload.get("trace_id"):
            payload = dict(payload)
            payload["trace_id"] = trace.trace_id
        event_type = payload.get("event_type")
        if event_type == "model_call_end":
            usage = payload.get("usage")
            if isinstance(usage, dict):
                await self._repository.add_model_call(
                    call_id=str(payload["model_call_id"]),
                    run_id=run_id,
                    model=str(payload.get("model") or ""),
                    route=str(payload.get("route") or "fast"),
                    input_tokens=int(usage.get("input_tokens") or 0),
                    cached_input_tokens=int(
                        usage.get("cached_input_tokens") or 0
                    ),
                    output_tokens=int(usage.get("output_tokens") or 0),
                    estimated_cost=float(payload.get("estimated_cost") or 0),
                    currency=str(payload.get("currency") or "CNY"),
                    pricing_model=(
                        str(payload["pricing_model"])
                        if payload.get("pricing_model")
                        else None
                    ),
                    latency_ms=int(payload.get("latency_ms") or 0),
                )
        elif event_type == "tool_execution_end":
            status = str(payload.get("status") or "completed")
            call_id = str(payload["tool_call_id"])
            record = await self._repository.add_tool_call(
                call_id=call_id,
                run_id=run_id,
                tool_name=str(payload["tool"]),
                skill_name=(
                    str(payload["skill_name"])
                    if payload.get("skill_name")
                    else None
                ),
                status=status,
                arguments=payload.get("arguments") or {},
                error_code=(
                    str(payload["error_code"])
                    if payload.get("error_code")
                    else None
                ),
                input_summary=(
                    str(payload["input_summary"])
                    if payload.get("input_summary")
                    else None
                ),
                output_summary=(
                    str(payload["output_summary"])
                    if payload.get("output_summary")
                    else None
                ),
                retry_count=int(payload.get("retry_count") or 0),
                retry_history=(
                    list(payload["retry_history"])
                    if payload.get("retry_history")
                    else None
                ),
                cached=bool(payload.get("cached")),
                duration_ms=int(payload.get("duration_ms") or 0),
                estimated_cost=float(payload.get("estimated_cost") or 0),
                idempotency_key=(
                    str(payload["idempotency_key"])
                    if payload.get("idempotency_key")
                    # 审批中断时 _persist_approval 已按同一 key 写入
                    # waiting_approval 行；恢复重放命中后走下方 update
                    # 写终态，避免同主键重复插入。
                    else f"{run_id}:{call_id}"
                ),
                approval_id=(
                    str(payload["approval_id"])
                    if payload.get("approval_id")
                    else None
                ),
                rag=(
                    dict(payload["rag"]) if payload.get("rag") else None
                ),
            )
            if record.status in {"running", "waiting_approval"}:
                # A pending record (e.g. created at approval time) must be
                # promoted to its final state instead of being duplicated.
                await self._repository.update_tool_call(
                    run_id,
                    record.id,
                    status=status,
                    error_code=(
                        str(payload["error_code"])
                        if payload.get("error_code")
                        else None
                    ),
                    output_summary=(
                        str(payload["output_summary"])
                        if payload.get("output_summary")
                        else None
                    ),
                    duration_ms=int(payload.get("duration_ms") or 0),
                    retry_count=int(payload.get("retry_count") or 0),
                    retry_history=(
                        list(payload["retry_history"])
                        if payload.get("retry_history")
                        else None
                    ),
                    cached=bool(payload.get("cached")),
                    estimated_cost=float(payload.get("estimated_cost") or 0),
                )
        await self._repository.add_run_event(run_id, payload)
        if (
            event_type == "tool_execution_end"
            and payload.get("tool") == "collect_social_posts"
            and payload.get("status") == "completed"
        ):
            arguments = payload.get("arguments")
            if isinstance(arguments, dict):
                from app.harness.approval_policy import crawl_scope

                await self._repository.patch_run_metadata(
                    run_id,
                    {"approved_crawl_scope": crawl_scope(arguments)},
                )

    async def _persist_approval(
        self,
        run_id: str,
        interrupt_value: Any,
        *,
        agent: str = "coordinator",
    ) -> None:
        """Write an approvals row for a pending LangGraph interrupt (idempotent)."""
        value = interrupt_value
        if hasattr(value, "value"):
            value = value.value
        action = str((value or {}).get("action") or "unknown_tool")
        reason = str((value or {}).get("reason") or "")
        request_payload = dict((value or {}).get("request_payload") or {})
        pending = await self._repository.list_pending_approvals(run_id)
        for approval in pending:
            if approval.action == action:
                return
        approval = await self._repository.create_approval(
            run_id=run_id,
            action=action,
            reason=reason,
            request_payload=request_payload,
        )
        tool_call = (value or {}).get("tool_call")
        if isinstance(tool_call, dict) and tool_call.get("id"):
            call_id = str(tool_call["id"])
            existing = await self._repository.list_run_tool_calls(run_id)
            if any(call.id == call_id for call in existing):
                await self._repository.update_tool_call(
                    run_id,
                    call_id,
                    status="waiting_approval",
                    approval_id=approval.id,
                )
            else:
                # Interrupted before any execution event: record the
                # blocked attempt so the trace stays complete.
                await self._repository.add_tool_call(
                    call_id=call_id,
                    run_id=run_id,
                    tool_name=action,
                    skill_name=None,
                    status="waiting_approval",
                    arguments=tool_call.get("arguments") or {},
                    input_summary=request_payload.get("arguments_summary"),
                    idempotency_key=f"{run_id}:{call_id}",
                    approval_id=approval.id,
                )
        await self._repository.add_run_event(
            run_id,
            {
                "event_type": "approval_pending",
                "agent": agent,
                "status": "waiting_approval",
                "tool_call_id": (
                    str(tool_call["id"])
                    if isinstance(tool_call, dict) and tool_call.get("id")
                    else None
                ),
                "tool": action,
                "approval_id": approval.id,
                "action": action,
                "reason": reason,
                "request_payload": request_payload,
            },
        )

    async def _fail_run(self, run_id: str, exc: Exception) -> None:
        code = exc.code if isinstance(exc, ApplicationError) else "agent_run_failed"
        message = str(exc)
        agent = "coordinator"
        parent_run_id: str | None = None
        try:
            run = await self._repository.get_agent_run(run_id)
            agent = run.agent
            parent_run_id = run.parent_run_id
        except Exception:
            pass
        await self._repository.update_agent_run(
            run_id,
            status="failed",
            error_code=code,
            error=message,
        )
        try:
            await self._repository.add_run_event(
                run_id,
                {
                    "event_type": "agent_error",
                    "agent": agent,
                    "status": "failed",
                    "error": {"code": code, "message": message},
                },
            )
        except Exception:
            logger.exception("could not record failure event for run %s", run_id)
        if parent_run_id:
            try:
                await self._repository.add_run_event(
                    parent_run_id,
                    {
                        "event_type": "expert_failed",
                        "agent": agent,
                        "status": "failed",
                        "child_run_id": run_id,
                        "error": {"code": code, "message": message},
                    },
                )
                await self._repository.add_agent_message(
                    sender_run_id=run_id,
                    receiver_run_id=parent_run_id,
                    message_type="expert_failed",
                    payload={
                        "agent": agent,
                        "error_code": code,
                        "error": message,
                    },
                )
            except Exception:
                logger.exception(
                    "could not notify parent %s of expert failure %s",
                    parent_run_id,
                    run_id,
                )



async def _persist_fact_check_cards(
    data: dict[str, object],
    *,
    repository: ApplicationRepository,
    social: SocialRepository,
    case_id: str,
    run_id: str,
) -> None:
    """把核查专家的核查卡持久化到 claims/evidence 表。

    专家路径绕过 verify_claims 工具时核查卡只进 artifact；证据侧栏
    按 claims/evidence 表展示，因此必须在此补写（2026-08-10 反馈：
    证据显示 0）。证据引用支持 social_post:{db_id} 前缀与原生 id。
    """
    posts = await social.list_posts_by_case(case_id)
    native_to_db = {str(post.native_id): post.id for post in posts}
    db_ids = {post.id for post in posts}

    def resolve_source(reference: str) -> str | None:
        if reference.startswith("social_post:"):
            db_id = reference[len("social_post:"):]
            return db_id if db_id in db_ids else None
        return native_to_db.get(reference)

    for card in data.get("cards") or []:
        if not isinstance(card, dict):
            continue
        text = str(card.get("claim") or "").strip()
        if not text:
            continue
        claim = await repository.create_claim(
            case_id=case_id,
            text=text,
            created_by_run_id=run_id,
        )
        verdict = str(card.get("verdict") or "open")
        if verdict in {"supported", "refuted", "insufficient", "misleading"}:
            await repository.update_claim_verdict(
                claim.id,
                verdict=verdict,
                status="closed",
                confidence=float(card.get("confidence") or 0),
            )
        reason = str(card.get("reason") or "")
        for reference, stance in (
            (card.get("supporting_evidence"), "support"),
            (card.get("contradicting_evidence"), "oppose"),
        ):
            for source in reference or []:
                source_id = resolve_source(str(source))
                if not source_id:
                    continue
                await repository.create_evidence(
                    case_id=case_id,
                    claim_id=claim.id,
                    source_type="post",
                    source_id=source_id,
                    stance=stance,
                    excerpt=reason[:500],
                    relevance=float(card.get("confidence") or 0),
                )