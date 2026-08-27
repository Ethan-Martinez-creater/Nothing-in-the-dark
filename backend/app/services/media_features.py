"""Pixel average-hash, caption OCR and video keyframe helpers.

No PIL / numpy / tesseract dependency: average-hash runs on an 8x8
luminance grid the caller already has (tests and crawlers can attach
``luminance_grid`` or raw ``pixels``). OCR falls back to on-post captions
and explicit ``ocr_text`` fields. Video keyframes are cover frames plus
any ``keyframes`` list supplied with the post.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.propagation_algorithm import (
    _extract_media_urls,
    _post_value,
    normalize_media_url,
    url_fingerprint,
)

HASH_SIZE = 8
PHASH_MATCH_DISTANCE = 10
_VIDEO_EXT = (".mp4", ".mov", ".webm", ".m3u8", ".flv")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
_AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
_QUOTED = re.compile(r"[「『\"“]([^」』\"”]{1,40})[」』\"”]")


def average_hash(grid: list[list[int]]) -> str:
    """64-bit average hash of an 8x8 (or larger, cropped) luminance grid."""
    rows = [row[:HASH_SIZE] for row in grid[:HASH_SIZE]]
    while len(rows) < HASH_SIZE:
        rows.append([0] * HASH_SIZE)
    rows = [row + [0] * (HASH_SIZE - len(row)) for row in rows]
    flat = [max(0, min(255, int(value))) for row in rows for value in row]
    mean = sum(flat) / len(flat)
    bits = 0
    for value in flat:
        bits = (bits << 1) | (1 if value >= mean else 0)
    return f"{bits:016x}"


def downsample_luminance(
    width: int, height: int, pixels: list[int], size: int = HASH_SIZE
) -> list[list[int]]:
    """Nearest-neighbour downsample of a row-major luminance buffer."""
    if width <= 0 or height <= 0 or not pixels:
        return [[0] * size for _ in range(size)]
    grid: list[list[int]] = []
    for row in range(size):
        source_y = min(height - 1, row * height // size)
        line: list[int] = []
        for col in range(size):
            source_x = min(width - 1, col * width // size)
            line.append(int(pixels[source_y * width + source_x]))
        grid.append(line)
    return grid


def pixel_phash(
    *,
    width: int | None = None,
    height: int | None = None,
    pixels: list[int] | None = None,
    grid: list[list[int]] | None = None,
) -> str | None:
    if grid:
        return average_hash(grid)
    if pixels is not None and width and height:
        return average_hash(downsample_luminance(width, height, pixels))
    return None


def hamming_distance(left: str, right: str) -> int:
    if not left or not right or len(left) != len(right):
        return 64
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def similar_phash(left: str, right: str, threshold: int = PHASH_MATCH_DISTANCE) -> bool:
    return bool(left) and bool(right) and hamming_distance(left, right) <= threshold


def infer_media_type(url: str) -> str:
    lowered = str(url or "").split("?", 1)[0].lower()
    if any(lowered.endswith(ext) for ext in _VIDEO_EXT) or "video" in lowered:
        return "video"
    if any(lowered.endswith(ext) for ext in _AUDIO_EXT):
        return "audio"
    if any(lowered.endswith(ext) for ext in _IMAGE_EXT) or "image" in lowered:
        return "image"
    return "image"


def extract_ocr_text(post: dict[str, Any], media: dict[str, Any] | None = None) -> str:
    if media:
        explicit = str(media.get("ocr_text") or "").strip()
        if explicit:
            return explicit
    content = str(post.get("content") or "")
    quoted = [match.group(1) for match in _QUOTED.finditer(content)]
    if quoted:
        return " ".join(quoted)
    runs = _CJK_RUN.findall(content)
    if runs:
        # Prefer the longest CJK run as a weak on-image caption.
        return max(runs, key=len)
    return ""


def extract_keyframes(post: dict[str, Any], media: dict[str, Any] | None = None) -> list[str]:
    frames: list[str] = []
    sources = [
        (media or {}).get("keyframe_urls"),
        post.get("keyframes"),
        post.get("keyframe_urls"),
        _post_value(post, "keyframes"),
    ]
    for source in sources:
        if isinstance(source, list):
            frames.extend(str(item) for item in source if item)
    cover = post.get("cover_url") or _post_value(post, "cover_url")
    if cover:
        frames.append(str(cover))
    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for url in frames:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def media_items_from_post(post: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize every media attachment on a post into a persistable item."""
    items: list[dict[str, Any]] = []
    urls = _extract_media_urls(post)
    explicit_video = post.get("video_url") or _post_value(post, "video_url")
    if explicit_video and str(explicit_video) not in urls:
        urls.append(str(explicit_video))
    seen: set[str] = set()
    for url in urls:
        normalized = normalize_media_url(url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        media_type = infer_media_type(url)
        grid = post.get("luminance_grid") if media_type == "image" else None
        phash = None
        if media_type == "image":
            phash = pixel_phash(
                grid=grid if isinstance(grid, list) else None,
                width=post.get("pixel_width"),
                height=post.get("pixel_height"),
                pixels=post.get("pixels") if isinstance(post.get("pixels"), list) else None,
            )
        item = {
            "url": url,
            "normalized_url": normalized,
            "fingerprint": url_fingerprint(url),
            "media_type": media_type,
            "phash": phash,
            "ocr_text": extract_ocr_text(post, {}),
            "keyframe_urls": extract_keyframes(post, {"url": url})
            if media_type == "video"
            else [],
        }
        items.append(item)
    return items


def phashes_from_post(post: dict[str, Any]) -> list[str]:
    hashes = [
        item["phash"]
        for item in media_items_from_post(post)
        if item.get("phash")
    ]
    explicit = post.get("phash")
    if explicit:
        hashes.append(str(explicit))
    return hashes


def keyframe_fingerprints(post: dict[str, Any]) -> set[str]:
    prints: set[str] = set()
    for item in media_items_from_post(post):
        for url in item.get("keyframe_urls") or []:
            prints.add(url_fingerprint(url))
    return prints


async def persist_media_from_posts(
    repository: Any,
    social: Any,
    case_id: str,
    posts: list[dict[str, Any]],
) -> dict[str, int]:
    """Write media_assets for collected posts. Best-effort, idempotent."""
    created = 0
    skipped = 0
    stored = await social.list_posts_by_case(case_id)
    by_native: dict[tuple[str, str], str] = {}
    for record in stored:
        by_native[(record.platform, record.native_id)] = record.id
        by_native[(record.platform, str(record.id))] = record.id
    for post in posts:
        platform = str(post.get("platform") or "")
        native_id = str(post.get("native_id") or post.get("id") or "")
        post_id = by_native.get((platform, native_id))
        for item in media_items_from_post(post):
            try:
                await repository.create_media_asset(
                    case_id=case_id,
                    post_id=post_id,
                    platform=platform,
                    media_type=item["media_type"],
                    url=item["url"],
                    normalized_url=item["normalized_url"],
                    file_sha256=item.get("fingerprint"),
                    phash=item.get("phash"),
                    ocr_text=item.get("ocr_text") or None,
                    keyframe_urls=item.get("keyframe_urls") or [],
                    metadata={"fingerprint": item.get("fingerprint")},
                )
                created += 1
            except Exception:
                skipped += 1
    return {"created": created, "skipped": skipped}
