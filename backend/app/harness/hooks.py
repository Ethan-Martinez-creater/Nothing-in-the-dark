from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any


class HookEvent(StrEnum):
    BEFORE_USER_MESSAGE = "before_user_message"
    AFTER_USER_MESSAGE = "after_user_message"
    BEFORE_MODEL_CALL = "before_model_call"
    AFTER_MODEL_CALL = "after_model_call"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    BEFORE_MEMORY_WRITE = "before_memory_write"
    AFTER_MEMORY_WRITE = "after_memory_write"
    BEFORE_ARTIFACT_WRITE = "before_artifact_write"
    AFTER_ARTIFACT_WRITE = "after_artifact_write"
    ON_AGENT_STOP = "on_agent_stop"
    ON_ERROR = "on_error"


HookHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


class HookBus:
    def __init__(self) -> None:
        self._handlers: dict[HookEvent, list[HookHandler]] = defaultdict(list)

    def register(self, event: HookEvent, handler: HookHandler) -> None:
        self._handlers[event].append(handler)

    async def emit(
        self,
        event: HookEvent,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        current = dict(payload)
        for handler in self._handlers[event]:
            update = await handler(current)
            if update:
                current.update(update)
        return current
