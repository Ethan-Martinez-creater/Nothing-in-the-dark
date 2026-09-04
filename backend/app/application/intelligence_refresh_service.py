"""V3 §61: Intelligence Refresh orchestration（确定性刷新链路）。

refresh_case 固定顺序：quality → entities → cross_case → signals（§61
execute）；enqueue 只创建 intelligence_refresh AnalysisJob（不递归）。
"""

from __future__ import annotations

from typing import Any

from app.infrastructure.database.analysis_job_repository import AnalysisJobRepository


class IntelligenceRefreshService:
    def __init__(
        self,
        *,
        analysis_job_repository: AnalysisJobRepository,
        quality_service: Any,
        workspace_entity_service: Any,
        cross_investigation_service: Any,
        advanced_signal_service: Any,
    ) -> None:
        self._jobs = analysis_job_repository
        self._quality = quality_service
        self._workspace = workspace_entity_service
        self._cross = cross_investigation_service
        self._signals = advanced_signal_service

    async def refresh_case(self, case_id: str) -> dict[str, Any]:
        """§61 execute：固定顺序 quality → entities → cross_case → signals。

        signals 为 Derived Signal coordination refresh（Advanced Signal
        Detector 的全局 detector 由独立刷新覆盖，此处只做 case scope）。
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
