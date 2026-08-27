"""Cross-platform account, media and post-content alignment service."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from app.infrastructure.database.alignment_repository import AlignmentRepository
from app.infrastructure.database.media_pipeline_repository import MediaPipelineRepository
from app.services import alignment as algo


class AlignmentService:
    """Generate sparse cross-platform candidates and materialize reviewed links."""

    def __init__(
        self,
        repository: AlignmentRepository,
        media_repository: MediaPipelineRepository,
        app_repository: Any,
        social_repository: Any | None = None,
    ) -> None:
        self._repository = repository
        self._media = media_repository
        self._app = app_repository
        self._social = social_repository

    async def analyze_case(self, case_id: str) -> dict[str, int]:
        media_count = await self._align_media(case_id)
        account_count = await self._align_accounts(case_id)
        post_count = await self._align_posts(case_id)
        return {
            "content_candidates": media_count + post_count,
            "media_candidates": media_count,
            "post_candidates": post_count,
            "account_candidates": account_count,
            "probable_publication_enabled": 0,
        }

    async def materialize_candidate(self, case_id: str, candidate_id: str) -> Any:
        candidate = await self._repository.get_candidate(candidate_id)
        if candidate.case_id != case_id:
            raise ValueError("alignment candidate does not belong to case")
        if candidate.decision != "confirmed" or candidate.relation_type != "same_as":
            return None
        if candidate.left_type == "account" and candidate.right_type == "account":
            return await self._materialize_account(case_id, candidate)
        if self._is_content_type(candidate.left_type) and self._is_content_type(
            candidate.right_type
        ):
            return await self._materialize_content(case_id, candidate)
        return None

    async def retract_candidate(self, case_id: str, candidate_id: str) -> None:
        candidate = await self._repository.get_candidate(candidate_id)
        if candidate.case_id != case_id:
            raise ValueError("alignment candidate does not belong to case")
        source = f"candidate:{candidate.id}"
        await self._repository.retract_candidate_materialization(source)

    @staticmethod
    def _is_content_type(value: str) -> bool:
        return value == "post" or value.startswith("media:")

    async def _materialize_account(self, case_id: str, candidate: Any) -> Any:
        left_entity = await self._repository.find_entity_for_object(
            case_id, "account", candidate.left_id
        )
        right_entity = await self._repository.find_entity_for_object(
            case_id, "account", candidate.right_id
        )
        entity = left_entity or right_entity
        if entity is None:
            accounts = await self._app.list_accounts(case_id=case_id, limit=10000)
            by_id = {account.id: account for account in accounts}
            left = by_id.get(candidate.left_id)
            canonical_name = (left.name if left else candidate.left_id) or candidate.left_id
            entity = await self._repository.upsert_canonical_entity(
                case_id=case_id,
                entity_type="account",
                canonical_name=canonical_name,
                aliases=[candidate.left_id, candidate.right_id],
                created_by="alignment",
            )
        elif (
            left_entity is not None
            and right_entity is not None
            and left_entity.id != right_entity.id
        ):
            await self._repository.merge_entities(left_entity.id, right_entity.id)
            entity = left_entity
        source = f"candidate:{candidate.id}"
        for account_id in (candidate.left_id, candidate.right_id):
            await self._repository.create_entity_mention(
                case_id=case_id,
                entity_id=entity.id,
                platform_object_type="account",
                platform_object_id=account_id,
                confidence=1.0,
                method=source,
            )
        await self._repository.mark_entity_confirmed(entity.id)
        return entity

    async def _materialize_content(self, case_id: str, candidate: Any) -> Any:
        left_family = await self._repository.find_family_for_member(
            case_id, candidate.left_type, candidate.left_id
        )
        right_family = await self._repository.find_family_for_member(
            case_id, candidate.right_type, candidate.right_id
        )
        family = left_family or right_family
        if family is None:
            family = await self._repository.create_content_family(
                case_id=case_id,
                label=f"内容族 {candidate.left_id[:8]}",
                earliest_known_id=candidate.left_id,
            )
        elif (
            left_family is not None
            and right_family is not None
            and left_family.id != right_family.id
        ):
            await self._repository.merge_families(left_family.id, right_family.id)
            family = left_family
        source = f"candidate:{candidate.id}"
        await self._repository.add_family_member(
            family_id=family.id,
            member_type=candidate.left_type,
            member_id=candidate.left_id,
            relation="same_content",
            decision_source=source,
        )
        await self._repository.add_family_member(
            family_id=family.id,
            member_type=candidate.right_type,
            member_id=candidate.right_id,
            relation="same_content",
            decision_source=source,
        )
        return family

    async def _align_media(self, case_id: str) -> int:
        assets = await self._media.list_assets_by_case(case_id, limit=10000)
        created = 0
        by_sha: dict[str, list[Any]] = defaultdict(list)
        by_phash: dict[str, list[Any]] = defaultdict(list)
        for asset in assets:
            if asset.actual_sha256:
                by_sha[asset.actual_sha256].append(asset)
            elif asset.phash:
                # Four independent prefixes are cheap LSH-style recall keys.
                for offset in (0, 4, 8, 12):
                    by_phash[
                        f"{asset.media_type}:{offset}:{asset.phash[offset : offset + 4]}"
                    ].append(asset)

        seen: set[tuple[str, str]] = set()
        for group in by_sha.values():
            for left, right in self._cross_platform_pairs(group, top_k=50):
                pair = tuple(sorted((left.id, right.id)))
                if pair in seen:
                    continue
                seen.add(pair)
                candidate = await self._repository.create_alignment_candidate(
                    case_id=case_id,
                    left_type=f"media:{left.media_type}",
                    left_id=left.id,
                    right_type=f"media:{right.media_type}",
                    right_id=right.id,
                    relation_type="same_as",
                    feature_scores={"sha256_match": 1.0, "blocking": ["sha256"]},
                    combined_score=1.0,
                    decision="confirmed",
                    model_version="media-blocking-1.1.0",
                )
                if candidate is not None:
                    created += 1
                    await self.materialize_candidate(case_id, candidate.id)

        for block_key, group in by_phash.items():
            for left, right in self._cross_platform_pairs(group, top_k=20):
                pair = tuple(sorted((left.id, right.id)))
                if pair in seen:
                    continue
                seen.add(pair)
                result = algo.content_alignment(
                    {"phash": left.phash, "content": left.ocr_text or left.url},
                    {"phash": right.phash, "content": right.ocr_text or right.url},
                )
                decision, score = self._gated_decision(result)
                if decision == "pending":
                    continue
                features = {
                    **result["features"],
                    "blocking": [block_key],
                    "publication_gate": "closed_uncalibrated",
                }
                candidate = await self._repository.create_alignment_candidate(
                    case_id=case_id,
                    left_type=f"media:{left.media_type}",
                    left_id=left.id,
                    right_type=f"media:{right.media_type}",
                    right_id=right.id,
                    relation_type="same_as",
                    feature_scores=features,
                    combined_score=score,
                    decision=decision,
                    model_version="media-blocking-1.1.0",
                )
                created += int(candidate is not None)
        return created

    async def _align_accounts(self, case_id: str) -> int:
        accounts = await self._app.list_accounts(case_id=case_id, limit=10000)
        blocks: dict[str, list[Any]] = defaultdict(list)
        for account in accounts:
            normalized = algo.normalize_name(account.name)
            if not normalized:
                continue
            # Full normalized name is precise; two-character prefix preserves typo recall.
            blocks[f"name:{normalized}"].append(account)
            blocks[f"prefix:{normalized[:2]}"].append(account)
        created = 0
        seen: set[tuple[str, str]] = set()
        for block_key, group in blocks.items():
            for left, right in self._cross_platform_pairs(group, top_k=20):
                pair = tuple(sorted((left.id, right.id)))
                if pair in seen:
                    continue
                seen.add(pair)
                result = algo.account_alignment(
                    {
                        "name": left.name,
                        "verified": left.verified,
                        "phash": (left.metadata_json or {}).get("avatar_phash"),
                    },
                    {
                        "name": right.name,
                        "verified": right.verified,
                        "phash": (right.metadata_json or {}).get("avatar_phash"),
                    },
                )
                decision, score = self._gated_decision(result)
                if decision == "pending":
                    continue
                candidate = await self._repository.create_alignment_candidate(
                    case_id=case_id,
                    left_type="account",
                    left_id=left.id,
                    right_type="account",
                    right_id=right.id,
                    relation_type="same_as",
                    feature_scores={
                        **result["features"],
                        "blocking": [block_key],
                        "publication_gate": "closed_uncalibrated",
                    },
                    combined_score=score,
                    decision=decision,
                    model_version="account-blocking-1.1.0",
                )
                created += int(candidate is not None)
        return created

    async def _align_posts(self, case_id: str) -> int:
        if self._social is None:
            return 0
        posts = await self._social.list_posts_by_case(case_id)
        exact_blocks: dict[str, list[Any]] = defaultdict(list)
        lsh_blocks: dict[str, list[Any]] = defaultdict(list)
        signatures: dict[str, list[int]] = {}
        for post in posts:
            normalized = algo.normalize_text(post.content or "")
            if len(normalized) < 8:
                continue
            exact_blocks[hashlib.sha256(normalized.encode()).hexdigest()].append(post)
            signature = algo.minhash_signature(normalized, k=32)
            signatures[post.id] = signature
            for band in range(8):
                start = band * 4
                key = hashlib.sha256(str(signature[start : start + 4]).encode()).hexdigest()[:12]
                lsh_blocks[f"{band}:{key}"].append(post)
        created = 0
        seen: set[tuple[str, str]] = set()
        for block_type, blocks in (("exact_text", exact_blocks), ("minhash_lsh", lsh_blocks)):
            for block_key, group in blocks.items():
                for left, right in self._cross_platform_pairs(group, top_k=30):
                    pair = tuple(sorted((left.id, right.id)))
                    if pair in seen:
                        continue
                    seen.add(pair)
                    result = algo.content_alignment(
                        {"content": left.content}, {"content": right.content}
                    )
                    score = result["score"]
                    if block_type == "exact_text":
                        score = max(score, 1.0)
                    decision = "possible" if score >= algo.POSSIBLE_THRESHOLD else "pending"
                    if decision == "pending":
                        continue
                    candidate = await self._repository.create_alignment_candidate(
                        case_id=case_id,
                        left_type="post",
                        left_id=left.id,
                        right_type="post",
                        right_id=right.id,
                        relation_type="same_as",
                        feature_scores={
                            **result["features"],
                            "blocking": [f"{block_type}:{block_key}"],
                            "publication_gate": "closed_uncalibrated",
                        },
                        combined_score=score,
                        decision=decision,
                        model_version="post-minhash-1.1.0",
                    )
                    created += int(candidate is not None)
        return created

    @staticmethod
    def _gated_decision(result: dict[str, Any]) -> tuple[str, float]:
        decision, score = algo.decide_relation(
            relation_type="same_as",
            score=float(result["score"]),
            features=result["features"],
        )
        # Until the delegated gold-set calibration is approved, non-deterministic
        # probable results remain review-only possible candidates.
        if decision == "probable":
            decision = "possible"
        return decision, score

    @staticmethod
    def _cross_platform_pairs(group: list[Any], *, top_k: int) -> list[tuple[Any, Any]]:
        by_platform: dict[str, list[Any]] = defaultdict(list)
        for item in group:
            by_platform[str(item.platform)].append(item)
        pairs: list[tuple[Any, Any]] = []
        platforms = sorted(by_platform)
        for index, left_platform in enumerate(platforms):
            for right_platform in platforms[index + 1 :]:
                for left in by_platform[left_platform][:top_k]:
                    for right in by_platform[right_platform][:top_k]:
                        pairs.append((left, right))
        return pairs
