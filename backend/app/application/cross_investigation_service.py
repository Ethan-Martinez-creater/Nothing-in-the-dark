"""V3 Part C: Cross-Investigation Intelligence（CrossInvestigationService）。

4 个固定 detector（§35）：shared_actor / shared_post / shared_media /
shared_content。每个 detector 先计算完整 expected set，再 upsert，最后
reconcile stale（§35 禁止"发现一个写一个、异常后立即清理旧 relation"）。

禁止 O(N²)（§40）：全部依赖 identity component / platform+native_id /
content_hash / media SHA / phash blocking 生成候选。

shared_content 固定使用 SourcePost.content_hash 的 exact raw-content
复用（§39）；同一原始 Post 不重复计入 shared_content（§39 防双计）。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select

from app.application.repositories import ApplicationRepository
from app.application.workspace_entity_service import WorkspaceEntityService
from app.core import v3
from app.core.errors import ResourceNotFoundError
from app.infrastructure.database.cross_investigation_repository import (
    CrossInvestigationRepository,
    cross_link_fingerprint,
)
from app.infrastructure.database.engine import Database
from app.infrastructure.database.media_pipeline_repository import (
    MediaPipelineRepository,
)
from app.infrastructure.database.models import MediaAssetRecord
from app.infrastructure.database.social_repository import SocialRepository
from app.infrastructure.database.workspace_entity_repository import (
    WorkspaceEntityRepository,
)
from app.services import alignment as algo

ALGORITHM_VERSION = v3.CROSS_INTELLIGENCE_VERSION
_MAX_EVIDENCE_REFS = v3.MAX_LINK_EVIDENCE_REFS


class CrossInvestigationService:
    def __init__(
        self,
        *,
        cross_repository: CrossInvestigationRepository,
        workspace_repository: WorkspaceEntityRepository,
        workspace_service: WorkspaceEntityService,
        social_repository: SocialRepository,
        media_repository: MediaPipelineRepository,
        application_repository: ApplicationRepository,
        database: Database,
    ) -> None:
        self._cross = cross_repository
        self._workspace = workspace_repository
        self._workspace_service = workspace_service
        self._social = social_repository
        self._media = media_repository
        self._application = application_repository
        self._database = database

    # ---------------- refresh ----------------

    async def refresh_case(self, case_id: str) -> dict[str, Any]:
        case = await self._application.get_case(case_id)
        if case is None:
            raise ResourceNotFoundError("case", case_id)
        summary: dict[str, Any] = {"case_id": case_id}

        # 每个 detector：expected set → upsert → reconcile（异常时不得
        # reconcile 部分 expected set，§C15）
        shared_actor = await self._detect_shared_actor(case_id)
        summary["shared_actor"] = await self._flush_detector(
            case_id, "shared_actor", shared_actor
        )

        shared_post = await self._detect_shared_post(case_id)
        shared_post_by_pair = {
            (link["left_case_id"], link["right_case_id"]): link
            for link in shared_post
        }
        summary["shared_post"] = await self._flush_detector(
            case_id, "shared_post", shared_post
        )

        shared_media = await self._detect_shared_media(case_id)
        summary["shared_media"] = await self._flush_detector(
            case_id, "shared_media", shared_media
        )

        shared_content = await self._detect_shared_content(
            case_id, shared_post_by_pair
        )
        summary["shared_content"] = await self._flush_detector(
            case_id, "shared_content", shared_content
        )

        return summary

    async def _flush_detector(
        self,
        case_id: str,
        relation_type: str,
        links: list[dict[str, Any]],
    ) -> dict[str, int]:
        """upsert expected links；仅在 expected set 完整计算成功后 reconcile。

        返回 {"upserted": N, "stale_deactivated": M}。
        """
        expected_fingerprints: set[str] = set()
        for link in links:
            fingerprint = cross_link_fingerprint(
                left_case_id=link["left_case_id"],
                right_case_id=link["right_case_id"],
                relation_type=relation_type,
                algorithm_version=ALGORITHM_VERSION,
            )
            expected_fingerprints.add(fingerprint)
            await self._cross.upsert_link(
                left_case_id=link["left_case_id"],
                right_case_id=link["right_case_id"],
                relation_type=relation_type,
                status=link["status"],
                score=link["score"],
                evidence_count=link["evidence_count"],
                evidence_refs=link["evidence_refs"],
                feature_scores=link.get("feature_scores", {}),
                algorithm_version=ALGORITHM_VERSION,
                max_evidence_refs=_MAX_EVIDENCE_REFS,
            )
        stale = await self._cross.reconcile_for_anchor(
            case_id, relation_type, ALGORITHM_VERSION, expected_fingerprints
        )
        return {"upserted": len(links), "stale_deactivated": stale}

    # ---------------- shared_actor（§36，identity component 单位） ----------

    async def _detect_shared_actor(
        self, case_id: str
    ) -> list[dict[str, Any]]:
        entities = await self._workspace.list_entities_for_case(case_id)
        if not entities:
            return []
        entity_ids = [entity.id for entity in entities]
        relations = await self._workspace.list_active_relations_for_entities(
            entity_ids
        )
        # 构造 identity components（BFS，bounded 由 entity service 保证）
        adjacency: dict[str, set[str]] = defaultdict(set)
        for relation in relations:
            adjacency[relation.left_entity_id].add(relation.right_entity_id)
            adjacency[relation.right_entity_id].add(relation.left_entity_id)
        visited: set[str] = set()
        components: list[list[str]] = []
        for entity_id in entity_ids:
            if entity_id in visited:
                continue
            stack = [entity_id]
            component: list[str] = []
            visited.add(entity_id)
            while stack:
                current = stack.pop()
                component.append(current)
                for neighbor in adjacency.get(current, ()):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)
            components.append(sorted(component))
        # 单节点 component 也是 identity unit
        links = await self._workspace.list_case_links_for_entities(entity_ids)
        links_by_entity: dict[str, set[str]] = defaultdict(set)
        for link in links:
            links_by_entity[link.entity_id].add(link.case_id)

        shared_pairs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for component in components:
            component_key = component[0]
            component_cases: set[str] = set()
            for entity_id in component:
                component_cases |= links_by_entity.get(entity_id, set())
            for other_case in component_cases:
                if other_case == case_id:
                    continue
                left, right = sorted((case_id, other_case))
                shared_pairs[(left, right)].append(
                    {
                        "component_key": component_key,
                        "entity_ids": component,
                    }
                )
        results: list[dict[str, Any]] = []
        for (left, right), components_shared in sorted(shared_pairs.items()):
            results.append(
                {
                    "left_case_id": left,
                    "right_case_id": right,
                    "status": "observed",
                    "score": 1.0,
                    "evidence_count": len(components_shared),
                    "evidence_refs": components_shared[:_MAX_EVIDENCE_REFS],
                    "feature_scores": {"identity_component": 1.0},
                }
            )
        return results

    # ---------------- shared_post（§37，exact platform+native_id） ----------

    async def _detect_shared_post(self, case_id: str) -> list[dict[str, Any]]:
        native_pairs = await self._social.list_case_native_pairs(case_id)
        if not native_pairs:
            return []
        pairs = [(platform, native_id) for _, platform, native_id in native_pairs]
        matches = await self._social.find_cross_case_native_post_matches(
            case_id, pairs
        )
        shared_pairs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for match in matches:
            left, right = sorted((case_id, match.case_id))
            shared_pairs[(left, right)].append(
                {
                    "platform": match.platform,
                    "native_id": match.native_id,
                    "anchor_post_id": next(
                        post_id
                        for post_id, platform, native_id in native_pairs
                        if platform == match.platform
                        and native_id == match.native_id
                    ),
                    "other_post_id": match.id,
                }
            )
        results: list[dict[str, Any]] = []
        for (left, right), evidence in sorted(shared_pairs.items()):
            results.append(
                {
                    "left_case_id": left,
                    "right_case_id": right,
                    "status": "observed",
                    "score": 1.0,
                    "evidence_count": len(evidence),
                    "evidence_refs": evidence[:_MAX_EVIDENCE_REFS],
                    "feature_scores": {"platform_native_exact": 1.0},
                }
            )
        return results

    # ---------------- shared_media（§38：SHA observed / phash candidate） ----

    async def _detect_shared_media(self, case_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        anchor_assets = await self._media.list_case_media_hashes(case_id)
        sha_values = [sha for _, sha, _ in anchor_assets]
        if sha_values:
            matches = await self._media.find_cross_case_sha_matches(
                case_id, sha_values
            )
            sha_to_anchor: dict[str, list[str]] = defaultdict(list)
            for asset_id, sha, _ in anchor_assets:
                sha_to_anchor[sha].append(asset_id)
            shared_pairs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(
                list
            )
            for match in matches:
                left, right = sorted((case_id, match.case_id))
                shared_pairs[(left, right)].append(
                    {
                        "actual_sha256": match.actual_sha256,
                        "anchor_asset_ids": sha_to_anchor.get(
                            str(match.actual_sha256), []
                        ),
                        "other_asset_id": match.id,
                        "media_type": match.media_type,
                    }
                )
            for (left, right), evidence in sorted(shared_pairs.items()):
                results.append(
                    {
                        "left_case_id": left,
                        "right_case_id": right,
                        "status": "observed",
                        "score": 1.0,
                        "evidence_count": len(evidence),
                        "evidence_refs": evidence[:_MAX_EVIDENCE_REFS],
                        "feature_scores": {"sha256_exact": 1.0},
                    }
                )

        # phash candidate：复用四段 blocking + POSSIBLE_THRESHOLD（§38），
        # 不把 phash candidate 升级 observed。
        phash_assets = await self._load_anchor_phash_assets(case_id)
        if phash_assets:
            block_keys = set()
            for asset in phash_assets:
                for offset in (0, 4, 8, 12):
                    block_keys.add(
                        f"{asset.media_type}:{offset}:"
                        f"{asset.phash[offset : offset + 4]}"
                    )
            candidates = await self._media.find_cross_case_phash_candidates(
                case_id, sorted(block_keys)
            )
            candidates_by_block: dict[str, list[MediaAssetRecord]] = defaultdict(
                list
            )
            for asset in candidates:
                for offset in (0, 4, 8, 12):
                    if not asset.phash or len(asset.phash) <= offset + 4:
                        continue
                    key = (
                        f"{asset.media_type}:{offset}:"
                        f"{asset.phash[offset : offset + 4]}"
                    )
                    if key in block_keys:
                        candidates_by_block[key].append(asset)
            seen_pairs: set[tuple[str, str]] = set()
            for asset in phash_assets:
                for offset in (0, 4, 8, 12):
                    key = (
                        f"{asset.media_type}:{offset}:"
                        f"{asset.phash[offset : offset + 4]}"
                    )
                    for other in candidates_by_block.get(key, ()):
                        pair_key = (asset.id, other.id)
                        if pair_key in seen_pairs:
                            continue
                        seen_pairs.add(pair_key)
                        result = algo.content_alignment(
                            {
                                "phash": asset.phash,
                                "content": asset.ocr_text or asset.url,
                            },
                            {
                                "phash": other.phash,
                                "content": other.ocr_text or other.url,
                            },
                        )
                        # §38：sha 未知时 content_alignment 总分被缺失特征
                        # 稀释（上限 0.5 < POSSIBLE_THRESHOLD），phash-only
                        # 判定复用算法自身 phash_match 相似度对照同一
                        # POSSIBLE_THRESHOLD（不新造 threshold）。
                        score = float(result["features"].get("phash_match") or 0.0)
                        if score < algo.POSSIBLE_THRESHOLD:
                            continue
                        left, right = sorted((case_id, other.case_id))
                        results.append(
                            {
                                "left_case_id": left,
                                "right_case_id": right,
                                "status": "candidate",
                                "score": score,
                                "evidence_count": 1,
                                "evidence_refs": [
                                    {
                                        "anchor_asset_id": asset.id,
                                        "other_asset_id": other.id,
                                        "phash": asset.phash,
                                    }
                                ][:_MAX_EVIDENCE_REFS],
                                "feature_scores": {
                                    "phash_candidate": score,
                                    "threshold": algo.POSSIBLE_THRESHOLD,
                                },
                            }
                        )
                        break  # 每 anchor asset 命中一个候选即足够聚合
        return results

    async def _load_anchor_phash_assets(self, case_id: str) -> list[Any]:
        async with self._database.session_factory() as session:
            result = await session.scalars(
                select(MediaAssetRecord).where(
                    MediaAssetRecord.case_id == case_id,
                    MediaAssetRecord.phash.isnot(None),
                    MediaAssetRecord.phash != "",
                )
            )
            return list(result.all())

    # ---------------- shared_content（§39，exact raw content_hash） ----------

    async def _detect_shared_content(
        self,
        case_id: str,
        shared_post_by_pair: dict[tuple[str, str], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        anchor_hashes = await self._social.list_case_post_content_hashes(case_id)
        if not anchor_hashes:
            return []
        # §39：如果 pair 已满足 shared_post，则不计入 shared_content evidence
        excluded_post_ids = set()
        for link in shared_post_by_pair.values():
            for evidence in link.get("evidence_refs", []):
                excluded_post_ids.add(evidence.get("other_post_id") or "")
        hash_to_post = {
            content_hash: post_id for post_id, content_hash in anchor_hashes
        }
        matches = await self._social.find_cross_case_content_hash_matches(
            case_id, list(hash_to_post)
        )
        shared_pairs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for match in matches:
            if match.id in excluded_post_ids:
                continue
            left, right = sorted((case_id, match.case_id))
            shared_pairs[(left, right)].append(
                {
                    "content_hash": match.content_hash,
                    "anchor_post_id": hash_to_post.get(str(match.content_hash)),
                    "other_post_id": match.id,
                }
            )
        results: list[dict[str, Any]] = []
        for (left, right), evidence in sorted(shared_pairs.items()):
            results.append(
                {
                    "left_case_id": left,
                    "right_case_id": right,
                    "status": "observed",
                    "score": 1.0,
                    "evidence_count": len(evidence),
                    "evidence_refs": evidence[:_MAX_EVIDENCE_REFS],
                    "feature_scores": {"content_hash_exact": 1.0},
                }
            )
        return results

    # ---------------- queries（§42/§43） ----------------

    async def related_investigations(
        self, case_id: str, *, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Related Investigation DTO（§42）：API 层组合，无需 summary table。"""
        await self._application.get_case(case_id)
        links = await self._cross.list_for_case(case_id, active_only=True)
        by_other: dict[str, list[Any]] = defaultdict(list)
        for link in links:
            other = (
                link.right_case_id
                if link.left_case_id == case_id
                else link.left_case_id
            )
            by_other[other].append(link)
        results: list[dict[str, Any]] = []
        for other, case_links in by_other.items():
            other_case = await self._application.get_case(other)
            relation_types = sorted({link.relation_type for link in case_links})
            counts = {
                relation_type: sum(
                    1 for link in case_links if link.relation_type == relation_type
                )
                for relation_type in relation_types
            }
            results.append(
                {
                    "case_id": other,
                    "title": other_case.title if other_case else other,
                    "updated_at": other_case.updated_at if other_case else None,
                    "relation_types": relation_types,
                    "relation_count": len(relation_types),
                    "max_score": max(
                        (float(link.score or 0) for link in case_links), default=0.0
                    ),
                    "shared_actor_count": counts.get("shared_actor", 0),
                    "shared_post_count": counts.get("shared_post", 0),
                    "shared_media_count": counts.get("shared_media", 0),
                    "shared_content_count": counts.get("shared_content", 0),
                    "has_candidate_relation": any(
                        link.status == "candidate" for link in case_links
                    ),
                }
            )
        # §43.1 排序：relation type count DESC → max_score DESC → updated_at DESC
        results.sort(
            key=lambda item: (
                -item["relation_count"],
                -item["max_score"],
                -(item["updated_at"].timestamp() if item["updated_at"] else 0),
            )
        )
        return results[:limit]

    async def list_between(
        self, left_case_id: str, right_case_id: str
    ) -> list[Any]:
        """§43：两个 Case 之间的 active links（含 evidence 详情）。"""
        links = await self._cross.list_between(left_case_id, right_case_id)
        result: list[Any] = []
        for link in links:
            result.append(link)
        return result

    async def workspace_connections(
        self,
        *,
        relation_type: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        links = await self._cross.list_workspace(
            relation_type=relation_type,
            status=status,
            active_only=True,
            limit=min(limit, v3.MAX_INTELLIGENCE_CONNECTIONS),
        )
        case_ids = sorted(
            {link.left_case_id for link in links}
            | {link.right_case_id for link in links}
        )
        titles: dict[str, str] = {}
        for case_id in case_ids:
            case = await self._application.get_case(case_id)
            titles[case_id] = case.title if case else case_id
        return [
            {
                "id": link.id,
                "left_case_id": link.left_case_id,
                "right_case_id": link.right_case_id,
                "left_title": titles.get(link.left_case_id, link.left_case_id),
                "right_title": titles.get(link.right_case_id, link.right_case_id),
                "relation_type": link.relation_type,
                "status": link.status,
                "score": link.score,
                "evidence_count": link.evidence_count,
                "algorithm_version": link.algorithm_version,
            }
            for link in links
        ]


def case_order(case_id: str) -> int:
    """稳定伪序：保持 case_id 字典序作为第三排序键的确定性代理。"""
    return int.from_bytes(case_id.encode("utf-8")[:8], "big") if case_id else 0
