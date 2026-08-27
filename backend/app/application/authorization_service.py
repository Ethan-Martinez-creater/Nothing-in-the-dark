"""M21/M22 one-time execution authorization consumption.

一次性执行授权消费：审批对象 + 操作参数哈希 + run/资源 ID + 有效期四要素
绑定，原子消费后失效；同一个审批不能重复用于多个 Kill Switch、死信重试
或工具调用。消费与业务变更在同一事务中执行（见 resilience 仓储）。

- :meth:`AuthorizationService.issue` 在审批决策（approve）或运维操作前签发
  授权记录（approval_id 唯一约束保证一个审批至多一次）。
- :meth:`AuthorizationService.consume` 原子消费；重复使用同一审批返回
  409 语义的错误码（authorization_already_consumed）。
- :meth:`consume_in_session` 供业务仓储在同一个 session 中与业务变更合并，
  防止"授权已消费但业务未落库"的窗口。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError

TOOL_FAMILY_PREFIX = "tool:"


def argument_hash(parameters: dict[str, Any] | None) -> str:
    """规范化参数哈希：排序键 + 确定性序列化，杜绝顺序/编码篡改。"""
    normalized = json.dumps(
        parameters or {}, sort_keys=True, ensure_ascii=False, default=str
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class AuthorizationService:
    """签发与原子消费一次性执行授权（防重放）。"""

    def __init__(self, repository: ApplicationRepository) -> None:
        self._repository = repository

    async def issue(
        self,
        approval_id: str,
        *,
        action_family: str,
        resource_id: str,
        parameters: dict[str, Any] | None = None,
        run_id: str | None = None,
        ttl_hours: float = 1.0,
    ) -> str:
        """为一次具体操作签发授权，返回稳定的授权记录 ID。

        校验：审批必须是 approved / approved_with_edits 且未过期；同一
        scope 的未消费记录可幂等复用；unique 约束与冲突重读保证并发安全。
        """
        approval = await self._repository.get_approval(approval_id)
        if approval.status not in {"approved", "approved_with_edits"}:
            raise ApplicationError(
                f"approval {approval_id} has not been approved",
                code="authorization_approval_not_approved",
            )
        expires_at = _aware(approval.expires_at)
        if expires_at is not None and expires_at < datetime.now(UTC):
            raise ApplicationError(
                f"approval {approval_id} has expired",
                code="authorization_approval_expired",
            )
        existing = await self._repository.get_execution_authorization_by_approval(
            approval_id
        )
        expected_hash = argument_hash(parameters)

        def reusable(record: Any) -> bool:
            now_utc = datetime.now(UTC)
            expires = _aware(record.expires_at)
            return (
                record.consumed_at is None
                and (expires is None or expires >= now_utc)
                and record.action_family == action_family
                and record.resource_id == resource_id
                and (
                    not record.argument_hash
                    or record.argument_hash == expected_hash
                )
            )

        if existing is not None:
            if reusable(existing):
                return existing.id
            raise ApplicationError(
                f"approval {approval_id} already issued an execution authorization",
                code="authorization_already_issued",
            )
        effective_run_id = run_id or approval.run_id
        token = secrets.token_urlsafe(32)
        try:
            created = await self._repository.create_execution_authorization(
                approval_id=approval_id,
                run_id=effective_run_id,
                tool_name=action_family,
                argument_hash=expected_hash,
                token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                action_family=action_family,
                resource_id=resource_id,
                expires_at=datetime.now(UTC).replace(microsecond=0)
                + timedelta(hours=ttl_hours),
            )
            return created.id
        except ApplicationError as exc:
            if exc.code != "authorization_already_issued":
                raise
            concurrent = await self._repository.get_execution_authorization_by_approval(
                approval_id
            )
            if concurrent is not None and reusable(concurrent):
                return concurrent.id
            raise

    async def consume(
        self,
        approval_id: str,
        *,
        action_family: str,
        resource_id: str,
        parameters: dict[str, Any] | None = None,
        now=None,
    ) -> None:
        """原子消费一次授权；失败抛 409 语义错误（防重放）。"""
        now = now or datetime.now(UTC)
        record = await self._repository.get_execution_authorization_by_approval(
            approval_id
        )
        if record is None:
            raise ApplicationError(
                f"no execution authorization for approval {approval_id}",
                code="authorization_not_issued",
            )
        if record.consumed_at is not None:
            raise ApplicationError(
                f"authorization for approval {approval_id} already consumed",
                code="authorization_already_consumed",
            )
        if _aware(record.expires_at) is not None and _aware(record.expires_at) < now:
            raise ApplicationError(
                f"authorization for approval {approval_id} has expired",
                code="authorization_expired",
            )
        if record.action_family != action_family or record.resource_id != resource_id:
            raise ApplicationError(
                "authorization scope mismatch (action/resource)",
                code="authorization_scope_mismatch",
            )
        # 空哈希表示该工具无稳定规范化参数（LLM 重放），仅绑定审批+操作族+资源；
        # 非空哈希（如 crawl 的规范化 scope）必须严格匹配，杜绝篡改。
        if record.argument_hash and record.argument_hash != argument_hash(parameters):
            raise ApplicationError(
                "authorization parameter mismatch (tampered arguments)",
                code="authorization_parameter_mismatch",
            )
        consumed = await self._repository.consume_authorization_by_approval(
            approval_id=approval_id,
            action_family=action_family,
            resource_id=resource_id,
            argument_hash=record.argument_hash,
            now=now,
        )
        if not consumed:
            raise ApplicationError(
                f"authorization for approval {approval_id} cannot be consumed",
                code="authorization_already_consumed",
            )

    async def consume_for_tool(
        self,
        approval_id: str,
        *,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        """Agent 工具执行前的授权消费（action_family=tool:{name}, 资源=run）。

        crawl 参数以规范化 scope（crawl_scope）绑定，与审批时一致；其余
        工具以原始参数记录（空哈希语义，见 consume）。
        """
        parameters: dict[str, Any] = dict(arguments)
        if tool_name == "collect_social_posts":
            from app.harness.approval_policy import crawl_scope

            parameters = crawl_scope(arguments)
        await self.consume(
            approval_id,
            action_family=f"{TOOL_FAMILY_PREFIX}{tool_name}",
            resource_id=run_id,
            parameters=parameters,
        )

    @staticmethod
    async def consume_in_session(
        session: Any,
        approval_id: str,
        *,
        action_family: str,
        resource_id: str,
        parameters: dict[str, Any] | None = None,
        now=None,
    ) -> int:
        """业务仓储事务内消费：与业务变更同一事务，防重放窗口为 0。"""
        return await ApplicationRepository.consume_authorization_in_session(
            session,
            approval_id=approval_id,
            action_family=action_family,
            resource_id=resource_id,
            argument_hash=argument_hash(parameters),
            now=now or datetime.now(UTC),
        )
