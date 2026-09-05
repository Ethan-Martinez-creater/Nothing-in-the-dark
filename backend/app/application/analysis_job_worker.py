"""Recoverable worker for long-running alignment and integrity jobs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.v3 import V3_INTELLIGENCE_VERSION
from app.infrastructure.database.analysis_job_repository import AnalysisJobRepository

logger = logging.getLogger(__name__)


class AnalysisJobWorker:
    def __init__(
        self,
        repository: AnalysisJobRepository,
        *,
        alignment_service: Any | None = None,
        integrity_service: Any | None = None,
        intelligence_service: Any | None = None,
        advanced_signal_service: Any | None = None,
        worker_id: str = "local-analysis-worker",
        poll_interval_seconds: float = 2.0,
        lease_seconds: int = 600,
        enabled: bool = True,
    ) -> None:
        self._repository = repository
        self._alignment = alignment_service
        self._integrity = integrity_service
        self._intelligence = intelligence_service
        self._advanced_signals = advanced_signal_service
        self._worker_id = worker_id
        self._poll_interval = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._enabled = enabled
        self._stopping = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self._enabled:
            logger.info("AnalysisJobWorker disabled")
            return
        self._task = asyncio.create_task(
            self._loop(), name=f"analysis-worker:{self._worker_id}"
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("analysis job tick failed")
            await asyncio.sleep(self._poll_interval)

    async def tick(self) -> str | None:
        job = await self._repository.claim_job(self._worker_id, self._lease_seconds)
        if job is None:
            return None
        job_type = job.job_type
        run_task = asyncio.create_task(self._run(job_type, job.case_id))
        interval = max(0.1, self._lease_seconds / 3.0)
        try:
            while not run_task.done():
                done, _ = await asyncio.wait({run_task}, timeout=interval)
                if done:
                    break
                current = await self._repository.get_job(job.id)
                if current.cancel_requested:
                    run_task.cancel()
                    await asyncio.gather(run_task, return_exceptions=True)
                    await self._repository.mark_cancelled(job.id, self._worker_id)
                    return job.id
                if not await self._repository.refresh_lease(
                    job.id, self._worker_id, self._lease_seconds
                ):
                    run_task.cancel()
                    await asyncio.gather(run_task, return_exceptions=True)
                    logger.warning("analysis job %s lost its lease", job.id)
                    return job.id
            result = await run_task
            if not await self._repository.complete_job(
                job.id, self._worker_id, result or {}
            ):
                logger.warning("analysis job %s completion rejected after lease loss", job.id)
            else:
                # §62.1：alignment/integrity 成功后 follow-up enqueue
                # intelligence_refresh（绝不 enqueue 自己，绝不递归）。
                await self._maybe_enqueue_intelligence_refresh(job.id, job.case_id, job_type)
        except asyncio.CancelledError:
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
            await self._repository.fail_job(job.id, self._worker_id, "worker_stopped")
            raise
        except Exception as exc:
            logger.warning("analysis job %s failed: %s", job.id, exc)
            await self._repository.fail_job(
                job.id, self._worker_id, getattr(exc, "code", "analysis_error")
            )
        return job.id

    async def _maybe_enqueue_intelligence_refresh(
        self, job_id: str, case_id: str, job_type: str
    ) -> None:
        if self._intelligence is None:
            return
        if job_type == "intelligence_refresh":
            # Rework R1：intelligence_refresh 成功后 best-effort enqueue
            # advanced_signal_refresh（绝不 enqueue 自己，绝不递归）。
            try:
                await self._intelligence.enqueue_advanced_signal_refresh(
                    job_id=job_id, case_id=case_id
                )
            except Exception:
                logger.warning(
                    "advanced_signal_refresh follow-up enqueue failed for job %s",
                    job_id,
                    exc_info=True,
                )
            return
        if job_type not in ("alignment", "integrity"):
            return
        try:
            await self._intelligence.enqueue(
                case_id,
                source_key=(
                    f"v3:intel:{job_type}:{job_id}:{V3_INTELLIGENCE_VERSION}"
                ),
            )
        except Exception:
            logger.warning(
                "intelligence_refresh follow-up enqueue failed for job %s",
                job_id,
                exc_info=True,
            )

    async def _run(self, job_type: str, case_id: str) -> dict[str, Any]:
        if job_type == "alignment" and self._alignment is not None:
            return await self._alignment.analyze_case(case_id)
        if job_type == "integrity" and self._integrity is not None:
            return await self._integrity.analyze_case(case_id)
        if job_type == "intelligence_refresh" and self._intelligence is not None:
            return await self._intelligence.refresh_case(case_id)
        if job_type == "advanced_signal_refresh" and self._advanced_signals is not None:
            return await self._advanced_signals.refresh_global()
        raise ValueError(f"unknown job_type {job_type}")
