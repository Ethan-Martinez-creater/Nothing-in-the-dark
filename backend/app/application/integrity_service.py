"""Social integrity analysis application service (07)."""

from __future__ import annotations

import hashlib
from typing import Any

from app.infrastructure.database.integrity_repository import IntegrityRepository
from app.services import integrity


class IntegrityService:
    """扫描案例账号与帖子，产出单账号风险信号与协同群体候选。"""

    def __init__(
        self,
        repository: IntegrityRepository,
        app_repository: Any,
        social: Any,
    ) -> None:
        self._repository = repository
        self._app = app_repository
        self._social = social

    async def analyze_case(self, case_id: str) -> dict[str, int]:
        accounts = await self._app.list_accounts(case_id=case_id, limit=1000)
        posts = await self._social.list_posts_by_case(case_id)

        assessments = 0
        for account in accounts:
            account_posts = [
                p
                for p in posts
                if p.platform == account.platform
                and (p.author_id == account.native_id or p.author_name == account.name)
            ]
            account_dict = {
                "follower_count": account.follower_count,
                "following_count": (account.metadata_json or {}).get("following_count"),
            }
            post_dicts = [
                {
                    "content": p.content,
                    "published_at": p.published_at,
                    "url": p.source_url,
                }
                for p in account_posts
            ]
            policy_record = await self._repository.get_effective_policy(account.platform)
            policy = None
            model_version = "rules-1.1.0"
            if policy_record is not None:
                policy = {
                    "thresholds": policy_record.thresholds or {},
                    "weights": policy_record.weights or {},
                }
                model_version = policy_record.version
            result = integrity.account_risk_assessment(account_dict, post_dicts, policy)
            published = [p["published_at"] for p in post_dicts if p.get("published_at")]
            window_start = min(published) if published else None
            window_end = max(published) if published else None
            subject_id = f"{account.platform}:{account.native_id}"
            for feature_name, feature in {
                "interval_regularity": integrity.interval_regularity(published),
                "duplicate_text_rate": integrity.duplicate_text_rate(post_dicts),
            }.items():
                if feature["value"] is not None:
                    await self._repository.create_behavior_snapshot(
                        case_id=case_id,
                        subject_type="account",
                        subject_id=subject_id,
                        feature_name=feature_name,
                        feature_value=float(feature["value"]),
                        coverage={"status": feature["coverage"], "posts": len(post_dicts)},
                        window_start=window_start,
                        window_end=window_end,
                        extract_version=model_version,
                    )
            for risk_type in integrity.RISK_TYPES:
                await self._repository.upsert_risk_assessment(
                    case_id=case_id,
                    subject_type="account",
                    subject_id=subject_id,
                    risk_type=risk_type,
                    score=result["scores"][risk_type],
                    band=result["bands"][risk_type],
                    reason_codes=result["reason_codes_by_risk"][risk_type],
                    evidence_refs=result["evidence_by_risk"][risk_type],
                    model_version=model_version,
                )
                assessments += 1

        clusters = 0
        cluster_ids: list[str] = []
        by_author: dict[str, list[dict[str, Any]]] = {}
        for post in posts:
            author = f"{post.platform}:{post.author_id or post.author_name or 'unknown'}"
            by_author.setdefault(author, []).append(
                {
                    "content": post.content,
                    "published_at": post.published_at,
                    "url": post.source_url,
                }
            )
        published_times = [
            p.published_at for p in posts if p.published_at is not None
        ]
        window_start = min(published_times) if published_times else None
        window_end = max(published_times) if published_times else None
        algorithm_version = "sparse-signals-1.1.0"
        for cluster in integrity.detect_coordination(by_author, min_support=2):
            fingerprint = hashlib.sha256(
                (
                    f"{case_id}|{window_start}|{window_end}|"
                    f"{sorted(cluster['account_ids'])}|{algorithm_version}"
                ).encode()
            ).hexdigest()[:32]
            record = await self._repository.create_cluster(
                case_id=case_id,
                size=cluster["size"],
                score=cluster["score"],
                explanation=_explain_cluster(cluster),
                fingerprint=fingerprint,
                algorithm_version=algorithm_version,
                window_start=window_start,
                window_end=window_end,
                members=[
                    {
                        "account_id": account_id,
                        "score": cluster["score"],
                        "evidence": {
                            "edges": cluster["evidence"].get(account_id, [])
                        },
                    }
                    for account_id in cluster["account_ids"]
                ],
            )
            cluster_ids.append(record.id)
            clusters += 1

        # V3 §52：coordination_cluster detector 以最新 succeeded integrity job
        # 的 result_json.cluster_ids 为 scope；window 用于展示检测窗口。
        return {
            "assessments": assessments,
            "clusters": clusters,
            "cluster_ids": cluster_ids,
            "window_start": window_start.isoformat() if window_start else None,
            "window_end": window_end.isoformat() if window_end else None,
        }

    async def compute_views(self, case_id: str) -> dict[str, Any]:
        """原始/降权/排除三套关键指标（INT-P1-05）。

        原始结果永远保留；降权/排除是明确选择的派生视图，永不删原数据。
        """
        posts = await self._social.list_posts_by_case(case_id)
        assessments = await self._repository.list_assessments(case_id, limit=5000)
        active_assessments = [
            assessment
            for assessment in assessments
            if assessment.status != "reviewed_unlikely"
            and (assessment.band == "high" or assessment.status == "reviewed_likely")
        ]
        risk_by_subject: dict[str, float] = {}
        for assessment in active_assessments:
            risk_by_subject[assessment.subject_id] = max(
                risk_by_subject.get(assessment.subject_id, 0.0), assessment.score
            )
        high_subjects = set(risk_by_subject)

        raw = {"post_count": 0, "engagement_total": 0}
        downweighted = {"post_count": 0, "engagement_total": 0}
        excluded = {"post_count": 0, "engagement_total": 0}

        for post in posts:
            subject = f"{post.platform}:{post.author_id or post.author_name}"
            is_high = subject in high_subjects
            engagement = 0
            if isinstance(post.engagement, dict):
                try:
                    engagement = int(post.engagement.get("total", 0) or 0)
                except (TypeError, ValueError):
                    engagement = 0
            raw["post_count"] += 1
            raw["engagement_total"] += engagement
            weight = max(0.1, 1.0 - risk_by_subject.get(subject, 0.0))
            downweighted["post_count"] += weight
            downweighted["engagement_total"] += engagement * weight
            if not is_high:
                excluded["post_count"] += 1
                excluded["engagement_total"] += engagement

        return {
            "raw": raw,
            "downweighted": downweighted,
            "excluded": excluded,
            "high_risk_accounts": len(high_subjects),
            "delta": {
                "post_count": raw["post_count"] - excluded["post_count"],
                "engagement_total": raw["engagement_total"] - excluded["engagement_total"],
            },
        }


def _explain_cluster(cluster: dict[str, Any]) -> str:
    accounts = ", ".join(cluster["account_ids"][:5])
    signals = ", ".join(cluster["shared_signals"][:5])
    return f"疑似协同：{len(cluster['account_ids'])} 个账号共享信号 [{signals}]（成员：{accounts}）"
