"""把冻结 fixture 落到真实数据库（全部走生产写入路径）。

seed 只做"数据预置"：不调用 agent、不调用模型、不访问公网。
每条写入都使用生产 repository / service，因此 fixture 覆盖的存储结构与
真实运行完全一致（这也是 Tier A 能验证真实 runtime 的前提）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from app.schemas.cases import CreateCaseRequest

#: seed 产生的 run 使用独立 agent 名，便于与真实 agent run 区分。
SEED_AGENT = "eval_fixture_seed"
SEED_OBJECTIVE = "[interview_agent_v1 fixture seed]"


@dataclass
class EvalDataStack:
    """评测用的真实生产组件装配（只读工具 + 各 repository）。"""

    database: Any
    repository: Any
    social: Any
    knowledge: Any
    finding_service: Any
    finding_repository: Any
    report_service: Any
    report_repository: Any
    workspace_repository: Any
    cross_repository: Any
    signal_repository: Any


@dataclass(frozen=True, slots=True)
class SeededFixture:
    """seed 结果：fixture 内逻辑 key → 运行时真实 id。"""

    fixture_id: str
    cases: dict[str, str] = field(default_factory=dict)
    ref_map: dict[str, str] = field(default_factory=dict)

    def case_id(self, investigation_key: str) -> str:
        try:
            return self.cases[investigation_key]
        except KeyError as exc:
            raise KeyError(
                f"investigation {investigation_key!r} not seeded; have {sorted(self.cases)}"
            ) from exc

    #: ref_map 中的 category 前缀，便于 evaluator 分类已知 id。
    def known_ids(self) -> dict[str, set[str]]:
        buckets: dict[str, set[str]] = {
            "case": set(),
            "post": set(),
            "account": set(),
            "claim": set(),
            "evidence": set(),
            "finding": set(),
            "artifact": set(),
            "review": set(),
        }
        for key, value in self.ref_map.items():
            category = key.split(":", 1)[0]
            buckets.setdefault(category, set()).add(value)
        buckets["artifact"] |= buckets.get("report", set())
        return {key: value for key, value in buckets.items() if value}


# ---------------------------------------------------------------------------
# 转换辅助
# ---------------------------------------------------------------------------


def _post_payload(post: Mapping[str, Any]) -> dict[str, object]:
    comments: list[dict[str, object]] = []
    for comment in post.get("comments", []) or []:
        comments.append(
            {
                "native_id": comment["native_id"],
                "content": comment.get("content", ""),
                "author_id": comment.get("author_id", ""),
                "author_name": comment.get("author_name", ""),
                "published_at": comment.get("published_at"),
                "metrics": dict(comment.get("metrics", {}) or {}),
                "raw": {"user_id": comment.get("author_id", "")},
            }
        )
    return {
        "platform": post["platform"],
        "native_id": post["native_id"],
        "content": post.get("content", ""),
        "title": post.get("title", ""),
        "author": post.get("author", ""),
        "url": post.get("url", ""),
        "published_at": post.get("published_at"),
        "engagement": post.get("engagement", 0),
        "content_type": post.get("content_type", "post"),
        "metrics": dict(post.get("metrics", {}) or {}),
        # author_id 只能通过 raw 注入（见 SocialRepository.persist_batch）。
        "raw": {"user_id": post.get("author_id", "")},
        "comments": comments,
    }


# ---------------------------------------------------------------------------
# seed 主流程
# ---------------------------------------------------------------------------


async def seed_fixture(
    stack: EvalDataStack,
    *,
    fixture_id: str,
    fixture: Mapping[str, Any],
) -> SeededFixture:
    """预置一个 fixture 的全部数据，返回逻辑 key → 真实 id 映射。"""
    repository = stack.repository
    cases: dict[str, str] = {}
    ref_map: dict[str, str] = {}

    for investigation in fixture.get("investigations", []):
        inv_key = investigation["key"]
        case = await repository.create_case(
            CreateCaseRequest(
                topic=investigation["topic"],
                title=investigation.get("title"),
                description=investigation.get("description", ""),
                platforms=list(investigation.get("platforms", ["weibo"])),
                time_start=investigation.get("time_start"),
                time_end=investigation.get("time_end"),
            )
        )
        cases[inv_key] = case.id
        ref_map[f"case:{inv_key}"] = case.id

        # claims.created_by_run_id 是非空外键；seed run 直接置为 completed，
        # 避免被 worker 拾取执行。
        seed_run = await repository.create_agent_run(
            case_id=case.id,
            turn_id=None,
            objective=SEED_OBJECTIVE,
            agent=SEED_AGENT,
        )
        await repository.update_agent_run(seed_run.id, status="completed")

        for account in investigation.get("accounts", []) or []:
            record = await repository.upsert_account(
                case_id=case.id,
                platform=account["platform"],
                native_id=account["native_id"],
                name=account.get("name", ""),
                normalized_name=account.get("normalized_name", account.get("name", "")),
            )
            ref_map[f"account:{account['key']}"] = record.id

        if investigation.get("posts"):
            await stack.social.persist_batch(
                case_id=case.id,
                posts=[_post_payload(post) for post in investigation["posts"]],
            )
            posts = await stack.social.list_posts_by_case(case.id)
            by_native = {record.native_id: record.id for record in posts}
            for post in investigation["posts"]:
                record_id = by_native.get(post["native_id"])
                if record_id is None:
                    raise RuntimeError(
                        f"fixture {fixture_id}: post {post['key']} not persisted"
                    )
                ref_map[f"post:{post['key']}"] = record_id

        # 媒体资产：跨调查的 shared_media detector 依赖 normalized_url / phash。
        for media in investigation.get("media", []) or []:
            record = await repository.create_media_asset(
                case_id=case.id,
                post_id=ref_map.get(f"post:{media['post_key']}")
                if media.get("post_key")
                else None,
                platform=media.get("platform", "weibo"),
                media_type=media.get("media_type", "image"),
                url=media["url"],
                normalized_url=media["normalized_url"],
                file_sha256=media.get("file_sha256"),
                phash=media.get("phash"),
            )
            ref_map[f"media:{media['key']}"] = record.id

        for claim in investigation.get("claims", []) or []:
            record = await repository.create_claim(
                case_id=case.id,
                text=claim["text"],
                created_by_run_id=seed_run.id,
            )
            ref_map[f"claim:{claim['key']}"] = record.id

        for evidence in investigation.get("evidence", []) or []:
            claim_id = (
                ref_map.get(f"claim:{evidence['claim_key']}")
                if evidence.get("claim_key")
                else None
            )
            source_id = (
                ref_map.get(f"post:{evidence['source_post_key']}")
                if evidence.get("source_post_key")
                else evidence.get("source_id", "")
            )
            record = await repository.create_evidence(
                case_id=case.id,
                claim_id=claim_id,
                source_type=evidence.get("source_type", "social_post"),
                source_id=source_id,
                stance=evidence.get("stance", "context"),
                excerpt=evidence.get("excerpt", ""),
                relevance=float(evidence.get("relevance", 0.0)),
            )
            ref_map[f"evidence:{evidence['key']}"] = record.id

        for finding in investigation.get("findings", []) or []:
            links = [
                (ref_map[f"evidence:{ref['evidence_key']}"], ref.get("relation", "supports"))
                for ref in finding.get("evidence_refs", []) or []
                if f"evidence:{ref['evidence_key']}" in ref_map
            ]
            record = await stack.finding_service.create_manual(
                case.id,
                kind=finding.get("kind", "manual"),
                title=finding.get("title"),
                statement=finding["statement"],
                confidence=finding.get("confidence"),
                evidence_links=links,
            )
            ref_map[f"finding:{finding['key']}"] = record.id
            if finding.get("status") == "under_review":
                # Finding → Review 的唯一原子入口（复合状态一致性）。
                await repository.submit_finding_for_review(
                    case_id=case.id,
                    finding_id=record.id,
                    actor=SEED_AGENT,
                )

        if investigation.get("report"):
            report = investigation["report"]
            artifact = await repository.create_artifact(
                case_id=case.id,
                kind="report",
                title=report.get("title", "阶段报告"),
                data={
                    "title": report.get("title", "阶段报告"),
                    "executive_summary": report.get("executive_summary", ""),
                    "sections": [
                        {
                            **section,
                            "evidence_ids": [
                                ref_map.get(f"evidence:{ref}", ref)
                                for ref in section.get("evidence_refs", []) or []
                            ],
                        }
                        for section in report.get("sections", []) or []
                    ],
                    "citation_links": [
                        {
                            "conclusion": report.get("title", "结论"),
                            "evidence_ids": [
                                ref_map.get(f"evidence:{ref}", ref)
                                for ref in report.get("citation_refs", []) or []
                                if ref.startswith("ev_") or ref.startswith("rv_")
                            ],
                        }
                    ],
                },
            )
            ref_map[f"artifact:report:{inv_key}"] = artifact.id
            document = await stack.report_service.import_from_artifact(
                case.id, artifact.id
            )
            ref_map[f"report:{inv_key}"] = document.id

    # -- fixture 级：跨调查实体 / 链接 / 信号 --------------------------------

    for entity in fixture.get("workspace_entities", []) or []:
        record = await stack.workspace_repository.create_with_key(
            entity_type=entity.get("entity_type", "account"),
            canonical_name=entity.get("canonical_name", ""),
            aliases=list(entity.get("aliases", []) or []),
            key_type="platform_account",
            key_value=f"{entity['platform']}:{entity['native_id']}",
            created_by=SEED_AGENT,
        )
        ref_map[f"entity:{entity['key']}"] = record.id
        for case_key in entity.get("case_keys", []) or []:
            case_id = cases.get(case_key)
            if not case_id:
                continue
            await stack.workspace_repository.upsert_case_link(
                entity_id=record.id,
                case_id=case_id,
                source_type="case_account",
                source_id=f"{entity['platform']}:{entity['native_id']}",
                method=SEED_AGENT,
            )

    for link in fixture.get("cross_links", []) or []:
        left = cases.get(link["left_case_key"])
        right = cases.get(link["right_case_key"])
        if not left or not right:
            continue
        await stack.cross_repository.upsert_link(
            left_case_id=left,
            right_case_id=right,
            relation_type=link["relation_type"],
            status=link.get("status", "observed"),
            score=float(link.get("score", 0.0)),
            evidence_count=int(link.get("evidence_count", 0)),
            evidence_refs=[
                {
                    "type": ref.get("type", "social_post"),
                    "post_id": ref_map.get(f"post:{ref['post_key']}", ""),
                }
                for ref in link.get("evidence_refs", []) or []
            ],
            feature_scores=dict(link.get("feature_scores", {}) or {}),
            algorithm_version=link.get("algorithm_version", "v1"),
        )

    for signal in fixture.get("signals", []) or []:
        case_id = cases.get(signal["case_key"])
        if not case_id:
            continue
        related_case_ids = [
            cases[key]
            for key in signal.get("related_case_keys", []) or []
            if key in cases
        ]
        await stack.signal_repository.upsert_observed_signal(
            fingerprint=signal["fingerprint"],
            case_id=case_id,
            source_type=signal.get("source_type", "cross_investigation"),
            source_id=signal.get("source_id", ""),
            signal_type=signal["signal_type"],
            severity=signal.get("severity", "medium"),
            title=signal.get("title", ""),
            why_it_matters=signal.get("why_it_matters", ""),
            confidence=signal.get("confidence"),
            metric_snapshot=dict(signal.get("metric_snapshot", {}) or {}),
            evidence_refs=[
                {
                    "type": ref.get("type", "social_post"),
                    "post_id": ref_map.get(f"post:{ref['post_key']}", ""),
                }
                for ref in signal.get("evidence_refs", []) or []
            ],
            related_case_ids=related_case_ids,
            detector_version=signal.get("detector_version", "v1"),
            # 目标 case 自身也必须建 case link，否则 list_for_case 查不到。
            case_links=[case_id, *related_case_ids],
        )

    return SeededFixture(fixture_id=fixture_id, cases=cases, ref_map=ref_map)


def build_stack(database: Any) -> EvalDataStack:
    """用真实生产组件装配评测栈（不含 agent runtime）。"""
    from app.application.finding_service import FindingService
    from app.application.report_document_service import ReportDocumentService
    from app.application.repositories import ApplicationRepository
    from app.infrastructure.database.cross_investigation_repository import (
        CrossInvestigationRepository,
    )
    from app.infrastructure.database.derived_signal_repository import (
        DerivedSignalRepository,
    )
    from app.infrastructure.database.finding_repository import FindingRepository
    from app.infrastructure.database.knowledge_repository import KnowledgeRepository
    from app.infrastructure.database.report_repository import ReportDocumentRepository
    from app.infrastructure.database.social_repository import SocialRepository
    from app.infrastructure.database.workspace_entity_repository import (
        WorkspaceEntityRepository,
    )

    repository = ApplicationRepository(database)
    return EvalDataStack(
        database=database,
        repository=repository,
        social=SocialRepository(database),
        knowledge=KnowledgeRepository(database),
        finding_service=FindingService(database, repository),
        finding_repository=FindingRepository(database),
        report_service=ReportDocumentService(database),
        report_repository=ReportDocumentRepository(database),
        workspace_repository=WorkspaceEntityRepository(database),
        cross_repository=CrossInvestigationRepository(database),
        signal_repository=DerivedSignalRepository(database),
    )


def iter_investigation_keys(fixture: Mapping[str, Any]) -> Iterable[str]:
    for investigation in fixture.get("investigations", []):
        yield investigation["key"]
