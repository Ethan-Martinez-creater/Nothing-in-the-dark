from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.graph_worker import GraphWorker
from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.harness.approval_policy import (
    APPROVAL_APPROVED,
    APPROVAL_APPROVED_WITH_EDITS,
    APPROVAL_CANCELLED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    crawl_scope,
    validate_approval_transition,
)

logger = logging.getLogger(__name__)


class AgentRunService:
    """Enqueue agent runs and drive approvals.

    Execution itself always goes through :class:`GraphWorker`: ``start`` only
    persists the run and lets the worker claim it, which makes runs durable
    across restarts. Approvals are persisted in the ``approvals`` table and
    the run is flipped back to ``pending`` so the worker resumes the exact
    interrupted tool call.
    """

    def __init__(
        self,
        repository: ApplicationRepository,
        worker: GraphWorker,
        collection_run_service: Any = None,
    ) -> None:
        self._repository = repository
        self._worker = worker
        self._collection_run_service = collection_run_service

    async def start(
        self,
        *,
        case_id: str,
        content: str,
        approve_crawl: bool,
        artifact_id: str | None = None,
        ui_context: dict[str, object] | None = None,
    ) -> Any:
        turn = await self._repository.add_turn(case_id, role="user", content=content)
        metadata: dict[str, object] = {"approve_crawl": approve_crawl}
        if artifact_id:
            # M2 Artifact 追问：worker 把目标 Artifact 数据注入上下文。
            metadata["artifact_ref"] = {"artifact_id": artifact_id}
        if ui_context:
            # M2.2 Contextual Copilot：结构化 UI 上下文进入 Run metadata，
            # 由 ContextBuilder 注入为独立 system block（不构成证据）。
            try:
                serialized = json.dumps(ui_context, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                raise ApplicationError(
                    "ui_context is not JSON serializable",
                    code="ui_context_too_large",
                ) from exc
            if len(serialized.encode("utf-8")) > 16 * 1024:
                raise ApplicationError(
                    "ui_context exceeds the 16KB limit",
                    code="ui_context_too_large",
                )
            metadata["ui_context"] = ui_context
        run = await self._repository.create_agent_run(
            case_id=case_id,
            turn_id=turn.id,
            objective=content,
            metadata=metadata,
        )
        await self._repository.add_run_event(
            run.id,
            {
                "event_type": "agent_queued",
                "agent": "coordinator",
                "status": "pending",
            },
        )
        return run

    async def steer(self, run_id: str, content: str) -> Any:
        """Enqueue a steering instruction for a running coordinator run.

        The worker folds it into the agent loop at the next model step
        (``steering_step`` node); it never applies to expert runs (their
        output is a fixed structured artifact) or to terminal runs.
        """
        run = await self._repository.get_agent_run(run_id)
        if run.status in {"completed", "failed", "cancelled"}:
            raise ApplicationError(
                f"Run '{run_id}' is in terminal state '{run.status}'",
                code="run_not_steerable",
            )
        if run.parent_run_id:
            raise ApplicationError(
                "Steering is only supported on coordinator runs",
                code="steering_not_supported",
            )
        record = await self._repository.add_run_steering(run_id, content)
        await self._repository.add_run_event(
            run_id,
            {
                "event_type": "steering_received",
                "agent": run.agent,
                "status": "pending",
                "steering_id": record.id,
            },
        )
        return record

    async def cancel(self, run_id: str) -> Any:
        await self._worker.cancel(run_id)
        # 级联取消：协调器 run 发起的后台 CollectionRun 由独立 worker
        # 驱动，取消 agent run 时必须一并取消，否则采集会继续空转。
        if self._collection_run_service is not None:
            try:
                await self._collection_run_service.cancel_by_trigger_run(run_id)
            except Exception:  # noqa: BLE001
                logger.exception("cascade cancel of collection runs failed for %s", run_id)
        return await self._repository.update_agent_run(run_id, status="cancelled")

    async def approve(
        self,
        run_id: str,
        *,
        approval_id: str,
        decision: str,
        note: str | None = None,
        edited_action: dict[str, object] | None = None,
        actor: str = "operator",
    ) -> Any:
        """M21 统一审批决策。

        decision: approve | edit_and_approve | reject | cancel。
        幂等：已作出的相同决策直接返回；不同决策报错（并发保护）。
        编辑批准会重新校验（工具名不可改、参数结构化），并生成一次性
        执行授权（绑定 tool + 参数哈希 + run + 期限）。
        """
        approval = await self._repository.get_approval(approval_id)
        if approval.run_id != run_id:
            raise ApplicationError(
                f"Approval '{approval_id}' does not belong to run '{run_id}'",
                code="approval_run_mismatch",
            )
        if (
            approval.status == APPROVAL_PENDING
            and approval.expires_at is not None
            and approval.expires_at < datetime.now(UTC)
        ):
            await self._repository.update_approval_full(
                approval_id, status="expired"
            )
            raise ApplicationError(
                f"Approval '{approval_id}' expired",
                code="approval_expired",
            )
        mapping = {
            "approve": APPROVAL_APPROVED,
            "edit_and_approve": APPROVAL_APPROVED_WITH_EDITS,
            "reject": APPROVAL_REJECTED,
            "cancel": APPROVAL_CANCELLED,
        }
        target_status = mapping.get(decision)
        if target_status is None:
            raise ApplicationError(
                f"Unknown approval decision: {decision!r}",
                code="approval_decision_unknown",
            )
        if approval.status != APPROVAL_PENDING:
            if approval.status == target_status:
                return await self.resume(run_id)
            raise ApplicationError(
                f"Approval already decided as '{approval.status}'",
                code="approval_already_decided",
            )
        try:
            validate_approval_transition(APPROVAL_PENDING, target_status)
        except ValueError as exc:
            raise ApplicationError(
                str(exc), code="approval_transition_invalid"
            ) from exc

        edited: dict[str, object] | None = None
        effective_arguments: dict[str, object] = {}
        if decision == "edit_and_approve":
            if not edited_action:
                raise ApplicationError(
                    "edit_and_approve 需要 edited_action",
                    code="approval_edit_required",
                )
            edited = dict(edited_action)
            tool = str(edited.get("tool") or "")
            if tool and tool != approval.action:
                raise ApplicationError(
                    "编辑批准不能更改工具",
                    code="approval_edit_tool_changed",
                )
            arguments = edited.get("arguments")
            if arguments is None:
                raise ApplicationError(
                    "edited_action.arguments 必填",
                    code="approval_edit_arguments_required",
                )
            if not isinstance(arguments, dict):
                raise ApplicationError(
                    "edited_action.arguments 必须是对象",
                    code="approval_edit_arguments_invalid",
                )
            effective_arguments = dict(arguments)
        elif approval.request_payload and approval.request_payload.get(
            "crawl_scope"
        ):
            effective_arguments = dict(approval.request_payload["crawl_scope"])

        if target_status in {APPROVAL_APPROVED, APPROVAL_APPROVED_WITH_EDITS}:
            payload = approval.request_payload or {}
            patch: dict[str, object] = {}
            if approval.action == "collect_social_posts" and effective_arguments:
                patch["approved_crawl_scope"] = crawl_scope(effective_arguments)
            elif payload.get("crawl_scope"):
                patch["approved_crawl_scope"] = payload["crawl_scope"]
            if (
                approval.action == "budget_exceeded"
                or payload.get("approval_kind") == "budget_exceeded"
            ):
                patch["budget_approved"] = True
                current_max = payload.get("max_cost")
                try:
                    base = float(current_max) if current_max is not None else 5.0
                except (TypeError, ValueError):
                    base = 5.0
                patch["max_cost_override"] = base + 5.0
            if patch:
                await self._repository.patch_run_metadata(run_id, patch)
            # 一次性执行授权：绑定 tool + 参数哈希 + run + 期限 +
            # action_family/resource_id（M21/M22 防重放：一个审批至多一条授权）。
            # crawl 用规范化 scope 做严格参数绑定；其余工具参数由 LLM 重放，
            # 无稳定规范化表示，授权绑定 approval+action_family+资源即可。
            token = secrets.token_urlsafe(32)
            if approval.action == "collect_social_posts":
                argument_hash = self._argument_hash(effective_arguments)
            else:
                argument_hash = ""
            expires_at = datetime.now(UTC) + timedelta(hours=1)
            await self._repository.create_execution_authorization(
                approval_id=approval_id,
                run_id=run_id,
                tool_name=approval.action,
                argument_hash=argument_hash,
                token_hash=self._hash_token(token),
                action_family=f"tool:{approval.action}",
                resource_id=run_id,
                expires_at=expires_at,
            )

        await self._repository.update_approval_full(
            approval_id,
            status=target_status,
            decision=decision,
            decision_payload={
                "decision": decision,
                "note": note or "",
                "actor": actor,
            },
            edited_action=edited,
            actor=actor,
            decision_version="1.0",
        )
        return await self.resume(run_id)

    @staticmethod
    def _argument_hash(arguments: dict[str, object]) -> str:
        normalized = json.dumps(
            arguments, sort_keys=True, ensure_ascii=False, default=str
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


    async def resume(self, run_id: str) -> Any:
        run = await self._repository.get_agent_run(run_id)
        if run.status in {"completed", "failed", "cancelled"}:
            raise ApplicationError(
                f"Run '{run_id}' is in terminal state '{run.status}'",
                code="run_not_resumable",
            )
        await self._repository.update_agent_run(run_id, status="pending")
        return await self._repository.get_agent_run(run_id)
