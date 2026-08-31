"""Review workbench application service (09).

统一人工审核流程：队列生成、领取/释放、追加式决策、评论与活动日志。
决策应用采用“先校验状态机、再追加写、再更新对象状态”的顺序；
数据库事务失败不会出现决策已写但对象状态未更新。
"""

from __future__ import annotations

from typing import Any

from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.infrastructure.database.models import (
    CaseActivityLogRecord,
    ReviewCommentRecord,
    ReviewDecisionRecord,
    ReviewItemRecord,
)
from app.services import review as review_domain


class ReviewService:
    def __init__(self, repository: ApplicationRepository) -> None:
        self._repository = repository

    async def submit_item(
        self,
        *,
        case_id: str,
        object_type: str,
        object_id: str,
        summary: str = "",
        priority: int = 0,
        risk_level: str = "low",
        queue: str = "default",
        actor: str = "local_operator",
    ) -> ReviewItemRecord:
        if object_type not in review_domain.OBJECT_TYPES:
            raise ApplicationError(
                f"unknown review object type {object_type!r}",
                code="review_object_type_unknown",
            )
        # RC1: finding 必须走唯一原子提交入口（validate Finding + case scope +
        # 状态行为表 + 单事务）。此处只做 early branch，不得复制矩阵。
        if object_type == "finding":
            _finding, item = await self._repository.submit_finding_for_review(
                case_id=case_id,
                finding_id=object_id,
                priority=priority,
                risk_level=risk_level,
                queue=queue,
                actor=actor,
            )
            return item
        existing = await self._repository.list_review_items(
            case_id, limit=1000
        )
        for item in existing:
            if item.object_type == object_type and item.object_id == object_id:
                return item
        item = await self._repository.create_review_item(
            ReviewItemRecord(
                case_id=case_id,
                object_type=object_type,
                object_id=object_id,
                summary=summary,
                priority=priority,
                risk_level=risk_level,
                queue=queue,
            )
        )
        await self._repository.add_activity_log(
            CaseActivityLogRecord(
                case_id=case_id,
                activity_type="review_item_submitted",
                summary=f"提交审核项：{object_type}:{object_id}",
                actor=actor,
                metadata_json={
                    "object_type": object_type,
                    "object_id": object_id,
                },
            )
        )
        return item

    async def claim(
        self, item_id: str, actor: str, *, case_id: str | None = None
    ) -> ReviewItemRecord:
        if case_id is not None:
            await self._require_case_item(case_id, item_id)
        item = await self._repository.claim_review_item(item_id, actor)
        if item is None:
            current = await self._repository.get_review_item(item_id)
            if current.status != "unreviewed":
                raise ApplicationError(
                    f"review item {item_id} 已被他人领取（status={current.status}）",
                    code="review_item_already_claimed",
                )
            raise ApplicationError(
                f"review item {item_id} 不存在",
                code="review_item_not_found",
            )
        await self._log(
            item.case_id,
            "review_item_claimed",
            f"审核项 {item_id} 被 {actor} 领取",
            actor=actor,
        )
        return item

    async def release(
        self, item_id: str, actor: str, *, case_id: str | None = None
    ) -> ReviewItemRecord:
        if case_id is not None:
            await self._require_case_item(case_id, item_id)
        item = await self._repository.release_review_item(item_id, actor)
        if item is None:
            raise ApplicationError(
                f"review item {item_id} 无法释放（可能未领取或不存在）",
                code="review_item_release_failed",
            )
        await self._log(
            item.case_id,
            "review_item_released",
            f"审核项 {item_id} 已释放",
            actor=actor,
        )
        return item

    async def decide(
        self,
        *,
        item_id: str,
        decision: str,
        reason: str,
        actor: str = "local_operator",
        structured_patch: dict[str, Any] | None = None,
        case_id: str | None = None,
        expected_version: int | None = None,
    ) -> ReviewItemRecord:
        item = await self._repository.get_review_item(item_id)
        if case_id is not None and item.case_id != case_id:
            raise ApplicationError("review item not found", code="review_item_not_found")
        # RH2/18: 未显式传版本的旧调用者也把当前快照版本作为确定的 expected
        # version 交给 repository，由数据库 CAS 决定唯一 winner；显式传版本
        # 的客户端（第一方 Workbench）能检测跨审核轮次的 stale/ABA。
        effective_expected_version = (
            expected_version if expected_version is not None else item.current_version
        )
        if expected_version is not None and expected_version != item.current_version:
            raise ApplicationError(
                "review object version changed; reload before deciding",
                code="review_version_conflict",
            )
        domain_decision = review_domain.ReviewDecision(
            decision=decision,
            reason=reason,
            actor=actor,
            structured_patch=structured_patch,
        )
        # 状态机 + 对象约束校验（不合法直接抛错，不落任何记录）。
        domain_decision.validate(
            object_type=item.object_type, current_status=item.status
        )
        target_status = review_domain.apply_decision(item.status, decision)
        # 追加式决策：先写决策记录，再更新对象状态。
        record = ReviewDecisionRecord(
            item_id=item_id,
            object_version=effective_expected_version,
            decision=decision,
            structured_patch=structured_patch or {},
            reason=reason,
            actor=actor,
        )
        result = await self._repository.decide_review_item(
            item_id=item_id,
            expected_status=item.status,
            expected_version=effective_expected_version,
            target_status=target_status,
            decision=record,
        )
        if result is None:
            raise ApplicationError(
                "review item changed while the decision was being submitted",
                code="review_version_conflict",
            )
        updated, record = result
        # 撤销/覆盖时旧决策保留（追加写，supersede 语义由前端展示链）。
        await self._log(
            item.case_id,
            f"review_{decision}",
            f"审核项 {item_id} 决策 {decision}：{reason[:100]}",
            actor=actor,
            metadata_json={
                "decision_id": record.id,
                "target_status": target_status,
            },
        )
        return updated

    async def reopen(
        self, item_id: str, actor: str = "local_operator", *, case_id: str | None = None
    ) -> ReviewItemRecord:
        # PC2B: 原子重开（ReviewItem + Finding 同一事务）。状态机校验在
        # repository 原子方法内保留唯一权威实现；case 校验也移入其中。
        updated = await self._repository.reopen_review_item_atomic(
            item_id=item_id, case_id=case_id
        )
        await self._log(
            updated.case_id, "review_reopened", f"审核项 {item_id} 重新打开", actor=actor
        )
        return updated

    async def add_comment(
        self,
        *,
        item_id: str,
        text: str,
        actor: str = "local_operator",
        reference: str = "",
        thread_id: str = "",
        case_id: str | None = None,
    ) -> ReviewCommentRecord:
        item = await self._repository.get_review_item(item_id)
        if case_id is not None and item.case_id != case_id:
            raise ApplicationError("review item not found", code="review_item_not_found")
        comment = await self._repository.add_review_comment(
            ReviewCommentRecord(
                item_id=item_id,
                thread_id=thread_id,
                reference=reference,
                text=text,
                actor=actor,
            )
        )
        await self._log(item.case_id, "review_commented", f"审核项 {item_id} 新增评论", actor=actor)
        return comment

    async def list_queue(
        self,
        *,
        case_id: str,
        status: str | None = None,
        object_type: str | None = None,
    ) -> list[dict[str, object]]:
        records = await self._repository.list_review_items(
            case_id, status=status, object_type=object_type, limit=200
        )
        result: list[dict[str, object]] = []
        for record in records:
            decisions = await self._repository.list_review_decisions(record.id, limit=5)
            comments = await self._repository.list_review_comments(record.id, limit=5)
            result.append(
                {
                    "id": record.id,
                    "case_id": record.case_id,
                    "object_type": record.object_type,
                    "object_id": record.object_id,
                    "priority": record.priority,
                    "status": record.status,
                    "risk_level": record.risk_level,
                    "queue": record.queue,
                    "summary": record.summary,
                    "current_version": record.current_version,
                    "decisions": [
                        {
                            "id": d.id,
                            "decision": d.decision,
                            "reason": d.reason,
                            "actor": d.actor,
                            "created_at": d.created_at.isoformat() if d.created_at else None,
                        }
                        for d in decisions
                    ],
                    "comments": [
                        {
                            "id": c.id,
                            "text": c.text,
                            "actor": c.actor,
                            "reference": c.reference,
                            "created_at": c.created_at.isoformat() if c.created_at else None,
                        }
                        for c in comments
                    ],
                }
            )
        return result

    async def _require_case_item(
        self, case_id: str, item_id: str
    ) -> ReviewItemRecord:
        item = await self._repository.get_review_item(item_id)
        if item.case_id != case_id:
            raise ApplicationError("review item not found", code="review_item_not_found")
        return item

    async def _log(
        self,
        case_id: str,
        activity_type: str,
        summary: str,
        *,
        actor: str,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        try:
            await self._repository.add_activity_log(
                CaseActivityLogRecord(
                    case_id=case_id,
                    activity_type=activity_type,
                    summary=summary,
                    actor=actor,
                    metadata_json=metadata_json or {},
                )
            )
        except Exception:
            pass  # 日志失败不阻断审核主流程
