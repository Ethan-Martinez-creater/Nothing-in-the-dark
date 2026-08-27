"""M11: Typed Mailbox over the ``agent_messages`` table.

A thin, type-safe wrapper around :class:`ApplicationRepository`'s raw
send/list methods. The mailbox is how parent and child agent runs exchange
typed messages (``expert_completed`` and friends); this module turns those
rows into A2A :class:`Message` DTOs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.a2a.schemas import Message, MessageRole
from app.application.repositories import ApplicationRepository
from app.infrastructure.database.models import AgentMessageRecord

#: Child expert reports its finished artifacts back to the coordinator.
EXPERT_COMPLETED = "expert_completed"
#: Coordinator acknowledges receipt of an expert result.
COORDINATOR_ACK = "coordinator_ack"


class TypedMailbox:
    """Typed message exchange between agent runs (A2A ``Message`` DTOs)."""

    def __init__(self, repository: ApplicationRepository) -> None:
        self._repository = repository

    async def send(
        self,
        *,
        sender_run_id: str,
        receiver_run_id: str,
        message_type: str,
        payload: dict[str, Any] | None = None,
    ) -> Message:
        """Deliver one typed message; validates both runs exist."""
        record = await self._repository.add_agent_message(
            sender_run_id=sender_run_id,
            receiver_run_id=receiver_run_id,
            message_type=message_type,
            payload=payload or {},
        )
        return _to_message(record)

    async def send_expert_completed(
        self,
        *,
        sender_run_id: str,
        receiver_run_id: str,
        artifact_ids: list[str],
    ) -> Message:
        """Convenience helper for the standard expert completion report."""
        return await self.send(
            sender_run_id=sender_run_id,
            receiver_run_id=receiver_run_id,
            message_type=EXPERT_COMPLETED,
            payload={"artifact_ids": artifact_ids},
        )

    async def list(
        self,
        run_id: str,
        *,
        sender_run_id: str | None = None,
        receiver_run_id: str | None = None,
    ) -> list[Message]:
        """All messages touching a run, oldest first."""
        records = await self._repository.list_agent_messages(
            run_id,
            sender_run_id=sender_run_id,
            receiver_run_id=receiver_run_id,
        )
        return [_to_message(record) for record in records]


def _to_message(record: AgentMessageRecord) -> Message:
    return Message(
        messageId=record.id,
        role=MessageRole.AGENT,
        message_type=record.message_type,
        payload=record.payload or {},
        metadata={
            "sender_run_id": record.sender_run_id,
            "receiver_run_id": record.receiver_run_id,
        },
        createdAt=_as_utc(record.created_at),
    )


def _as_utc(value: datetime) -> datetime:
    """SQLite reads back naive datetimes; pin them to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
