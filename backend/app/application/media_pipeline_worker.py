"""MediaPipelineWorker: 多模态流水线的独立异步 Worker (04).

阶段状态机 + 租约 + 重试幂等：

- 下载阶段完成后才创建后续分析阶段（probe/ocr/asr/keyframe/c2pa）。
- 每个阶段独立提交结果；失败可重试，超过上限标记 failed。
- 同一资产同一 pipeline_version 只保留一个成功结果（阶段唯一约束）。
- OCR 成功后 ASR 崩溃时，恢复不会重新下载（download job 已 succeeded）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.infrastructure.database.media_pipeline_repository import MediaPipelineRepository
from app.infrastructure.media_fetch import MediaFetchService
from app.services.media_pipeline import (
    DEFAULT_MAX_ATTEMPTS,
    ByteC2PAVerifier,
    NullASRProvider,
    NullFrameExtractor,
    NullOCRProvider,
    probe_image_dimensions,
)

logger = logging.getLogger(__name__)

_IMAGE_STAGES = ("probe", "ocr", "c2pa")
_VIDEO_STAGES = ("probe", "asr", "keyframe", "c2pa")
_AUDIO_STAGES = ("probe", "asr", "c2pa")


class MediaPipelineWorker:
    def __init__(
        self,
        repository: MediaPipelineRepository,
        fetch_service: MediaFetchService,
        *,
        worker_id: str = "local-media-worker",
        poll_interval_seconds: float = 2.0,
        lease_seconds: int = 600,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        enabled: bool = True,
        probe_provider: Any | None = None,
        ocr_provider: Any | None = None,
        asr_provider: Any | None = None,
        frame_extractor: Any | None = None,
        c2pa_verifier: Any | None = None,
        app_repository: Any | None = None,
        knowledge: Any | None = None,
    ) -> None:
        self._repository = repository
        self._fetch = fetch_service
        self._worker_id = worker_id
        self._poll_interval = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._enabled = enabled
        self._probe = probe_provider
        self._ocr = ocr_provider or NullOCRProvider()
        self._asr = asr_provider or NullASRProvider()
        self._frames = frame_extractor or NullFrameExtractor()
        self._c2pa = c2pa_verifier or ByteC2PAVerifier()
        self._app_repository = app_repository
        self._knowledge = knowledge
        self._stopping = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self._enabled:
            logger.info("MediaPipelineWorker disabled by configuration")
            return
        self._task = asyncio.create_task(self._loop(), name=f"media-worker:{self._worker_id}")

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
            except Exception:  # pragma: no cover - defensive loop
                logger.exception("media pipeline tick failed")
            await asyncio.sleep(self._poll_interval)

    async def tick(self) -> str | None:
        await self._enqueue_jobs()
        expired_assets = await self._repository.terminalize_expired_jobs(self._max_attempts)
        for asset_id in expired_assets:
            await self._finalize_asset_if_done(asset_id)
        job = await self._repository.claim_job(
            self._worker_id, self._lease_seconds, self._max_attempts
        )
        if job is None:
            return None
        run_task = asyncio.create_task(self._execute(job.id))
        interval = max(0.1, self._lease_seconds / 3.0)
        try:
            while not run_task.done():
                done, _ = await asyncio.wait({run_task}, timeout=interval)
                if done:
                    break
                if not await self._repository.refresh_job_lease(
                    job.id, self._worker_id, self._lease_seconds
                ):
                    run_task.cancel()
                    await asyncio.gather(run_task, return_exceptions=True)
                    logger.warning("media job %s lost its lease", job.id)
                    return job.id
            await run_task
        except asyncio.CancelledError:
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
            raise
        except Exception as exc:  # noqa: BLE001 - job failure recorded
            await self._fail_job(job.id, exc)
        return job.id

    async def _enqueue_jobs(self) -> None:
        assets = await self._repository.list_pending_assets(limit=50)
        for asset in assets:
            if asset.download_status in ("not_downloaded", "failed"):
                await self._repository.create_job(asset.id, "download")
            elif asset.download_status == "downloaded":
                for stage in self._stages_for(asset.media_type):
                    await self._repository.create_job(asset.id, stage)

    @staticmethod
    def _stages_for(media_type: str) -> tuple[str, ...]:
        if media_type == "image":
            return _IMAGE_STAGES
        if media_type == "video":
            return _VIDEO_STAGES
        if media_type == "audio":
            return _AUDIO_STAGES
        return _IMAGE_STAGES

    async def _execute(self, job_id: str) -> None:
        job = await self._repository.get_job(job_id)
        asset = await self._repository.get_asset(job.asset_id)

        if job.stage == "download":
            await self._execute_download(asset, job_id)
            return
        if job.stage == "probe":
            await self._execute_probe(asset, job_id)
            return
        if job.stage == "ocr":
            await self._execute_ocr(asset, job_id)
            return
        if job.stage == "asr":
            await self._execute_asr(asset, job_id)
            return
        if job.stage == "keyframe":
            await self._execute_keyframe(asset, job_id)
            return
        if job.stage == "c2pa":
            await self._execute_c2pa(asset, job_id)
            return
        # 未知阶段：标记跳过。
        await self._repository.update_job(job_id, status="skipped")

    # ---- 阶段实现 --------------------------------------------------------

    async def _execute_download(self, asset: Any, job_id: str) -> None:
        result = await self._fetch.fetch(asset.url, asset.media_type)
        if not result.ok:
            await self._repository.update_asset(
                asset.id,
                download_status="failed",
                error_code=result.error_code,
            )
            await self._fail_job(job_id, RuntimeError(result.error_message or result.error_code))
            return
        await self._repository.update_asset(
            asset.id,
            storage_uri=result.storage_uri,
            byte_size=result.byte_size,
            mime_type=result.mime_type or "",
            actual_sha256=result.sha256,
            hash_kind="sha256",
            download_status="downloaded",
            analysis_status="pending",
            error_code=None,
        )
        await self._repository.update_job(
            job_id,
            status="succeeded",
            resource_stats={"byte_size": result.byte_size, "sha256": result.sha256},
            lease_owner=None,
            lease_expires_at=None,
        )
        # 下载成功后创建后续分析阶段。
        for stage in self._stages_for(asset.media_type):
            await self._repository.create_job(asset.id, stage)

    async def _execute_probe(self, asset: Any, job_id: str) -> None:
        path = asset.storage_uri
        width = height = None
        duration_ms = None
        probe_stats: dict[str, Any] = {}
        if path:
            if self._probe is not None and getattr(self._probe, "available", False):
                try:
                    probe_stats = await self._probe.probe(path)
                    width = probe_stats.get("width")
                    height = probe_stats.get("height")
                    duration_ms = probe_stats.get("duration_ms")
                except Exception:  # noqa: BLE001 - probe failure is non-fatal
                    probe_stats = {"error": "probe_failed"}
            else:
                try:
                    data = self._read_head(path, 4096)
                    dims = probe_image_dimensions(data, asset.mime_type)
                    if dims:
                        width, height = dims
                except OSError:
                    pass
        await self._repository.update_asset(
            asset.id, width=width, height=height, duration_ms=duration_ms
        )
        await self._repository.update_job(
            job_id,
            status="succeeded",
            resource_stats={"width": width, "height": height, "probe": probe_stats},
            lease_owner=None,
            lease_expires_at=None,
        )
        await self._finalize_asset_if_done(asset.id)

    @staticmethod
    def _provider_available(provider: Any) -> bool:
        return bool(getattr(provider, "available", False))

    async def _ingest_derived_text(
        self,
        asset: Any,
        kind: str,
        text: str,
        language: str = "",
        provenance: dict[str, Any] | None = None,
    ) -> None:
        """MEDIA-P0-02：派生文本写入 Evidence 与 Knowledge（源 kind 区分）。"""
        text = (text or "").strip()
        if not text:
            return
        if self._app_repository is not None:
            try:
                await self._app_repository.create_evidence(
                    case_id=asset.case_id,
                    claim_id=None,
                    source_type=f"media_{kind}",
                    source_id=f"{asset.id}:{kind}",
                    stance="context",
                    excerpt=text[:2000],
                    relevance=0.5,
                    metadata={
                        "asset_id": asset.id,
                        "platform": asset.platform,
                        "media_type": asset.media_type,
                        "language": language,
                        "kind": kind,
                        **(provenance or {}),
                    },
                )
            except Exception as exc:
                raise RuntimeError("media_evidence_ingest_failed") from exc
        if self._knowledge is not None:
            checksum = hashlib.sha256(text.encode()).hexdigest()
            try:
                await self._knowledge.add_document(
                    case_id=asset.case_id,
                    filename=f"{asset.id}-{kind}",
                    media_type=f"text/{kind}",
                    checksum=checksum,
                    chunks=[text],
                    metadata={
                        "asset_id": asset.id,
                        "kind": kind,
                        "source": "media_pipeline",
                        **(provenance or {}),
                    },
                )
            except Exception as exc:
                raise RuntimeError("media_knowledge_ingest_failed") from exc

    async def _execute_ocr(self, asset: Any, job_id: str) -> None:
        if not self._provider_available(self._ocr):
            await self._repository.update_job(
                job_id,
                status="skipped",
                resource_stats={"provider_unavailable": "ocr"},
                lease_owner=None,
                lease_expires_at=None,
            )
            await self._finalize_asset_if_done(asset.id)
            return
        path = asset.storage_uri or ""
        result = await self._ocr.extract(path, asset.media_type)
        if result.text:
            await self._repository.create_transcript(
                asset_id=asset.id,
                kind="ocr",
                language=result.language,
                segments=result.regions,
                full_text=result.text,
                provider=self._ocr.__class__.__name__,
            )
            await self._ingest_derived_text(asset, "ocr", result.text, result.language)
        await self._repository.update_job(
            job_id,
            status="succeeded",
            resource_stats={"text_chars": len(result.text)},
            lease_owner=None,
            lease_expires_at=None,
        )
        await self._finalize_asset_if_done(asset.id)

    async def _execute_asr(self, asset: Any, job_id: str) -> None:
        if not self._provider_available(self._asr):
            await self._repository.update_job(
                job_id,
                status="skipped",
                resource_stats={"provider_unavailable": "asr"},
                lease_owner=None,
                lease_expires_at=None,
            )
            await self._finalize_asset_if_done(asset.id)
            return
        path = asset.storage_uri or ""
        result = await self._asr.transcribe(path, asset.media_type)
        if result.full_text:
            await self._repository.create_transcript(
                asset_id=asset.id,
                kind="asr",
                language=result.language,
                segments=result.segments,
                full_text=result.full_text,
                confidence=result.confidence,
                provider=self._asr.__class__.__name__,
            )
            await self._ingest_derived_text(
                asset,
                "asr",
                result.full_text,
                result.language,
                provenance={"segments": result.segments[:100]},
            )
        await self._repository.update_job(
            job_id,
            status="succeeded",
            resource_stats={"text_chars": len(result.full_text), "confidence": result.confidence},
            lease_owner=None,
            lease_expires_at=None,
        )
        await self._finalize_asset_if_done(asset.id)

    async def _execute_keyframe(self, asset: Any, job_id: str) -> None:
        if not self._provider_available(self._frames):
            await self._repository.update_job(
                job_id,
                status="skipped",
                resource_stats={"provider_unavailable": "keyframe"},
                lease_owner=None,
                lease_expires_at=None,
            )
            await self._finalize_asset_if_done(asset.id)
            return
        path = asset.storage_uri or ""
        frames = await self._frames.extract(path, asset.media_type)
        frame_ocr_parts: list[str] = []
        frame_ocr_regions: list[dict[str, Any]] = []
        for frame in frames:
            await self._repository.create_derivative(
                asset_id=asset.id,
                kind="keyframe",
                storage_uri=frame.storage_uri,
                sha256=frame.sha256,
                time_start_ms=frame.time_ms,
                metadata=frame.metadata,
                producer=self._frames.__class__.__name__,
            )
            if frame.storage_uri and self._provider_available(self._ocr):
                ocr = await self._ocr.extract(frame.storage_uri, "image")
                if ocr.text:
                    frame_ocr_parts.append(ocr.text)
                    frame_ocr_regions.append(
                        {"time_ms": frame.time_ms, "text": ocr.text, "regions": ocr.regions}
                    )
        if frame_ocr_parts:
            frame_text = "\n".join(frame_ocr_parts)
            await self._repository.create_transcript(
                asset_id=asset.id,
                kind="keyframe_ocr",
                language="chi_sim+eng",
                segments=frame_ocr_regions,
                full_text=frame_text,
                provider=self._ocr.__class__.__name__,
            )
            await self._ingest_derived_text(
                asset,
                "keyframe_ocr",
                frame_text,
                "chi_sim+eng",
                provenance={"segments": frame_ocr_regions[:100]},
            )
        await self._repository.update_job(
            job_id,
            status="succeeded",
            resource_stats={"frames": len(frames)},
            lease_owner=None,
            lease_expires_at=None,
        )
        await self._finalize_asset_if_done(asset.id)

    async def _execute_c2pa(self, asset: Any, job_id: str) -> None:
        data = b""
        if asset.storage_uri:
            try:
                data = self._read_head(asset.storage_uri, 4096)
            except OSError:
                data = b""
        result = await self._c2pa.verify(asset.storage_uri or "", data)
        await self._repository.update_asset(asset.id, c2pa_status=result.status)
        await self._repository.update_job(
            job_id,
            status="succeeded",
            resource_stats={"c2pa_status": result.status},
            lease_owner=None,
            lease_expires_at=None,
        )
        await self._finalize_asset_if_done(asset.id)

    # ---- 收尾与失败 ------------------------------------------------------

    async def _finalize_asset_if_done(self, asset_id: str) -> None:
        jobs = await self._repository.list_jobs(asset_id)
        active = [j for j in jobs if j.status in ("pending", "running")]
        if active:
            return
        failed = [j for j in jobs if j.status in ("failed", "failed_terminal")]
        status = "partial" if failed else "succeeded"
        await self._repository.update_asset(asset_id, analysis_status=status)

    async def _fail_job(self, job_id: str, exc: Exception) -> None:
        job = await self._repository.get_job(job_id)
        if job.attempt >= self._max_attempts:
            # 达到重试上限：标记终态，不再自动重领。
            await self._repository.update_job(
                job_id,
                status="failed_terminal",
                error_code=getattr(exc, "code", "job_error"),
                lease_owner=None,
                lease_expires_at=None,
            )
            await self._finalize_asset_if_done(job.asset_id)
        else:
            backoff = 2**job.attempt
            await self._repository.update_job(
                job_id,
                status="failed",
                error_code=getattr(exc, "code", "job_error"),
                next_retry_at=datetime.now(UTC) + timedelta(seconds=backoff),
                lease_owner=None,
                lease_expires_at=None,
            )

    @staticmethod
    def _read_head(path: str, size: int) -> bytes:
        with open(path, "rb") as handle:
            return handle.read(size)
