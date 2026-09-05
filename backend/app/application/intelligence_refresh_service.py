"""V3 §61: Intelligence Refresh orchestration（确定性刷新链路）。

refresh_case 固定顺序：quality → entities → cross_case → signals（§61
execute）；enqueue 只创建 intelligence_refresh AnalysisJob（不递归）。

V3 Approval Rework R1：intelligence_refresh 成功后由 worker best-effort
enqueue advanced_signal_refresh（三个 Workspace-global detector）。
"""

from __future__ import annotations

from typing import Any

from app.core.v3 import ADVANCED_SIGNAL_VERSION
from app.infrastructure.database.analysis_job_repository import AnalysisJobRepository


class IntelligenceRefreshService:
    def __init__(
        self,
        *,
        analysis_job_repository: AnalysisJobRepository,
        application_repository: Any = None,
        quality_service: Any,
        workspace_entity_service: Any,
        cross_investigation_service: Any,
        advanced_signal_service: Any,
    ) -> None:
        self._jobs = analysis_job_repository
        self._application = application_repository
        self._quality = quality_service
        self._workspace = workspace_entity_service
        self._cross = cross_investigation_service
        self._signals = advanced_signal_service

    async def refresh_case(self, case_id: str) -> dict[str, Any]:
        """§61 execute：固定顺序 quality → entities → cross_case → signals。

        signals 为 Derived Signal coordination refresh（Advanced Signal
        Detector 的 global detector 由 advanced_signal_refresh job 覆盖，
        Rework R1）。
        """
        quality = await self._quality.evaluate(case_id)
        entities = await self._workspace.refresh_case(case_id)
        cross_case = await self._cross.refresh_case(case_id)
        signals = await self._signals.refresh_case(case_id)
        return {
            "quality": quality,
            "entities": entities,
            "cross_case": cross_case,
            "signals": signals,
        }

    async def enqueue(self, case_id: str, *, source_key: str) -> Any:
        """§61 enqueue：创建 intelligence_refresh job（idempotency = source_key）。"""
        return await self._jobs.create_job(
            case_id=case_id,
            job_type="intelligence_refresh",
            idempotency_key=source_key,
        )

    async def enqueue_advanced_signal_refresh(
        self, *, job_id: str, case_id: str
    ) -> Any:
        """Rework R1：intelligence_refresh 成功后 enqueue advanced_signal_refresh。

        idempotency key 固定 v3:advanced:{intelligence_job_id}:{version}
        （不使用分钟 key；绝不递归 enqueue 自己）。
        """
        return await self._jobs.create_job(
            case_id=case_id,
            job_type="advanced_signal_refresh",
            idempotency_key=(
                f"v3:advanced:{job_id}:{ADVANCED_SIGNAL_VERSION}"
            ),
        )

    async def enqueue_after_scope_deletion(
        self, *, scope_key: str
    ) -> Any | None:
        """FC2：Case / Project 删除成功后 enqueue 一次 global advanced refresh。

        - Workspace 已无剩余 Case → 返回 None（不创建 job，避免非法 FK）；
        - 否则以 remaining Cases 中 created_at ASC + id ASC 的第一个 Case
          作为 AnalysisJob.case_id anchor（deterministic，不引用已删除
          Case）；
        - idempotency key 使用 scope_key（Case delete = deleted case_id，
          Project delete = project_id）区分删除事件；同 scope 只产生一个
          refresh（64 字符截断保留 scope_key 全量前缀）。
        """
        if self._application is None:
            return None
        cases = await self._application.list_cases_ordered_by_creation()
        if not cases:
            return None
        anchor = cases[0]
        return await self._jobs.create_job(
            case_id=anchor.id,
            job_type="advanced_signal_refresh",
            idempotency_key=(
                f"v3:advanced:case-delete:{scope_key}:{ADVANCED_SIGNAL_VERSION}"
            ),
        )
