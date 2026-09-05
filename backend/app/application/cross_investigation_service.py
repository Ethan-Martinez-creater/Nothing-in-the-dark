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

import logging
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

logger = logging.getLogger(__name__)

# FC1 safety caps：参与 expected set → reconcile 的输入必须完整扫描；
# 达到 cap 时 scan_complete=False，禁止 reconcile_for_anchor。
MAX_CASE_ENTITY_SCAN = 20_000
MAX_CASE_POST_SCAN = 50_000
MAX_CROSS_MATCH_SCAN = 50_000
MAX_CASE_MEDIA_SCAN = 20_000
MAX_MEDIA_CANDIDATE_SCAN = 50_000

_CASE_ENTITY_PAGE_SIZE = 500
_ANCHOR_PAGE_SIZE = 1000
_CROSS_MATCH_BATCH_SIZE = 500
_CROSS_MATCH_BATCH_LIMIT = 2000


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
        # reconcile 部分 expected set，§C15）。FC1：scan incomplete 时
        # 只 upsert 已计算结果、跳过 reconcile_for_anchor。
        shared_actor_links, shared_actor_complete = (
            await self._detect_shared_actor(case_id)
        )
        summary["shared_actor"] = await self._flush_detector(
            case_id,
            "shared_actor",
            shared_actor_links,
            scan_complete=shared_actor_complete,
        )

        shared_post_links, shared_post_complete = (
            await self._detect_shared_post(case_id)
        )
        shared_post_by_pair = {
            (link["left_case_id"], link["right_case_id"]): link
            for link in shared_post_links
        }
        summary["shared_post"] = await self._flush_detector(
            case_id,
            "shared_post",
            shared_post_links,
            scan_complete=shared_post_complete,
        )

        shared_media_links, shared_media_complete = (
            await self._detect_shared_media(case_id)
        )
        summary["shared_media"] = await self._flush_detector(
            case_id,
            "shared_media",
            shared_media_links,
            scan_complete=shared_media_complete,
        )

        shared_content_links, shared_content_complete = (
            await self._detect_shared_content(case_id, shared_post_by_pair)
        )
        summary["shared_content"] = await self._flush_detector(
            case_id,
            "shared_content",
            shared_content_links,
            scan_complete=shared_content_complete,
        )

        return summary

    async def _flush_detector(
        self,
        case_id: str,
        relation_type: str,
        links: list[dict[str, Any]],
        *,
        scan_complete: bool,
    ) -> dict[str, Any]:
        """upsert expected links；仅在 expected set 完整计算成功后 reconcile。

        FC1：scan_complete=False（safety cap / batch 截断）时只保留本轮
        已计算结果，禁止对 anchor 做 destructive reconcile。返回
        {"upserted": N, "stale_deactivated": M, "scan_complete": bool}。
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
        stale = 0
        if scan_complete:
            stale = await self._cross.reconcile_for_anchor(
                case_id, relation_type, ALGORITHM_VERSION, expected_fingerprints
            )
        else:
            logger.warning(
                "%s detector scan incomplete for case %s; anchor reconcile "
                "skipped (upserted=%s)",
                relation_type,
                case_id,
                len(links),
            )
        return {
            "upserted": len(links),
            "stale_deactivated": stale,
            "scan_complete": scan_complete,
        }

    # ---------------- shared_actor（§36，identity component 单位） ----------

    async def _detect_shared_actor(
        self, case_id: str
    ) -> tuple[list[dict[str, Any]], bool]:
        # FC1：anchor 实体 id keyset 分页（id ASC，page=500，cap 20_000），
        # 不再把 list_entities_for_case(limit=2000) 的截断结果当完整输入。
        entity_ids: list[str] = []
        scan_complete = True
        after_id: str | None = None
        while True:
            page = await self._workspace.list_entity_ids_for_case_page(
                case_id, after_id=after_id, limit=_CASE_ENTITY_PAGE_SIZE
            )
            if not page:
                break
            entity_ids.extend(page)
            if len(entity_ids) >= MAX_CASE_ENTITY_SCAN:
                scan_complete = False
                entity_ids = entity_ids[:MAX_CASE_ENTITY_SCAN]
                logger.warning(
                    "shared_actor anchor entity scan reached safety cap %s "
                    "(case=%s); anchor reconcile will be skipped",
                    MAX_CASE_ENTITY_SCAN,
                    case_id,
                )
                break
            after_id = page[-1]
            if len(page) < _CASE_ENTITY_PAGE_SIZE:
                break
        if not entity_ids:
            return [], scan_complete
        # Rework R3：以 WorkspaceEntityService.identity_component 为唯一
        # identity 传播单位（active same_as 边，500 节点硬保护由 service
        # 保证，本服务不得绕过）；component 内全部实体（含其它 Case 的）
        # 的 Case appearance 必须参与聚合。
        components_by_key: dict[str, list[str]] = {}
        for entity_id in entity_ids:
            component = await self._workspace_service.identity_component(entity_id)
            components_by_key.setdefault(
                component["component_key"], component["entity_ids"]
            )
        all_entity_ids = sorted(
            {eid for ids in components_by_key.values() for eid in ids}
        )
        links: list[Any] = []
        for start in range(0, len(all_entity_ids), _CROSS_MATCH_BATCH_SIZE):
            links.extend(
                await self._workspace.list_case_links_for_entities(
                    all_entity_ids[start : start + _CROSS_MATCH_BATCH_SIZE]
                )
            )
        links_by_entity: dict[str, set[str]] = defaultdict(set)
        for link in links:
            links_by_entity[link.entity_id].add(link.case_id)

        shared_pairs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for component_key, entity_ids in sorted(components_by_key.items()):
            component_cases: set[str] = set()
            for entity_id in entity_ids:
                component_cases |= links_by_entity.get(entity_id, set())
            for other_case in component_cases:
                if other_case == case_id:
                    continue
                left, right = sorted((case_id, other_case))
                shared_pairs[(left, right)].append(
                    {
                        "component_key": component_key,
                        "entity_ids": entity_ids,
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
        return results, scan_complete

    # ---------------- shared_post（§37，exact platform+native_id） ----------

    async def _detect_shared_post(
        self, case_id: str
    ) -> tuple[list[dict[str, Any]], bool]:
        # FC1：anchor posts keyset 分页（SourcePost.id ASC，page=1000，
        # cap 50_000）；cross match 按 500 pairs/批执行，单批结果达到
        # batch limit 视为可能截断 → scan_complete=False（保守，不误删）。
        anchor_pairs: list[tuple[str, str, str]] = []
        scan_complete = True
        after_id: str | None = None
        while True:
            page = await self._social.list_case_native_pairs_page(
                case_id, after_id=after_id, limit=_ANCHOR_PAGE_SIZE
            )
            if not page:
                break
            anchor_pairs.extend(page)
            if len(anchor_pairs) >= MAX_CASE_POST_SCAN:
                scan_complete = False
                anchor_pairs = anchor_pairs[:MAX_CASE_POST_SCAN]
                logger.warning(
                    "shared_post anchor scan reached safety cap %s (case=%s)",
                    MAX_CASE_POST_SCAN,
                    case_id,
                )
                break
            after_id = page[-1][0]
            if len(page) < _ANCHOR_PAGE_SIZE:
                break
        if not anchor_pairs:
            return [], scan_complete
        pairs = [(platform, native_id) for _, platform, native_id in anchor_pairs]
        anchor_by_pair = {
            (platform, native_id): post_id
            for post_id, platform, native_id in anchor_pairs
        }
        matches: list[Any] = []
        for start in range(0, len(pairs), _CROSS_MATCH_BATCH_SIZE):
            batch = pairs[start : start + _CROSS_MATCH_BATCH_SIZE]
            batch_rows = list(
                await self._social.find_cross_case_native_post_matches(
                    case_id, batch, limit=_CROSS_MATCH_BATCH_LIMIT
                )
            )
            if len(batch_rows) >= _CROSS_MATCH_BATCH_LIMIT:
                # 单批命中上限：可能仍有未取回的 match，标 incomplete。
                scan_complete = False
                logger.warning(
                    "shared_post cross match batch hit limit %s (case=%s)",
                    _CROSS_MATCH_BATCH_LIMIT,
                    case_id,
                )
            if len(matches) + len(batch_rows) >= MAX_CROSS_MATCH_SCAN:
                scan_complete = False
                matches.extend(
                    batch_rows[: max(0, MAX_CROSS_MATCH_SCAN - len(matches))]
                )
                logger.warning(
                    "shared_post cross match scan reached safety cap %s "
                    "(case=%s)",
                    MAX_CROSS_MATCH_SCAN,
                    case_id,
                )
                break
            matches.extend(batch_rows)
        shared_pairs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for match in matches:
            left, right = sorted((case_id, match.case_id))
            shared_pairs[(left, right)].append(
                {
                    "platform": match.platform,
                    "native_id": match.native_id,
                    "anchor_post_id": anchor_by_pair.get(
                        (str(match.platform), str(match.native_id)), ""
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
        return results, scan_complete

    # ---------------- shared_media（§38：SHA observed / phash candidate） ----

    async def _detect_shared_media(
        self, case_id: str
    ) -> tuple[list[dict[str, Any]], bool]:
        # Rework R5：同一 Case Pair 先聚合再输出唯一 payload——有 exact SHA
        # 即 observed score=1.0（candidate 只作辅助信息，不降级）；只有
        # phash 才输出 candidate（score=max similarity）。禁止同 Pair 两条。
        # FC1：anchor media keyset 分页（asset id ASC，page=1000，cap
        # 20_000）；exact SHA match 按 500 SHA/批执行，批结果达到 limit
        # 视为可能截断 → scan_complete=False（保守）。
        anchor_assets: list[tuple[str, str, str]] = []
        scan_complete = True
        after_asset_id: str | None = None
        while True:
            page = await self._media.list_case_media_hashes_page(
                case_id, after_id=after_asset_id, limit=_ANCHOR_PAGE_SIZE
            )
            if not page:
                break
            anchor_assets.extend(page)
            if len(anchor_assets) >= MAX_CASE_MEDIA_SCAN:
                scan_complete = False
                anchor_assets = anchor_assets[:MAX_CASE_MEDIA_SCAN]
                logger.warning(
                    "shared_media anchor scan reached safety cap %s (case=%s)",
                    MAX_CASE_MEDIA_SCAN,
                    case_id,
                )
                break
            after_asset_id = page[-1][0]
            if len(page) < _ANCHOR_PAGE_SIZE:
                break
        observed_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        sha_values = [sha for _, sha, _ in anchor_assets]
        if sha_values:
            matches: list[Any] = []
            for start in range(0, len(sha_values), _CROSS_MATCH_BATCH_SIZE):
                batch = sha_values[start : start + _CROSS_MATCH_BATCH_SIZE]
                batch_rows = list(
                    await self._media.find_cross_case_sha_matches(
                        case_id, batch, limit=_CROSS_MATCH_BATCH_LIMIT
                    )
                )
                if len(batch_rows) >= _CROSS_MATCH_BATCH_LIMIT:
                    scan_complete = False
                    logger.warning(
                        "shared_media sha match batch hit limit %s (case=%s)",
                        _CROSS_MATCH_BATCH_LIMIT,
                        case_id,
                    )
                if len(matches) + len(batch_rows) >= MAX_CROSS_MATCH_SCAN:
                    scan_complete = False
                    matches.extend(
                        batch_rows[: max(0, MAX_CROSS_MATCH_SCAN - len(matches))]
                    )
                    logger.warning(
                        "shared_media cross match scan reached safety cap %s "
                        "(case=%s)",
                        MAX_CROSS_MATCH_SCAN,
                        case_id,
                    )
                    break
                matches.extend(batch_rows)
            sha_to_anchor: dict[str, list[str]] = defaultdict(list)
            for asset_id, sha, _ in anchor_assets:
                sha_to_anchor[sha].append(asset_id)
            shared_pairs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(
                list
            )
            seen_matches: set[tuple[str, str]] = set()
            for match in matches:
                left, right = sorted((case_id, match.case_id))
                # exact distinct media match：同 (SHA, other asset) 只计一次
                dedupe_key = (str(match.actual_sha256), str(match.id))
                if dedupe_key in seen_matches:
                    continue
                seen_matches.add(dedupe_key)
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
                observed_by_pair[(left, right)] = {
                    "left_case_id": left,
                    "right_case_id": right,
                    "status": "observed",
                    "score": 1.0,
                    "evidence_count": len(evidence),
                    "evidence_refs": evidence[:_MAX_EVIDENCE_REFS],
                    "feature_scores": {"sha256_exact": 1.0},
                }

        # phash candidate：复用四段 blocking + POSSIBLE_THRESHOLD（§38），
        # 不把 phash candidate 升级 observed。
        candidate_support: dict[tuple[str, str], float] = {}
        candidate_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        phash_assets = await self._load_anchor_phash_assets(case_id)
        if phash_assets:
            block_keys = set()
            for asset in phash_assets:
                for offset in (0, 4, 8, 12):
                    block_keys.add(
                        f"{asset.media_type}:{offset}:"
                        f"{asset.phash[offset : offset + 4]}"
                    )
            candidates = list(
                await self._media.find_cross_case_phash_candidates(
                    case_id,
                    sorted(block_keys),
                    limit=_CROSS_MATCH_BATCH_LIMIT,
                )
            )
            if len(candidates) >= _CROSS_MATCH_BATCH_LIMIT:
                # 候选集达到上限：可能仍有未取回候选 → 保守标 incomplete。
                scan_complete = False
                logger.warning(
                    "shared_media phash candidates hit limit %s (case=%s)",
                    _CROSS_MATCH_BATCH_LIMIT,
                    case_id,
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
                        candidate_support[(left, right)] = max(
                            candidate_support.get((left, right), 0.0), score
                        )
                        if (left, right) in observed_by_pair:
                            # Rework R5：observed 已覆盖该 pair，phash 只作
                            # 辅助信息，不得降级或产生第二条 payload。
                            continue
                        match_ref = {
                            "anchor_asset_id": asset.id,
                            "other_asset_id": other.id,
                            "phash": asset.phash,
                        }
                        existing = candidate_by_pair.get((left, right))
                        if existing is None or score > existing["score"]:
                            candidate_by_pair[(left, right)] = {
                                "left_case_id": left,
                                "right_case_id": right,
                                "status": "candidate",
                                "score": score,
                                "evidence_refs": [match_ref],
                                "feature_scores": {
                                    "phash_candidate": score,
                                    "threshold": algo.POSSIBLE_THRESHOLD,
                                },
                            }
                        else:
                            existing["evidence_refs"].append(match_ref)
                        break  # 每 anchor asset 命中一个候选即足够聚合
        for pair, support in candidate_support.items():
            if pair in observed_by_pair:
                observed_by_pair[pair]["feature_scores"]["phash_candidate"] = support
        results: list[dict[str, Any]] = []
        for payload in observed_by_pair.values():
            results.append(payload)
        for payload in candidate_by_pair.values():
            distinct_matches = {
                (
                    ref.get("anchor_asset_id", ""),
                    ref.get("other_asset_id", ""),
                )
                for ref in payload["evidence_refs"]
            }
            payload["evidence_count"] = len(distinct_matches)
            payload["evidence_refs"] = payload["evidence_refs"][:_MAX_EVIDENCE_REFS]
            results.append(payload)
        results.sort(key=lambda item: (item["left_case_id"], item["right_case_id"]))
        return results, scan_complete

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
    ) -> tuple[list[dict[str, Any]], bool]:
        # FC1：anchor hashes keyset 分页（SourcePost.id ASC，page=1000，
        # cap 50_000）；cross match 按 500 hashes/批，批结果达到 limit
        # 视为可能截断 → scan_complete=False。
        anchor_hashes: list[tuple[str, str]] = []
        scan_complete = True
        after_id: str | None = None
        while True:
            page = await self._social.list_case_post_content_hashes_page(
                case_id, after_id=after_id, limit=_ANCHOR_PAGE_SIZE
            )
            if not page:
                break
            anchor_hashes.extend(page)
            if len(anchor_hashes) >= MAX_CASE_POST_SCAN:
                scan_complete = False
                anchor_hashes = anchor_hashes[:MAX_CASE_POST_SCAN]
                logger.warning(
                    "shared_content anchor scan reached safety cap %s (case=%s)",
                    MAX_CASE_POST_SCAN,
                    case_id,
                )
                break
            after_id = page[-1][0]
            if len(page) < _ANCHOR_PAGE_SIZE:
                break
        if not anchor_hashes:
            return [], scan_complete
        # §39：如果 pair 已满足 shared_post，则不计入 shared_content evidence
        excluded_post_ids = set()
        for link in shared_post_by_pair.values():
            for evidence in link.get("evidence_refs", []):
                excluded_post_ids.add(evidence.get("other_post_id") or "")
        hash_to_post = {
            content_hash: post_id for post_id, content_hash in anchor_hashes
        }
        hashes = list(hash_to_post)
        matches: list[Any] = []
        for start in range(0, len(hashes), _CROSS_MATCH_BATCH_SIZE):
            batch = hashes[start : start + _CROSS_MATCH_BATCH_SIZE]
            batch_rows = list(
                await self._social.find_cross_case_content_hash_matches(
                    case_id, batch, limit=_CROSS_MATCH_BATCH_LIMIT
                )
            )
            if len(batch_rows) >= _CROSS_MATCH_BATCH_LIMIT:
                scan_complete = False
                logger.warning(
                    "shared_content cross match batch hit limit %s (case=%s)",
                    _CROSS_MATCH_BATCH_LIMIT,
                    case_id,
                )
            if len(matches) + len(batch_rows) >= MAX_CROSS_MATCH_SCAN:
                scan_complete = False
                matches.extend(
                    batch_rows[: max(0, MAX_CROSS_MATCH_SCAN - len(matches))]
                )
                logger.warning(
                    "shared_content cross match scan reached safety cap %s "
                    "(case=%s)",
                    MAX_CROSS_MATCH_SCAN,
                    case_id,
                )
                break
            matches.extend(batch_rows)
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
        return results, scan_complete

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
            # Rework R9：每 Pair + relation_type 只有一条聚合 Link，共享对象
            # 数量 = evidence_count 之和，不是 Link row 数。
            counts = {
                relation_type: sum(
                    int(link.evidence_count or 0)
                    for link in case_links
                    if link.relation_type == relation_type
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
