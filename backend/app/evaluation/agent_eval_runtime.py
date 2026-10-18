"""Eval Tool Stack —— 与生产装配一致的只读服务与 per-task 数据库隔离。

修复计划 FC-IR-01 / FC-IR-05：

* ``build_eval_read_services`` 复用**生产** ``AgentDatabaseReadService`` /
  ``IntelligenceToolReadService`` 及其上游确定性服务（Quality / Workspace /
  Cross / Signal），让 DB01–DB09 与 5 个 Intelligence Tool 在评测中真正读取
  冻结 fixture，而不是返回 ``*_unavailable``。这里不是第二套 Tool System：
  ToolSpec 契约仍来自生产 ``build_tool_registry`` / ``register_*_tools``，
  本模块只负责把生产 service 的最小依赖闭包装配出来。
* ``TemporaryTaskDatabase`` 提供 per-task 隔离：每个 Golden Task 一个全新
  临时文件库（生产 ``Database`` 类，QueuePool 多连接，worker 并发写安全），
  任务结束后关闭并保留，杜绝 Workspace Entity / Cross Intelligence 等
  全局数据跨任务累积。内存 StaticPool 单连接方案被明确否决：worker 的并发 session 会
  共享同一连接、事务状态互相污染（实测复现 refresh / missing table 错误）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4


class ReadObservationRecorder:
    """旁路记录只读服务的完整返回（FC-IR-02 grounding 的权威依据）。

    生产持久化只写 500 字符的 ``output_summary``，完整 observation 不落库；
    本记录器包在**同一个**生产 service 外面——handler 拿到的就是记录值，
    不重新执行、不改生产代码。不是第二套 Tool System，只是调用边界的
    spy（与测试中的 recording mock 同性质，但 inner 是真实生产服务）。
    """

    def __init__(self) -> None:
        self._texts: list[str] = []
        self._tool_texts: list[str] = []
        self._observations: list[dict[str, Any]] = []

    def wrap(self, service: Any) -> Any:
        recorder = self

        class _RecordingProxy:
            def __getattr__(self, name: str) -> Any:
                attr = getattr(service, name)
                if name.startswith("_") or not callable(attr):
                    return attr

                async def _call(*args: Any, **kwargs: Any) -> Any:
                    result = await attr(*args, **kwargs)
                    try:
                        serialized = json.dumps(result, ensure_ascii=False, default=str)
                    except Exception:  # noqa: BLE001 - 不可序列化时退化为 str()
                        serialized = str(result)
                    recorder._texts.append(serialized)
                    recorder._observations.append(
                        {
                            "kind": "read_service_observation",
                            "service_method": name,
                            "arguments": _json_safe({"args": args, "kwargs": kwargs}),
                            "result": _json_safe(result),
                        }
                    )
                    return result

                return _call

        return _RecordingProxy()

    def texts(self) -> tuple[str, ...]:
        return tuple(self._texts)

    def record_tool_call(
        self,
        *,
        tool: str,
        tool_call_id: str | None,
        arguments: Any,
        status: str,
        result: Any = None,
        error_code: str | None = None,
        error_message: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "kind": "tool_observation",
            "tool": tool,
            "tool_call_id": tool_call_id,
            "arguments": _json_safe(arguments),
            "status": status,
        }
        if status == "succeeded":
            record["result"] = _json_safe(result)
            serialized = json.dumps(record["result"], ensure_ascii=False, default=str)
            self._texts.append(serialized)
            self._tool_texts.append(serialized)
        if error_code:
            record["error_code"] = error_code
        if error_message:
            record["error_message"] = error_message
        if duration_ms is not None:
            record["duration_ms"] = duration_ms
        self._observations.append(record)

    def observations(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._observations)

    def tool_texts(self) -> tuple[str, ...]:
        return tuple(self._tool_texts)


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001 - retain a printable trace if conversion fails
        return str(value)


class TemporaryTaskDatabase:
    """评测专用的单任务临时文件库（接口与生产 ``Database`` 的子集一致）。

    repository / service 只依赖 ``session_factory``；``create_schema`` /
    ``dispose`` 供 runner 在任务边界调用。create_schema 实测 ~2.3s
    （129 张表单事务），24 任务约 55s，可接受。
    """

    def __init__(self) -> None:
        from app.infrastructure.database import Database

        self._root = (
            Path(__file__).resolve().parents[2]
            / "artifacts"
            / "agent_eval"
            / "task_databases"
        )
        self._root.mkdir(parents=True, exist_ok=True)
        self._dir = self._root / f"agent-eval-task-{uuid4().hex}"
        self._dir.mkdir(parents=True, exist_ok=False)
        self._database = Database(
            f"sqlite+aiosqlite:///{self._dir / 'task.db'}"
        )

    @property
    def path(self) -> Path:
        return self._dir

    @property
    def engine(self) -> Any:
        return self._database.engine

    @property
    def session_factory(self) -> Any:
        return self._database.session_factory

    async def create_schema(self) -> None:
        await self._database.create_schema()

    async def dispose(self) -> None:
        await self._database.dispose()
        # Evaluation fixtures are synthetic and isolated; preserve the closed
        # directory for audit/debugging rather than recursively deleting it.


def build_eval_read_services(stack: Any) -> tuple[Any, Any]:
    """用 EvalDataStack 装配生产只读服务。

    返回 ``(agent_database_service, intelligence_tool_service)``，分别供
    ``register_database_tools`` / ``register_intelligence_tools`` 使用。
    依赖闭包与 ``bootstrap.py`` 的生产装配逐行对应（除 LLM：
    ``CollectionDefinitionService`` 的只读路径不调用模型，评测传 None）。
    """
    from app.application.agent_database_service import AgentDatabaseReadService
    from app.application.collection_service import CollectionDefinitionService
    from app.application.cross_investigation_service import (
        CrossInvestigationService,
    )
    from app.application.investigation_quality_service import (
        InvestigationQualityService,
    )
    from app.application.signal_service import SignalService
    from app.application.workspace_entity_service import WorkspaceEntityService
    from app.harness.intelligence_tools import IntelligenceToolReadService

    collection_definitions = CollectionDefinitionService(stack.database, None)
    quality = InvestigationQualityService(
        repository=stack.repository,
        social_repository=stack.social,
        collection_run_repository=stack.collection_run_repository,
        finding_repository=stack.finding_repository,
        quality_repository=stack.investigation_quality_repository,
        report_document_service=stack.report_service,
        collection_definition_service=collection_definitions,
        database=stack.database,
    )
    workspace = WorkspaceEntityService(
        workspace_repository=stack.workspace_repository,
        alignment_repository=stack.alignment_repository,
        application_repository=stack.repository,
        social_repository=stack.social,
        integrity_repository=stack.integrity_repository,
        database=stack.database,
    )
    cross = CrossInvestigationService(
        cross_repository=stack.cross_repository,
        workspace_repository=stack.workspace_repository,
        workspace_service=workspace,
        social_repository=stack.social,
        media_repository=stack.media_repository,
        application_repository=stack.repository,
        database=stack.database,
    )
    signals = SignalService(
        stack.database,
        stack.monitor_repository,
        derived_repository=stack.signal_repository,
    )
    agent_database = AgentDatabaseReadService(
        repository=stack.repository,
        social_repository=stack.social,
        collection_run_repository=stack.collection_run_repository,
        finding_repository=stack.finding_repository,
        report_repository=stack.report_repository,
    )
    intelligence = IntelligenceToolReadService(
        quality_service=quality,
        cross_service=cross,
        workspace_service=workspace,
        signal_service=signals,
        workspace_repository=stack.workspace_repository,
        cross_repository=stack.cross_repository,
    )
    return agent_database, intelligence
