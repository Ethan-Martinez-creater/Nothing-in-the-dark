"""V3 Part A: 确定性调查质量评估（InvestigationQualityService）。

6 个固定维度（§13，不得增删）：collection_coverage / evidence_coverage /
finding_support / review_resolution / provenance_integrity / report_citation。

固定权重（§20）：25/25/20/10/10/10；维度 None 从总权重分母移除，不按 0 分。
Quality Score 表示调查完整度与准备度，不代表事件结论真实性（§24 文案红线）。

fingerprint（§22）：canonical JSON + SHA256；未变化直接返回缓存 record。
全部计算 deterministic、无 LLM。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.application.collection_service import CollectionDefinitionService
from app.application.report_document_service import ReportDocumentService
from app.application.repositories import ApplicationRepository
from app.core import v3
from app.infrastructure.database.collection_run_repository import (
    CollectionRunRepository,
)
from app.infrastructure.database.engine import Database
from app.infrastructure.database.finding_repository import FindingRepository
from app.infrastructure.database.investigation_quality_repository import (
    InvestigationQualityRepository,
)
from app.infrastructure.database.social_repository import SocialRepository

QUALITY_DISCLAIMER = (
    "Quality Score 表示调查完整度与准备度，不代表事实真实性。"
)

_DIMENSION_LABELS = {
    "collection_coverage": "Collection Coverage",
    "evidence_coverage": "Evidence Coverage",
    "finding_support": "Finding Support",
    "review_resolution": "Resolution",
    "provenance_integrity": "Provenance Integrity",
    "report_citation": "Report Citation",
}

# 权重固定（§20），不得调整。
_DIMENSION_WEIGHTS = {
    "collection_coverage": 25,
    "evidence_coverage": 25,
    "finding_support": 20,
    "review_resolution": 10,
    "provenance_integrity": 10,
    "report_citation": 10,
}

_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _sha256(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _gap(
    *,
    code: str,
    severity: str,
    object_type: str,
    object_id: str | None,
    message: str,
    case_id: str,
    target: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "object_type": object_type,
        "object_id": object_id,
        "message": message,
        "action": {
            "type": "navigate",
            "target": target.format(case_id=case_id),
        },
    }


def _dimension(
    key: str, score: float | None, metrics: dict[str, Any]
) -> dict[str, Any]:
    return {
        "key": key,
        "label": _DIMENSION_LABELS[key],
        "weight": _DIMENSION_WEIGHTS[key],
        "score": score,
        "available": score is not None,
        "metrics": metrics,
    }


def _grade_for(overall: float | None) -> str:
    if overall is None:
        return v3.QUALITY_GRADE_INSUFFICIENT
    if overall >= v3.QUALITY_GRADE_STRONG_THRESHOLD:
        return v3.QUALITY_GRADE_STRONG
    if overall >= v3.QUALITY_GRADE_ACCEPTABLE_THRESHOLD:
        return v3.QUALITY_GRADE_ACCEPTABLE
    if overall >= v3.QUALITY_GRADE_NEEDS_ATTENTION_THRESHOLD:
        return v3.QUALITY_GRADE_NEEDS_ATTENTION
    return v3.QUALITY_GRADE_WEAK


class InvestigationQualityService:
    def __init__(
        self,
        *,
        repository: ApplicationRepository,
        social_repository: SocialRepository,
        collection_run_repository: CollectionRunRepository,
        finding_repository: FindingRepository,
        quality_repository: InvestigationQualityRepository,
        report_document_service: ReportDocumentService,
        collection_definition_service: CollectionDefinitionService,
        database: Database,
    ) -> None:
        self._repository = repository
        self._social = social_repository
        self._collection_runs = collection_run_repository
        self._findings = finding_repository
        self._quality = quality_repository
        self._reports_service = report_document_service
        self._definitions = collection_definition_service
        self._database = database

    # ---------------- public ----------------

    async def evaluate(self, case_id: str, *, force: bool = False) -> dict[str, Any]:
        """fresh-if-needed（GET）与 force recompute（refresh）的统一入口。"""
        case = await self._repository.get_case(case_id)
        fingerprint = await self._input_fingerprint(case_id, case)
        if not force:
            cached = await self._quality.get(case_id)
            if (
                cached is not None
                and cached.input_fingerprint == fingerprint
                and cached.algorithm_version == v3.QUALITY_ALGORITHM_VERSION
            ):
                return self._response(cached)
        record = await self._compute_and_store(case_id, case, fingerprint)
        return self._response(record)

    async def latest(self, case_id: str) -> dict[str, Any] | None:
        record = await self._quality.get(case_id)
        return self._response(record) if record is not None else None

    async def list_needing_attention(self, limit: int = 5) -> list[dict[str, Any]]:
        records = await self._quality.list_needing_attention(limit=limit)
        return [self._attention_entry(record) for record in records]

    # ---------------- fingerprint（§22） ----------------

    async def _input_fingerprint(self, case_id: str, case: Any) -> str:
        claim_evidence = await self._repository.get_claim_evidence_quality_metrics(
            case_id
        )
        finding_metrics = await self._findings.get_quality_metrics(case_id)
        review_metrics = await self._repository.get_review_decision_quality_metrics(
            case_id
        )
        posts_count = await self._social.count_posts(case_id)
        posts_latest = await self._social.latest_post_created_at(case_id)

        definition_payload: dict[str, Any] = None
        active_definition = await self._definitions.get_active(case_id)
        if active_definition is not None:
            definition_payload = {
                "id": active_definition.id,
                "version": active_definition.version,
                "updated_at": str(active_definition.updated_at),
            }
            latest_run = await self._collection_runs.latest_for_definition(
                case_id,
                definition_id=active_definition.id,
                definition_version=active_definition.version,
            )
        else:
            latest_run = None
        run_payload: dict[str, Any] = None
        if latest_run is not None:
            run_payload = {
                "id": latest_run.id,
                "status": latest_run.status,
                "updated_at": str(latest_run.updated_at),
            }

        report_payload: dict[str, Any] = None
        latest_report = await self._select_report(case_id)
        if latest_report is not None:
            report_payload = {
                "id": latest_report.id,
                "status": latest_report.status,
                "lock_version": latest_report.lock_version,
                "updated_at": str(latest_report.updated_at),
            }

        return _sha256(
            {
                "case_updated_at": str(case.updated_at),
                "collection_definition": definition_payload,
                "collection_run": run_payload,
                "posts": {
                    "count": posts_count,
                    "latest_created_at": str(posts_latest),
                },
                "claims": {
                    "count": claim_evidence["claims_total"],
                    "latest_created_at": str(claim_evidence["latest_claim_at"]),
                },
                "evidence": {
                    "count": claim_evidence["evidence_total"],
                    "latest_created_at": str(claim_evidence["latest_evidence_at"]),
                },
                "findings": {
                    "count": finding_metrics["findings_total"],
                    "latest_updated_at": str(
                        finding_metrics["latest_finding_updated_at"]
                    ),
                },
                "finding_evidence_links": {
                    "count": finding_metrics["evidence_link_count"],
                    "latest_created_at": str(
                        finding_metrics["latest_evidence_link_at"]
                    ),
                },
                "finding_source_links": {
                    "count": finding_metrics["source_link_count"],
                    "latest_created_at": str(
                        finding_metrics["latest_source_link_at"]
                    ),
                },
                "review_decisions": {
                    "count": review_metrics["review_decision_count"],
                    "latest_created_at": str(
                        review_metrics["latest_review_decision_at"]
                    ),
                },
                "latest_report": report_payload,
            }
        )

    # ---------------- 维度计算（§14-§19） ----------------

    async def _compute_and_store(
        self, case_id: str, case: Any, fingerprint: str
    ) -> Any:
        collection = await self._dimension_collection_coverage(case_id, case)
        evidence = await self._dimension_evidence_coverage(case_id)
        finding_support = await self._dimension_finding_support(case_id)
        resolution = await self._dimension_review_resolution(case_id)
        provenance = await self._dimension_provenance_integrity(case_id)
        report_citation = await self._dimension_report_citation(case_id)

        dimensions = {
            "collection_coverage": collection,
            "evidence_coverage": evidence,
            "finding_support": finding_support,
            "review_resolution": resolution,
            "provenance_integrity": provenance,
            "report_citation": report_citation,
        }
        gaps: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        for computed in (
            collection,
            evidence,
            finding_support,
            resolution,
            provenance,
            report_citation,
        ):
            gaps.extend(computed["gaps"])
            warnings.extend(computed["warnings"])

        available_scores = {
            key: computed["score"]
            for key, computed in dimensions.items()
            if computed["score"] is not None
        }
        if available_scores:
            weight_sum = sum(_DIMENSION_WEIGHTS[key] for key in available_scores)
            overall = sum(
                score * _DIMENSION_WEIGHTS[key]
                for key, score in available_scores.items()
            ) / weight_sum
            overall = round(overall, 2)
        else:
            overall = None
        gaps.sort(key=lambda gap: _SEVERITY_RANK.get(gap["severity"], 3))

        record = await self._quality.upsert(
            case_id=case_id,
            overall_score=overall,
            grade=_grade_for(overall),
            dimensions={
                key: computed["dimension"] for key, computed in dimensions.items()
            },
            metrics={key: computed["metrics"] for key, computed in dimensions.items()},
            gaps=gaps,
            warnings=warnings,
            input_fingerprint=fingerprint,
            algorithm_version=v3.QUALITY_ALGORITHM_VERSION,
            computed_at=_utc_now(),
        )
        return record

    async def _dimension_collection_coverage(
        self, case_id: str, case: Any
    ) -> dict[str, Any]:
        gaps: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        active_definition = await self._definitions.get_active(case_id)
        expected_platforms = list(
            (active_definition.platforms if active_definition else None)
            or case.platforms
            or []
        )
        covered: list[str] = []
        missing: list[str] = []
        in_progress_platforms: list[str] = []
        collection_in_progress = False
        latest_run_id: str | None = None

        if active_definition is not None:
            terminal_run = await self._collection_runs.latest_terminal_for_definition(
                case_id,
                active_definition.id,
                active_definition.version,
            )
            if terminal_run is not None:
                latest_run_id = terminal_run.id
            active_run = await self._collection_runs.latest_for_definition(
                case_id,
                definition_id=active_definition.id,
                definition_version=active_definition.version,
                statuses=("queued", "running"),
            )
            collection_in_progress = active_run is not None
            if terminal_run is not None and terminal_run.status in (
                "completed",
                "completed_with_errors",
            ):
                platforms_state = (
                    terminal_run.progress_json or {}
                ).get("platforms") or {}
                for platform in expected_platforms:
                    status = (platforms_state.get(platform) or {}).get("status")
                    if status == "completed":
                        covered.append(platform)
                    elif collection_in_progress and status in ("queued", "running"):
                        # 运行中的平台不得提前成为最终 missing critical（§14）
                        in_progress_platforms.append(platform)
                    else:
                        missing.append(platform)
            elif not collection_in_progress:
                # 历史 case 完全没有匹配 run：fallback 到 SourcePost 覆盖
                covered, missing = await self._fallback_coverage(
                    case_id, expected_platforms
                )
            elif active_run is not None:
                latest_run_id = latest_run_id or active_run.id
                platforms_state = (active_run.progress_json or {}).get(
                    "platforms"
                ) or {}
                for platform in expected_platforms:
                    status = (platforms_state.get(platform) or {}).get("status")
                    if status == "completed":
                        covered.append(platform)
                    else:
                        in_progress_platforms.append(platform)
        else:
            posts_count = await self._social.count_posts(case_id)
            if posts_count == 0:
                # 尚未开始采集（无 definition、无 run、无数据）：不可评，
                # 视为 None 而不是 0 分（§6 insufficient_data 语义）
                covered, missing = [], []
            else:
                covered, missing = await self._fallback_coverage(
                    case_id, expected_platforms
                )

        score: float | None = None
        if expected_platforms and (covered or missing or in_progress_platforms):
            score = len(covered) / len(expected_platforms) * 100
        else:
            score = None

        run_finished = latest_run_id is not None and not collection_in_progress
        if missing and run_finished:
            missing_ratio = len(missing) / len(expected_platforms)
            severity = "critical" if missing_ratio >= 0.5 else "warning"
            gaps.append(
                _gap(
                    code="missing_collection_platform",
                    severity=severity,
                    object_type="collection",
                    object_id=latest_run_id,
                    message=(
                        f"采集目标平台未完成：{', '.join(missing)}"
                    ),
                    case_id=case_id,
                    target="/investigations/{case_id}/overview",
                )
            )
        if collection_in_progress:
            # §14：active run 尚未完成 → info only，不得提前产生 critical
            gaps.append(
                _gap(
                    code="collection_in_progress",
                    severity="info",
                    object_type="collection",
                    object_id=latest_run_id,
                    message="存在进行中的采集任务，覆盖度尚未最终确定。",
                    case_id=case_id,
                    target="/investigations/{case_id}/overview",
                )
            )

        metrics = {
            "expected_platforms": expected_platforms,
            "covered_platforms": covered,
            "missing_platforms": missing,
            "in_progress_platforms": in_progress_platforms,
            "collection_in_progress": collection_in_progress,
            "latest_collection_run_id": latest_run_id,
        }
        return {
            "score": score,
            "metrics": metrics,
            "gaps": gaps,
            "warnings": warnings,
            "dimension": _dimension("collection_coverage", score, metrics),
        }

    async def _fallback_coverage(
        self, case_id: str, expected_platforms: list[str]
    ) -> tuple[list[str], list[str]]:
        by_platform = dict(
            await self._social.count_posts_by_platform(case_id)
        )
        covered = [p for p in expected_platforms if by_platform.get(p, 0) > 0]
        missing = [p for p in expected_platforms if by_platform.get(p, 0) == 0]
        return covered, missing

    async def _dimension_evidence_coverage(self, case_id: str) -> dict[str, Any]:
        gaps: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        metrics_data = await self._repository.get_claim_evidence_quality_metrics(
            case_id
        )
        claims_total = int(metrics_data["claims_total"])
        claims_with_evidence = int(metrics_data["claims_with_evidence"])
        evidence_total = int(metrics_data["evidence_total"])
        if claims_total > 0:
            score = claims_with_evidence / claims_total * 100
        elif evidence_total > 0:
            score = 100.0
        else:
            score = None
        without_count = claims_total - claims_with_evidence
        if without_count > 0:
            sample_ids = await self._claims_without_evidence_sample(case_id)
            gaps.append(
                _gap(
                    code="claim_without_evidence",
                    severity="warning",
                    object_type="claim",
                    object_id=None,
                    message=f"{without_count} 个主张未绑定任何 Evidence。",
                    case_id=case_id,
                    target="/investigations/{case_id}/evidence",
                )
            )
            warnings.append(
                {
                    "code": "claim_without_evidence",
                    "count": without_count,
                    "claim_ids": sample_ids,
                }
            )
        metrics = {
            "claims_total": claims_total,
            "claims_with_evidence": claims_with_evidence,
            "evidence_total": evidence_total,
            "claims_without_evidence_count": without_count,
        }
        return {
            "score": score,
            "metrics": metrics,
            "gaps": gaps,
            "warnings": warnings,
            "dimension": _dimension("evidence_coverage", score, metrics),
        }

    async def _claims_without_evidence_sample(
        self, case_id: str, limit: int = 20
    ) -> list[str]:
        async with self._database.session_factory() as session:
            from sqlalchemy import select

            from app.infrastructure.database.models import (
                ClaimRecord,
                EvidenceRecord,
            )

            result = await session.scalars(
                select(ClaimRecord.id)
                .where(
                    ClaimRecord.case_id == case_id,
                    ~select(EvidenceRecord.id)
                    .where(EvidenceRecord.claim_id == ClaimRecord.id)
                    .exists(),
                )
                .limit(limit)
            )
            return list(result.all())

    async def _dimension_finding_support(self, case_id: str) -> dict[str, Any]:
        gaps: list[dict[str, Any]] = []
        metrics_data = await self._findings.get_quality_metrics(case_id)
        findings_total = int(metrics_data["findings_total"])
        findings_with_support = int(metrics_data["findings_with_support"])
        score: float | None = None
        if findings_total > 0:
            score = findings_with_support / findings_total * 100
        for finding_id in metrics_data["verified_without_support_ids"]:
            # verified 且 0 supports link → critical（§16），即使有
            # contradicts/context 也不算支持
            gaps.append(
                _gap(
                    code="verified_finding_without_supporting_evidence",
                    severity="critical",
                    object_type="finding",
                    object_id=finding_id,
                    message="verified Finding 缺少 supports 关系的证据链接。",
                    case_id=case_id,
                    target="/investigations/{case_id}/findings",
                )
            )
        metrics = {
            "findings_total": findings_total,
            "findings_with_support": findings_with_support,
            "verified_findings": int(metrics_data["verified_findings"]),
            "verified_findings_without_support": int(
                metrics_data["verified_findings_without_support"]
            ),
        }
        return {
            "score": score,
            "metrics": metrics,
            "gaps": gaps,
            "warnings": [],
            "dimension": _dimension("finding_support", score, metrics),
        }

    async def _dimension_review_resolution(self, case_id: str) -> dict[str, Any]:
        metrics_data = await self._findings.get_quality_metrics(case_id)
        findings_total = int(metrics_data["findings_total"])
        terminal_findings = int(metrics_data["terminal_findings"])
        score: float | None = None
        if findings_total > 0:
            score = terminal_findings / findings_total * 100
        metrics = {
            "findings_total": findings_total,
            "terminal_findings": terminal_findings,
        }
        return {
            "score": score,
            "metrics": metrics,
            "gaps": [],
            "warnings": [],
            # UI 名称必须是 Resolution（§17），不是 Accuracy
            "dimension": _dimension("review_resolution", score, metrics),
        }

    async def _dimension_provenance_integrity(
        self, case_id: str
    ) -> dict[str, Any]:
        gaps: list[dict[str, Any]] = []
        checked_refs = 0
        dangling_refs = 0
        critical_count = 0

        link_metrics = (
            await self._repository.get_finding_link_integrity_metrics(case_id)
        )
        checked_refs += int(link_metrics["checked_refs"])
        dangling_refs += int(link_metrics["dangling_refs"])
        critical_count += len(link_metrics["critical_dangling"])
        for entry in link_metrics["critical_dangling"]:
            gaps.append(
                _gap(
                    code="dangling_finding_link",
                    severity="critical",
                    object_type=entry["object_type"],
                    object_id=entry["object_id"],
                    message=f"引用对象不存在或不属于当前调查：{entry['ref']}",
                    case_id=case_id,
                    target="/investigations/{case_id}/findings",
                )
            )
        for entry in link_metrics["warning_dangling"]:
            gaps.append(
                _gap(
                    code="dangling_finding_link",
                    severity="warning",
                    object_type=entry["object_type"],
                    object_id=entry["object_id"],
                    message=f"引用对象不存在或不属于当前调查：{entry['ref']}",
                    case_id=case_id,
                    target="/investigations/{case_id}/findings",
                )
            )

        # Report citation：复用 ReportDocumentService 的同一 parser/validator
        report = await self._select_report(case_id)
        report_dangling = 0
        if report is not None:
            content = (
                report.content_json if isinstance(report.content_json, dict) else {}
            )
            citation_links = content.get("citation_links") or []
            problems = await self._reports_service.validate_citation_links(
                case_id, citation_links
            )
            report_dangling = len(problems)
            checked_refs += report_dangling
            dangling_refs += report_dangling
            if report_dangling > 0:
                severity = (
                    "critical" if report.status == "published" else "warning"
                )
                if report.status == "published":
                    critical_count += 1
                for problem in problems:
                    gaps.append(
                        _gap(
                            code="dangling_report_citation",
                            severity=severity,
                            object_type="report_document",
                            object_id=report.id,
                            message=(
                                f"报告引用不可解析：{problem.get('field')}"
                                f"（{problem.get('issue')}）"
                            ),
                            case_id=case_id,
                            target="/investigations/{case_id}/report",
                        )
                    )

        score: float | None = None
        if checked_refs > 0:
            score = 100 * (checked_refs - dangling_refs) / checked_refs
        metrics = {
            "checked_refs": checked_refs,
            "dangling_refs": dangling_refs,
            "critical_dangling_count": critical_count,
        }
        return {
            "score": score,
            "metrics": metrics,
            "gaps": gaps,
            "warnings": [],
            "dimension": _dimension("provenance_integrity", score, metrics),
        }

    async def _dimension_report_citation(self, case_id: str) -> dict[str, Any]:
        report = await self._select_report(case_id)
        if report is None:
            score = None
            metrics: dict[str, Any] = {"report_selected": False}
            gaps: list[dict[str, Any]] = []
        else:
            validation = await self._reports_service.validate_for_publish(
                case_id, report.id
            )
            problem_count = len(validation["problems"])
            if problem_count == 0:
                score = 100.0
            elif problem_count <= 2:
                score = 70.0
            elif problem_count <= 5:
                score = 40.0
            else:
                score = 0.0
            metrics = {
                "report_selected": True,
                "report_id": report.id,
                "report_status": report.status,
                "problem_count": problem_count,
                # Publish readiness，不是内容质量分（§19）
                "publish_readiness": True,
            }
            gaps = [
                _gap(
                    code="report_publish_problem",
                    severity="warning",
                    object_type="report_document",
                    object_id=report.id,
                    message=f"报告发布校验存在 {problem_count} 个问题。",
                    case_id=case_id,
                    target="/investigations/{case_id}/report",
                )
            ] if problem_count > 0 else []
        return {
            "score": score,
            "metrics": metrics,
            "gaps": gaps,
            "warnings": [],
            "dimension": _dimension("report_citation", score, metrics),
        }

    async def _select_report(self, case_id: str) -> Any:
        """§19：latest published → latest in_review → latest draft。

        list_for_case 按 created_at DESC 返回，每个状态取第一条即最新。
        """
        records = await self._reports_service.list_for_case(case_id)
        for status in ("published", "in_review", "draft"):
            for record in records:
                if record.status == status:
                    return record
        return None

    # ---------------- response ----------------

    def _response(self, record: Any) -> dict[str, Any]:
        dimensions = [
            {
                "key": key,
                "label": _DIMENSION_LABELS[key],
                "weight": _DIMENSION_WEIGHTS[key],
                "score": (record.dimensions_json or {}).get(key, {}).get("score"),
                "available": (record.dimensions_json or {}).get(key, {}).get("score")
                is not None,
                "metrics": (record.metrics_json or {}).get(key, {}),
            }
            for key in _DIMENSION_LABELS
        ]
        return {
            "case_id": record.case_id,
            "overall_score": record.overall_score,
            "grade": record.grade,
            "dimensions": dimensions,
            "gaps": list(record.gaps_json or []),
            "warnings": list(record.warnings_json or []),
            "disclaimer": QUALITY_DISCLAIMER,
            "computed_at": record.computed_at,
            "algorithm_version": record.algorithm_version,
            "input_fingerprint": record.input_fingerprint,
        }

    def _attention_entry(self, record: Any) -> dict[str, Any]:
        return {
            "case_id": record.case_id,
            "overall_score": record.overall_score,
            "grade": record.grade,
            "computed_at": record.computed_at,
        }
